# -*- coding: utf-8 -*-
r"""Quick preview generator: renders sample .docx files for one or more datasheets
so you can eyeball the output WITHOUT running the web app / login / peer-review.

Usage (from the project folder, with the venv):
    .\.venv\Scripts\python.exe preview_datasheets.py            # all: ESD RS_RI CRF PFMF
    .\.venv\Scripts\python.exe preview_datasheets.py ESD        # just one
    .\.venv\Scripts\python.exe preview_datasheets.py ESD CRF    # a few

Output: test_output\<CODE>_preview.docx  (opens the folder at the end on Windows)

The sample data fills the header fields, drops a correctly-sized + captioned image
into every image slot, fills the observation tables, and adds an observation legend,
so every recent change (image sizing, editable captions, ESD single legend) is visible.
This is a preview of the DOCUMENT output only; the interactive form features
(image editor, legend UI) are tested through the web app.
"""
import os, sys, glob

# run from the project folder regardless of where python is invoked
PROJECT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT)
sys.path.insert(0, PROJECT)

from PIL import Image, ImageDraw
from datasheet_gen import generic_generator as gg
from datasheet_gen import generic_service as gs
from datasheet_gen.registry import load_schema

OUT_DIR = os.path.join(PROJECT, "test_output")
IMG_DIR = os.path.join(OUT_DIR, "_images")
os.makedirs(IMG_DIR, exist_ok=True)

# every schema-driven datasheet + the bespoke CE form
GENERIC_CODES = sorted(os.path.basename(p)[:-5]
                       for p in glob.glob(os.path.join(PROJECT, "datasheet_gen", "schemas", "*.json")))
ALL_CODES = GENERIC_CODES + ["CE"]

EMC_LEGEND = [
    ("A", "Normal performance within the specification limits."),
    ("B", "Temporary degradation or loss of function which is self-recoverable."),
    ("C", "Temporary degradation or loss of function which requires operator intervention."),
    ("D", "Degradation or loss of function which is not recoverable."),
]


def make_sample_image(code, key, box_mm):
    """A placeholder image sized to the slot's aspect ratio (so exact-size sizing
    shows no distortion), labelled so it is obviously a sample."""
    bw, bh = box_mm
    w, h = max(200, int(bw * 6)), max(120, int(bh * 6))
    im = Image.new("RGB", (w, h), (245, 248, 252))
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, w - 3, h - 3], outline=(120, 150, 190), width=3)
    for gx in range(0, w, max(20, w // 12)):
        d.line([(gx, 0), (gx, h)], fill=(220, 228, 238))
    for gy in range(0, h, max(20, h // 8)):
        d.line([(0, gy), (w, gy)], fill=(220, 228, 238))
    d.line([(0, h), (w, 0)], fill=(200, 80, 80), width=2)
    d.text((12, 10), f"{code}  –  {key}", fill=(30, 60, 100))
    d.text((12, h - 22), f"sample image  {bw:g} x {bh:g} cm", fill=(80, 80, 80))
    p = os.path.join(IMG_DIR, f"{code}_{key}.png")
    im.save(p)
    return p


def sample_scalar(f):
    if f.get("default"):
        return f["default"]
    key, lbl = f.get("key", ""), f.get("label", "")
    opts = f.get("checkbox") or f.get("options")
    if opts:
        return opts[0]
    if "date" in key:
        return "2026-07-23"
    if key == "eut_configuration":
        return "Tabletop"
    return f"Sample {lbl or key}"


def build_form_data(code, schema):
    fd = {}
    # 1) scalar fields
    for f in gs.iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
        fd[f["key"]] = sample_scalar(f)

    # 2) images: a sample per slot, sized to the slot box (cm), + caption where allowed
    images, ikeys = {}, gs.image_keys(schema)
    for k in ikeys:
        box = gg._box(k, code)
        images[k] = make_sample_image(code, k, box)
        fd[k + "__wcm"] = round(box[0] / 10.0, 2)
        fd[k + "__hcm"] = round(box[1] / 10.0, 2)
    # captions for image items flagged caption:true (+ CE-style plot captions)
    for sec in schema["sections"]:
        for it in sec["items"]:
            imgs = []
            if it.get("type") == "fields":
                imgs = [x for x in it.get("fields", []) if x.get("input") == "image" and x.get("caption")]
            elif it.get("caption") and (it.get("type") == "image" or it.get("input") == "image"):
                imgs = [it]
            for x in imgs:
                fd[x["key"] + "_caption"] = x.get("label", "")

    # 3) observation tables, per layout. Track only the codes we actually put in
    #    the cells, so the legend below shows ONLY those (like the real form) and
    #    not the full A/B/C/D list.
    used = []

    def use(code):
        if code and code not in used:
            used.append(code)

    for sec in schema["sections"]:
        for it in sec["items"]:
            lay = it.get("layout")
            if lay == "esd_obs":
                codes = ["A", "B", "A", "C", "A", "B"]   # deliberately no 'D'
                for ri, row in enumerate(it.get("rows", [])):
                    if it.get("name_editable"):
                        fd[row["key"] + "_name"] = f"Test point {ri + 1}"
                    for n in range(1, 7):
                        c = codes[(ri + n) % len(codes)]
                        fd[f"{row['key']}_c{n}"] = c
                        use(c)
            elif lay == "rs_obs":
                for bi, b in enumerate(it.get("bands", [])):
                    fd[b["key"] + "_col_1"] = "10" if bi == 0 else "3"
                    fd[b["key"] + "_col_2"] = "3"
                    for n in range(3, 11):
                        fd[f"{b['key']}_col_{n}"] = "A"
                        use("A")
            elif lay == "pfmf_obs":
                for b in it.get("bands", []):
                    for n in range(1, 8):
                        fd[f"{b['key']}_col_{n}"] = "A"
                        use("A")
            elif it.get("type") == "table" and it.get("key") == "test_observation_rows":
                for c in it.get("columns", []):
                    if c.get("input") == "select":
                        val = (c.get("options") or ["A"])[0]
                        fd[f"{it['key']}__{c['key']}[]"] = [val for _ in range(3)]
                        if c.get("legend"):
                            use(val)
                    else:
                        fd[f"{it['key']}__{c['key']}[]"] = [f"Sample {c.get('label', c['key'])}" for _ in range(3)]

    # legend: describe ONLY the codes used above (generic + PFMF bespoke channels)
    desc_by_code = dict(EMC_LEGEND)
    leg_codes = [c for c in used if c in desc_by_code] or ["A"]
    for prefix in ("obs_legend", "pfmf_obs_legend"):
        fd[prefix + "_code[]"] = leg_codes
        fd[prefix + "_desc[]"] = [desc_by_code[c] for c in leg_codes]
    return fd, images, ikeys


def render_one(code):
    schema = load_schema(code)
    fd, images, ikeys = build_form_data(code, schema)
    ctx = gs.build_context(schema, fd)
    out = os.path.join(OUT_DIR, f"{code}_preview.docx")
    gg.render(code, ctx, ikeys, images, out)
    print(f"  [OK] {code:8s} -> {out}")
    return out


def render_ce():
    """The CE (IEC-FRM-504) datasheet uses a bespoke engine, not the schema/JSON
    one, so it is built separately: scalar fields + fixed images + one measurement
    record (with Line/Neutral plots)."""
    from datasheet_gen import service as ce_service
    from datasheet_gen import generator as ce_gen

    overrides = {
        "classification_group": "Group 1", "classification_class": "Class A",
        "eut_configuration": "Tabletop", "overall_result": "PASS", "result_class": "A",
        "test_date": "2026-07-23", "tested_by_date": "2026-07-23",
        "test_procedure": ("LISN (Voltage Method):\nThe test procedure was in accordance with "
                           "the standard. The EUT was configured and powered as specified and the "
                           "conducted emissions on the mains port were measured against the limits."),
    }
    fd = {f: overrides.get(f, f"Sample {f.replace('_', ' ')}") for f in ce_service.SCALAR_FIELDS}
    fd["photo_caption"] = "Photo 1: CE test setup_Power Port"

    images = {}
    for var, box in ce_gen._IMAGE_BOXES.items():
        images[var] = make_sample_image("CE", var, box)
        fd[var + "__wcm"], fd[var + "__hcm"] = round(box[0] / 10, 2), round(box[1] / 10, 2)

    # one measurement record (Test 1) with a 2-row Line/Neutral table + two plots
    fd["meas_index[]"] = ["1"]
    fd["meas_label_1"] = "Test 1: CE - Power Port"
    row_vals = {"qp_freq": ["0.15", "0.50"], "qp": ["45", "40"], "qp_limit": ["66", "56"],
                "qp_margin": ["21", "16"], "avg_freq": ["0.15", "0.50"], "avg": ["35", "30"],
                "avg_limit": ["56", "46"], "avg_margin": ["21", "16"]}
    for grp in ("line", "neutral"):
        for c in ce_service._MEAS_NAMES:
            fd[f"{grp}1_{c}[]"] = row_vals[c]
    for side in ("line", "neutral"):
        key = f"plot_{side}_1"
        images[key] = make_sample_image("CE", key, ce_gen._PLOT_BOX)
        fd[key + "__wcm"], fd[key + "__hcm"] = round(ce_gen._PLOT_BOX[0] / 10, 2), round(ce_gen._PLOT_BOX[1] / 10, 2)

    ctx = ce_service.build_ce_context(fd)
    out = os.path.join(OUT_DIR, "CE_preview.docx")
    ce_gen.render_ce_datasheet(ctx, out, images=images)
    print(f"  [OK] {'CE':8s} -> {out}")
    return out


if __name__ == "__main__":
    codes = [c.upper() for c in sys.argv[1:]] or ALL_CODES
    print(f"Rendering previews into: {OUT_DIR}\n")
    for c in codes:
        try:
            render_ce() if c == "CE" else render_one(c)
        except Exception as e:
            import traceback
            print(f"  [FAIL] {c}: {e}")
            traceback.print_exc()
    print("\nDone. Open the .docx files in Word to review.")
    try:
        os.startfile(OUT_DIR)  # Windows: pop the folder open
    except Exception:
        pass
