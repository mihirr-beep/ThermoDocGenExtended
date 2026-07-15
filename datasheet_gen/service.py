"""Build the docxtpl context for the CE datasheet from the submitted form, and
collect auto-fill values from the request to pre-populate the form.

Field names below are the canonical names used by the CE form page, the context,
and the template placeholders (all aligned).
"""
import re

STANDARD_PROCEDURE = (
    "The EUT was placed on a wooden table / insulation support at 0.8 / 0.1 m height. "
    "The EUT was tested at the conducted emissions test site with a horizontal ground "
    "reference plane and a vertical ground reference plane bonded together. The power "
    "supply to the EUT and auxiliary equipment was fed through LISN.\n\n"
    "LISN (Voltage Method):\nThe conducted emission was measured through the 50 Ω RF "
    "port of the LISN using an EMI receiver carried out in FFT mode, and conducted "
    "emission from the EUT coupled through the Power (mains) port was plotted in the "
    "graph. The dominant peaks at various frequencies, closer to and/or above the limit "
    "line, were identified and listed. Quasi-peak and Average measured frequencies are "
    "compared with the limit specified in the standard."
)

SCALAR_FIELDS = [
    "job_number", "eut_name", "eut_model", "eut_serial",
    "measurement_uncertainty", "sop_reference",
    "product_standard", "basic_standard", "classification_group", "classification_class",
    "test_port", "coupling_method", "frequency_range", "resolution_bandwidth",
    "step_size", "detector", "measurement_time", "test_mode", "eut_modification_state",
    "eut_configuration", "eut_voltage_frequency", "ambient_temperature",
    "relative_humidity", "test_date", "tested_by", "deviation", "test_procedure",
    "limit_qp_015_050", "limit_avg_015_050", "limit_qp_050_5", "limit_avg_050_5",
    "limit_qp_5_30", "limit_avg_5_30",
    "software_used", "software_version", "result_class", "overall_result",
    "tested_by_name", "tested_by_date",
]


def _s(value):
    if value is None:
        return ""
    if isinstance(value, list):
        for item in value:
            if item not in (None, ""):
                return str(item).strip()
        return ""
    return str(value).strip()


# --- Checkbox rendering -------------------------------------------------------
# The source datasheets show classification etc. as ticked checkboxes. docxtpl
# only renders text, so we turn the selected value into a marked-up option line
# using ballot-box glyphs, e.g. "Class A" -> "☒ Class A   ☐ Class B".
CHECK_ON = "☒"   # ☒ ballot box with X
CHECK_OFF = "☐"  # ☐ empty ballot box


def as_checkbox_line(value, options):
    """Return 'a<gap>b<gap>...' where the option matching `value` is ticked.

    Matching is case-insensitive and tolerant of "A" vs "Class A" style values.
    If nothing matches (blank/unknown), every box is left empty.
    """
    val = _s(value).lower()
    parts = []
    for opt in options:
        o = str(opt).strip()
        ol = o.lower()
        hit = bool(val) and (val == ol or val in ol or ol in val
                             or val == ol.split()[-1] or ol.split()[-1] == val)
        parts.append(("{} {}".format(CHECK_ON if hit else CHECK_OFF, o)))
    return "   ".join(parts)


def _ra(obj, *names):
    """First non-empty attribute value from obj across the given names."""
    for n in names:
        v = getattr(obj, n, None) if obj is not None else None
        if v not in (None, ""):
            return _s(v)
    return ""


def _eut_config(request_obj):
    """Normalise the request's form-factor to the datasheet's Tabletop/Floor wording."""
    pt = _ra(request_obj, "product_type").lower()
    if pt.startswith("floor"):
        return "Floor standing"
    if pt.startswith("table") or "table" in pt:
        return "Tabletop"
    return _ra(request_obj, "product_type", "type_others")


def _list(form_data, key):
    value = form_data.get(key)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _rows(form_data, keys, names):
    cols = [_list(form_data, k) for k in keys]
    length = max((len(c) for c in cols), default=0)
    out = []
    for i in range(length):
        row = {name: _s(col[i]) if i < len(col) else "" for col, name in zip(cols, names)}
        if any(row.values()):
            out.append(row)
    return out


_MEAS_NAMES = ["qp_freq", "qp", "qp_limit", "qp_margin", "avg_freq", "avg", "avg_limit", "avg_margin"]


def _measurement_records(form_data):
    """One record per Test: label + line_rows + neutral_rows + the two plot image keys.
    The form sends a hidden meas_index[] (active record indices, in order); each record i
    uses meas_label_i, plot_line_i/plot_neutral_i and line{i}_*[] / neutral{i}_*[] fields."""
    def keys(grp, i):
        return ["%s%s_%s[]" % (grp, i, c) for c in _MEAS_NAMES]
    def cap(name, i, n, side):
        # user-entered caption REPLACES the default; blank -> the auto "Figure N: ..." caption
        default = "Figure %d: CE plot_%s_Quasi-peak & Average_0.15MHz - 30MHz" % (n, side)
        return _s(form_data.get("%s_caption_%s" % (name, i))) or default
    order = _list(form_data, "meas_index[]")
    records = []
    fig = 1
    if order:
        for i in order:
            i = _s(i).strip()
            if not i:
                continue
            records.append({
                "label": _s(form_data.get("meas_label_" + i)),
                "line_rows": _rows(form_data, keys("line", i), _MEAS_NAMES),
                "neutral_rows": _rows(form_data, keys("neutral", i), _MEAS_NAMES),
                "plot_line_key": "plot_line_" + i,
                "plot_neutral_key": "plot_neutral_" + i,
                "line_caption": cap("plot_line", i, fig, "Line"),
                "neutral_caption": cap("plot_neutral", i, fig + 1, "Neutral"),
            })
            fig += 2
    else:
        # legacy single-record fallback (un-indexed line_*/neutral_* fields)
        line = _rows(form_data, keys("line", ""), _MEAS_NAMES)
        neutral = _rows(form_data, keys("neutral", ""), _MEAS_NAMES)
        if line or neutral:
            records.append({"label": "", "line_rows": line, "neutral_rows": neutral,
                            "plot_line_key": "plot_line", "plot_neutral_key": "plot_neutral",
                            "line_caption": cap("plot_line", "", 1, "Line"),
                            "neutral_caption": cap("plot_neutral", "", 2, "Neutral")})
    return records


def build_ce_context(form_data):
    ctx = {f: _s(form_data.get(f)) for f in SCALAR_FIELDS}

    ctx["modifications"] = _rows(
        form_data,
        ["mod_state[]", "mod_description[]", "mod_fitted_by[]", "mod_date[]"],
        ["state", "description", "fitted_by", "date"],
    )
    ctx["equipment"] = _rows(
        form_data,
        ["eq_name[]", "eq_make[]", "eq_model[]", "eq_serial[]", "eq_cal_due[]"],
        ["name", "make", "model", "serial", "cal_due"],
    )
    # 8-column measurement layout, repeated per Test record (label + Line + Neutral).
    ctx["measurement_records"] = _measurement_records(form_data)
    # Setup photo caption: user text replaces the default (blank -> default).
    ctx["photo_caption"] = _s(form_data.get("photo_caption")) or "Photo 1: CE test setup_Power Port"

    # Render classification selections as human-ticked checkboxes in the document
    # (the template uses {{r ... }} placeholders for these two fields).
    from .layout import human_checkbox
    ctx["classification_group"] = human_checkbox(ctx.get("classification_group"), ["Group 1", "Group 2"])
    ctx["classification_class"] = human_checkbox(ctx.get("classification_class"), ["Class A", "Class B"])
    # EUT Configuration as ticked checkboxes, one option per cell (Tabletop | Floor
    # standing) so both options stay visible with the selected one ticked, matching
    # the source form (template uses {{r eut_config_tabletop }} / {{r eut_config_floor }}).
    ctx["eut_config_tabletop"] = human_checkbox(ctx.get("eut_configuration"), ["Tabletop"])
    ctx["eut_config_floor"] = human_checkbox(ctx.get("eut_configuration"), ["Floor standing"])
    # Result line: bold the class label and show PASS / FAIL as ticked checkboxes so
    # the outcome is easy to see (template: {{r result_class_label }} / {{r result_checkbox }}).
    from docxtpl import RichText
    _rc = (ctx.get("result_class") or "").strip()
    ctx["result_class_label"] = RichText(("Class " + _rc) if _rc else "Class", bold=True)
    ctx["result_checkbox"] = human_checkbox(ctx.get("overall_result"), ["PASS", "FAIL"])
    # Test procedure as RichText so "LISN (Voltage Method):" renders bold, on its
    # own line (template uses {{r test_procedure }}).
    ctx["test_procedure"] = procedure_richtext(ctx.get("test_procedure", ""))
    return ctx


# --- Pre-fill (auto values + sensible defaults) ------------------------------

def _join(rows, attr):
    out = [str(getattr(r, attr, "")).strip() for r in (rows or []) if getattr(r, attr, None)]
    return "; ".join(out)


def _fmt_supply(rows):
    """Format supply voltage/frequency rows (stored as JSON) into '230 V, 50 Hz'."""
    import json
    out = []
    for r in rows or []:
        raw = getattr(r, "value_text", "") or ""
        try:
            d = json.loads(raw)
            parts = []
            if d.get("voltage"):
                parts.append(str(d["voltage"]).strip() + " V")
            if d.get("frequency"):
                parts.append(str(d["frequency"]).strip() + " Hz")
            out.append(", ".join(parts) if parts else str(raw).strip())
        except Exception:
            out.append(str(raw).strip())
    return "; ".join([o for o in out if o])


def _ce_detail(request_obj):
    for test in getattr(request_obj, "tests", []) or []:
        if str(getattr(test, "test_code", "")).upper() == "CE":
            return getattr(test, "ce_detail", None)
    return None


PROCEDURE_TEMPLATE = (
    "The test procedure was in accordance with {basic_standard}.\n\n"
    "The EUT was placed on {surface} at {height} height. "
    "The EUT was tested at the conducted emissions test site with a horizontal ground "
    "reference plane and a vertical ground reference plane bonded together. The power "
    "supply to the EUT and auxiliary equipment was fed through LISN.\n\n"
    "LISN (Voltage Method):\nThe conducted emission was measured through the 50 Ω RF "
    "port of the LISN using an EMI receiver carried out in FFT mode, and conducted "
    "emission from the EUT coupled through the Power (mains) port was plotted in the "
    "graph. The dominant peaks at various frequencies, closer to and/or above the limit "
    "line, were identified and listed. Quasi-peak and Average measured frequencies are "
    "compared with the limit specified in the standard."
)

# Basic Standard is DERIVED from the selected Product Standard (per the DS504 sheet).
# Matched on a punctuation/space-insensitive token so it tolerates "IEC 61326-1:2020",
# "IEC61326-1:2020", etc.
_BASIC_STANDARD_MAP = [
    ("en61326", "EN 55011:2016+A2:2021"),                 # EN 61326-1:2021
    ("iec61326", "CISPR 11:2015+A1:2016+A2:2019"),        # IEC 61326-1:2020
    ("ices", "CISPR 11:2015+A1:2016+A2:2019"),            # ICES-001 Issue 5
    ("part15", "ANSI C63.4:2024"),                        # 47 CFR Part 15 Subpart B:2024
    ("cfr", "ANSI C63.4:2024"),
    ("fcc", "ANSI C63.4:2024"),
    # Product standard that is ALREADY a basic/emission standard -> maps to itself
    ("en55011", "EN 55011:2016+A2:2021"),
    ("cispr11", "CISPR 11:2015+A1:2016+A2:2019"),
    ("c634", "ANSI C63.4:2024"),                          # ANSI C63.4
]


def basic_standard_for(product_standard):
    """Return the Basic Standard(s) for the given Product Standard(s). A CE request
    may cite several product standards (joined with ';'); each maps to its own basic
    (measurement) standard, so we map EACH and return the DISTINCT set, joined:
        IEC 61326-1:2020                          -> CISPR 11:2015+A1:2016+A2:2019
        EN 61326-1:2021                           -> EN 55011:2016+A2:2021
        ICES-001 Issue 5 (all clauses except 3.3) -> CISPR 11:2015+A1:2016+A2:2019
        47 CFR Part 15 Subpart B:2024 (Clause 15) -> ANSI C63.4:2024
    e.g. all four -> "CISPR 11:2015+A1:2016+A2:2019; EN 55011:2016+A2:2021; ANSI C63.4:2024".
    Unknown standards are skipped; returns '' if none match.
    """
    # Data now lives in the admin-editable basic_standard_map table (shared emission
    # mapping = test_code NULL). _BASIC_STANDARD_MAP above is the seed / fallback.
    from .fixed_store import basic_standard as _bs
    return _bs(product_standard, None)


def procedure_richtext(text):
    """Render the (read-only) test procedure as docxtpl RichText so the
    "LISN (Voltage Method):" line is BOLD and stands on its own like a header,
    while newlines become line breaks. Used via the {{r test_procedure }} tag."""
    from docxtpl import RichText
    marker = "LISN (Voltage Method):"
    rt = RichText()
    for i, line in enumerate(_s(text).split("\n")):
        if i:
            rt.add("\n")                       # preserve the original line break
        if line.strip() == marker:
            rt.add(marker, bold=True, font="Arial", size=22)   # Arial 11pt, bold header
        elif line:
            rt.add(line, font="Arial", size=22)                # Arial 11pt (match reference doc)
    return rt


def procedure_for_config(config, basic_standard=""):
    """CE test-procedure text. The opening line names the basic standard; the EUT
    placement follows the configuration:
        Tabletop       -> wooden table at 0.8m
        Floor standing -> insulation support at 0.1m
    Section 7 is read-only in the form, driven by Product Standard + EUT Configuration."""
    is_floor = _s(config).lower().startswith("floor")
    height = "0.1m" if is_floor else "0.8m"
    surface = "an insulation support" if is_floor else "a wooden table"
    return PROCEDURE_TEMPLATE.format(
        basic_standard=(_s(basic_standard) or "the applicable basic standard"),
        surface=surface,
        height=height,
    )


# Test-limit prefills by classification class (dBµV), per the DS504 sheet.
# Three frequency bands: 0.15-0.50, 0.50-5, 5-30 MHz.
_CE_CLASS_LIMITS = {
    "A": {"limit_qp_015_050": "79",    "limit_avg_015_050": "66",
          "limit_qp_050_5":   "73",    "limit_avg_050_5":   "60",
          "limit_qp_5_30":    "73",    "limit_avg_5_30":    "60"},
    "B": {"limit_qp_015_050": "66-56", "limit_avg_015_050": "56-46",
          "limit_qp_050_5":   "56",    "limit_avg_050_5":   "46",
          "limit_qp_5_30":    "60",    "limit_avg_5_30":    "50"},
}
_CE_LIMIT_KEYS = ("limit_qp_015_050", "limit_avg_015_050", "limit_qp_050_5",
                  "limit_avg_050_5", "limit_qp_5_30", "limit_avg_5_30")


def class_letter(class_value):
    """Map a Classification-Class value ('Class A' / 'A' / 'Class B' / 'B') to 'A'/'B'/''."""
    v = _s(class_value).upper()
    if "B" in v:
        return "B"
    if "A" in v:
        return "A"
    return ""


def ce_limits_for_class(class_value):
    """CE Test-Limit numbers for a class, from the admin-editable fixed-values
    table (CE.test_limits.by_class); _CE_CLASS_LIMITS is the seed/fallback."""
    letter = class_letter(class_value)
    from .fixed_store import get_fixed_values
    by_class = ((get_fixed_values("CE") or {}).get("test_limits", {}) or {}).get("by_class", {})
    limits = by_class.get(letter) or _CE_CLASS_LIMITS.get(letter, {})
    return {k: limits.get(k, "") for k in _CE_LIMIT_KEYS}


def collect_ce_equipment_rows():
    """Prefill CE 'Test Equipment Used' rows from the Equipment Master.

    There is no per-request FK to Equipment, so equipment is selected by the
    Equipment.test_name text column (comma-separated test codes, e.g. 'CE' or
    'CE,RE'). Returns {name, make, model, serial, cal_due} dicts matching the CE
    form's eq_name[]/eq_make[]/eq_model[]/eq_serial[]/eq_cal_due[] fields.
    """
    try:
        from models import db, Equipment
        candidates = Equipment.query.filter(
            Equipment.test_name.isnot(None),
            db.or_(Equipment.test_name.ilike("%CE%"),
                   Equipment.test_name.ilike("%conducted%")),
        ).order_by(Equipment.sl_no.asc(), Equipment.name.asc()).all()
    except Exception:
        return []
    rows = []
    for eq in candidates:
        tn = eq.test_name or ""
        tokens = [t.strip().upper() for t in tn.split(",")]
        if "CE" not in tokens and "conducted emission" not in tn.lower():
            continue  # avoid loose substring hits (e.g. "deviCE")
        cd = getattr(eq, "calibration_due_date", None)
        rows.append({
            "name": _s(eq.name), "make": _s(eq.make), "model": _s(eq.model_no),
            "serial": _s(eq.serial_no), "cal_due": cd.isoformat() if cd else "",
        })
    return rows


def _first_config_line(text):
    """Test Mode shows only the FIRST line/item of the (often long, numbered) EUT
    test-configuration text, with any leading '1.'/'1)' numbering + tab stripped."""
    t = _s(text).strip()
    if not t:
        return ""
    first = t.splitlines()[0].strip()
    first = re.sub(r"^\s*\d+[.)]\s*", "", first)
    return first.strip()


def collect_ce_prefill(request_obj, assignment=None):
    ce = _ce_detail(request_obj) if request_obj is not None else None
    # EUT model/serial come from the primary Product Identity columns (Model
    # Number/SKU, Serial Number). Fall back to the multi-valued child rows only
    # when the primary scalar is empty. (The old code read additional_models,
    # which is the OPTIONAL "Sample 2" model, so it was usually blank.)
    model = _ra(request_obj, "model_number") or _join(getattr(request_obj, "additional_models", []), "model_number")
    serial = _ra(request_obj, "serial_number") or _join(getattr(request_obj, "serial_numbers", []), "serial_number")
    tested_by = _s(getattr(assignment, "test_person_name", "")) if assignment else ""
    class_value = _ra(request_obj, "class_type") or (_s(getattr(ce, "ce_class", "")) if ce else "")
    config = _eut_config(request_obj)
    product_standard = _join(getattr(request_obj, "product_standards", []), "standard_value") if request_obj else ""
    basic_standard = basic_standard_for(product_standard)   # derived from product standard (DS504)
    from .fixed_store import get_fixed_values
    _cefv = get_fixed_values("CE")          # admin-editable constants (uncertainty/sop/software)
    _ce_software = (_cefv.get("software") or [{}])[0]
    data = {
        # auto from request
        "job_number": _ra(request_obj, "job_number", "tco_id"),
        "eut_name": _ra(request_obj, "product_name"),
        "eut_model": model,
        "eut_serial": serial,
        "product_standard": product_standard,
        # classification: overall Class/Group from the Test Request; per-test class as fallback
        "classification_class": class_value,
        "classification_group": _ra(request_obj, "product_group"),
        "eut_configuration": config,
        "test_mode": _first_config_line(_ra(request_obj, "test_configuration", "operation_modes")),
        "eut_voltage_frequency": _fmt_supply(getattr(request_obj, "supply_vf_values", [])) if request_obj else "",
        "tested_by": tested_by,
        "tested_by_name": tested_by,
        # fixed constants now come from the admin-editable datasheet_fixed_values table
        "measurement_uncertainty": _cefv.get("measurement_uncertainty", "± 3.368 dB"),
        "basic_standard": basic_standard,
        "sop_reference": _cefv.get("sop_reference", "IEC-SOP-505"),
        "test_port": "Power Line",            # editable
        "coupling_method": "LISN",            # editable
        "frequency_range": _s(getattr(ce, "freq_range", "")) if ce and getattr(ce, "freq_range", "") else "150 kHz - 30 MHz",
        "resolution_bandwidth": "9K",
        "step_size": "4K",
        "detector": "Quasi-peak",
        "measurement_time": "1",
        "deviation": "NA",
        # section 7: read-only, driven by EUT Configuration
        "test_procedure": procedure_for_config(config, basic_standard),
        # section 4: linked to the Modification Record (initial state = 0)
        "eut_modification_state": "0",
        # section 11: fixed software defaults (from the DB fixed-values table)
        "software_used": _ce_software.get("c0", "PMM Suite"),
        "software_version": _ce_software.get("c1", "2.54"),
        # section 12: Result Class follows Classification - Class
        "result_class": class_letter(class_value),
    }
    # section 6: test limits derived from classification class
    data.update(ce_limits_for_class(class_value))
    # section 10: equipment rows from the Equipment Master (CE-tagged)
    data["equipment"] = collect_ce_equipment_rows()
    return data
