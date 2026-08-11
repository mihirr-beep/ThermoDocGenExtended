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

#: The standard headings of a CE measurement table, in _MEAS_NAMES order. The engineer can
#: rename these and append more columns on the form; these are only the defaults.
CE_MEAS_DEFAULT_HEADERS = ["Frequency (MHz)", "Q-peak (dBµV)", "Limit (dBµV)", "Margin (dB)",
                           "Frequency (MHz)", "Average (dBµV)", "Limit (dBµV)", "Margin (dB)"]
#: Hard cap so a runaway column count can't produce an unprintable table.
_CE_MEAS_MAX_COLS = 12


def _ce_meas_headers(form_data, tid):
    """Column headings posted for one measurement table (meas_<tid>__h<j>), in order.

    Returns [] when the form posted none - meaning a draft saved before columns became
    editable - so the caller falls back to the standard headings."""
    out = []
    for j in range(_CE_MEAS_MAX_COLS):
        key = "meas_%s__h%d" % (tid, j)
        if key not in (form_data or {}):
            break
        out.append(_s((form_data or {}).get(key)) or "Column %d" % (j + 1))
    return out


def _ce_meas_rows(form_data, tid, ncols):
    """Rows posted for one measurement table as meas_<tid>__c<j>[], one list per column.

    Each row carries 'cells' (what the template's column loop iterates) plus the legacy
    _MEAS_NAMES keys for its first 8 values, so anything still reading r.qp_freq keeps
    working. Blank rows are dropped."""
    cols = ["c%d" % j for j in range(ncols)]
    arrs = {c: _list(form_data, "meas_%s__%s[]" % (tid, c)) for c in cols}
    n = max((len(a) for a in arrs.values()), default=0)
    rows = []
    for r in range(n):
        cells = [_s(arrs[c][r]) if r < len(arrs[c]) else "" for c in cols]
        if not any(cells):
            continue
        row = {"cells": cells}
        for j, name in enumerate(_MEAS_NAMES):
            row[name] = cells[j] if j < len(cells) else ""
        rows.append(row)
    return rows


def _ce_meas_table(form_data, tid, legacy_rows):
    """(headers, rows) for one CE measurement table.

    Prefers the dynamic-column fields; falls back to the fixed 8-column fields a draft
    saved before this feature would still carry, so no measurement data is ever lost on
    reload. A table always renders at least one (blank) row so the grid is visible."""
    headers = _ce_meas_headers(form_data, tid) or list(CE_MEAS_DEFAULT_HEADERS)
    rows = _ce_meas_rows(form_data, tid, len(headers))
    if not rows and legacy_rows:
        # legacy shape: dicts keyed by _MEAS_NAMES -> add 'cells' in that same order
        rows = []
        for lr in legacy_rows:
            row = dict(lr)
            row["cells"] = [_s(lr.get(n)) for n in _MEAS_NAMES]
            rows.append(row)
        headers = list(CE_MEAS_DEFAULT_HEADERS)
    if not rows:
        rows = [{"cells": ["" for _ in headers],
                 **{n: "" for n in _MEAS_NAMES}}]
    return headers, rows

#: CE's standard conducted-emission band, used when the Test Request says
#: 'As per the standard' (or carries no frequency range at all).
CE_STANDARD_RANGE = "150 kHz - 30 MHz"
#: How that band is spelled inside figure/table captions.
CE_CAPTION_BAND = "0.15MHz - 30MHz"
#: Every standard spelling a caption might already carry, so a custom range can replace it.
CE_CAPTION_BANDS = ("0.15MHz - 30MHz", "0.15MHz-30MHz", "150 kHz - 30 MHz", "150kHz-30MHz")


def ce_request_range(freq):
    """The Frequency Range to show on the datasheet, given the CE test detail's value.

    A real custom specification is used verbatim; 'As per the standard' and blanks fall
    back to CE's standard band. Mirrors what RE does with its own detail value."""
    v = _s(freq)
    # 'As per the standard' is a marker, not a range - the datasheet must not print it.
    return v if (v and "standard" not in v.lower()) else CE_STANDARD_RANGE


def ce_custom_range(freq):
    """The custom Frequency Range text, or '' when the standard band is in force.

    Drives the caption naming: when this is non-empty every '0.15MHz - 30MHz' in a
    figure/table caption is replaced by it, exactly as RE does for its bands."""
    v = _s(freq)
    if not v:
        return ""
    flat = v.lower().replace(" ", "")
    standard = {CE_STANDARD_RANGE.lower().replace(" ", ""),
                CE_CAPTION_BAND.lower().replace(" ", "")}
    return "" if (flat in standard or "standard" in v.lower()) else v


def ce_caption_band(form_data):
    """The band label to build figure captions with: the custom range when one was
    entered on the Test Request, otherwise CE's standard caption spelling."""
    return ce_custom_range((form_data or {}).get("frequency_range")) or CE_CAPTION_BAND


#: A measurement caption WE generated, in any spelling this datasheet has ever used. Such a
#: caption is regenerated rather than honoured, so a draft that autosaved an older wording
#: (e.g. '..._Line_Quasi-peak_...' from when the detectors had separate plots) does not
#: freeze it into the document. A caption the engineer actually typed does not match, so it
#: is always kept. The form's ceIsAutoCaption() applies the same rule.
_CE_AUTO_CAPTION = re.compile(
    r"^(?:Figure|Table)\s+\d+\s*:\s*CE[ _]", re.I)


def ce_is_auto_caption(text):
    """True when `text` is one of our generated measurement captions."""
    return bool(_CE_AUTO_CAPTION.match(_s(text).strip()))


#: The plot groups a Test carries, in document order. Each is ONE image followed by its own
#: full 8-column table, exactly as the reference datasheet lays it out: one plot per
#: conductor carrying both detector traces, and one grid holding both detectors' data.
#:     (variable stem, form field / table id stem, side, detector)
_CE_PLOT_GROUPS = (
    ("line",    "line",    "Line",    "Quasi-peak & Average"),
    ("neutral", "neutral", "Neutral", "Quasi-peak & Average"),
)


def _measurement_records(form_data):
    """One record per Test, laid out as the reference datasheet does: the Test label, then
    for EACH plot an image, its "Figure N:" caption, its own data table and that table's
    "Table N:" caption.

    The form sends a hidden meas_index[] (active record indices, in order). Each record i
    carries two such groups - one plot for the Line conductor and one for Neutral, each
    showing both the Quasi-peak and Average traces:

        plot_line_i     (Line, Quasi-peak & Average)
        plot_neutral_i  (Neutral, Quasi-peak & Average)

    with a table per group under meas_<stem><i>__h<j> / __c<j>[], holding the full 8
    columns (Q-peak half + Average half).

    Figure/Table numbers are provisional here; the generator renumbers both in document
    order once the groups that print nothing have been dropped."""
    band = ce_caption_band(form_data)   # custom Frequency Range, else '0.15MHz - 30MHz'

    def keys(grp, i):
        return ["%s%s_%s[]" % (grp, i, c) for c in _MEAS_NAMES]

    def cap(kind, name, i, n, side, detector):
        # A caption the engineer TYPED replaces the default. One we generated ourselves is
        # rebuilt instead, so a stale wording saved in a draft cannot outlive it.
        if kind == "Figure":
            default = "Figure %d: CE plot_%s_%s_%s" % (n, side, detector, band)
            field = "%s_caption_%s" % (name, i)
        else:
            default = "Table %d: CE_%s_%s_%s" % (n, side, detector, band)
            field = "meas_table_caption_%s%s" % (name, i)
        saved = _s(form_data.get(field))
        return default if (not saved or ce_is_auto_caption(saved)) else saved

    def build(i, fig, tbl):
        """The image+table groups for record `i`, numbered from fig/tbl."""
        out = {}
        for n, (stem, tid, side, detector) in enumerate(_CE_PLOT_GROUPS):
            # a draft saved while the table was a fixed 8-column grid posted line{i}_qp_freq[]
            # and friends; those feed the same grid so no measurement data is lost on reload
            legacy = _rows(form_data, keys(tid, i), _MEAS_NAMES)
            headers, rows = _ce_meas_table(form_data, tid + i, legacy)
            out["plot_%s_key" % stem] = "plot_%s_%s" % (stem, i) if i else "plot_%s" % stem
            out["%s_headers" % stem] = headers
            out["%s_rows" % stem] = rows
            out["%s_caption" % stem] = cap("Figure", "plot_" + stem, i, fig + n, side, detector)
            out["%s_table_caption" % stem] = cap("Table", tid, i, tbl + n, side, detector)
            # a group prints only when it has an image or some data of its own; the two
            # quasi-peak groups always print, matching the reference layout
            out["has_%s_data" % stem] = any(any(r.get("cells") or []) for r in rows)
        return out

    order = _list(form_data, "meas_index[]")
    records = []
    n_groups = len(_CE_PLOT_GROUPS)
    fig = tbl = 1
    if order:
        for i in order:
            i = _s(i).strip()
            if not i:
                continue
            rec = {"label": _s(form_data.get("meas_label_" + i)),
                   # further plots the engineer added to this Test, each with its own title
                   "extra_images": _ce_extra_images(form_data, f"plot_extra_{i}_")}
            rec.update(build(i, fig, tbl))
            records.append(rec)
            fig += n_groups
            tbl += n_groups
    else:
        # legacy single-record fallback (un-indexed line_*/neutral_* fields)
        probe = build("", 1, 1)
        if any(probe["has_%s_data" % g[0]] for g in _CE_PLOT_GROUPS):
            rec = {"label": "", "extra_images": []}
            rec.update(probe)
            records.append(rec)
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
    # Further Test Setup pictures, each with its own title (the generator drops the
    # slots with no upload and numbers the captions in document order).
    ctx["ce_extra_photos"] = _ce_extra_images(form_data, "ce_extra_photo_")

    # Ambient Temperature / Relative Humidity / Test Date / Tested by can each be split
    # into 1-3 sections (one per test day), exactly as on RE. Reuses RE's helper so the
    # two datasheets can't drift apart; the generator does the cell splitting.
    from .generic_service import _re_row_splits
    ctx["ce_row_splits"] = _re_row_splits(form_data)

    # A custom Frequency Range from the Test Request renames the captions that carry the
    # standard band in fixed template text (the two 'Table N: CE_..._0.15MHz - 30MHz'
    # headings). Measurement figure captions are already built with it above.
    ctx["ce_custom_range"] = ce_custom_range(form_data.get("frequency_range"))

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
    # Per-image size overrides (Word Picture Format -> Size, in cm). The form posts
    # hidden <imgvar>__wcm / <imgvar>__hcm inputs when the user sets an exact size in
    # the image editor. Convert cm -> mm and expose as {imgvar: (w_mm, h_mm)} so the
    # generator fits each image to the user's exact box instead of the default.
    _img_boxes = {}
    for _k in list(form_data.keys()):
        if not _k.endswith("__wcm"):
            continue
        _base = _k[:-5]
        _w, _h = _s(form_data.get(_base + "__wcm")), _s(form_data.get(_base + "__hcm"))
        if _w and _h:
            try:
                _img_boxes[_base] = (float(_w) * 10.0, float(_h) * 10.0)
            except ValueError:
                pass
    ctx["_img_boxes"] = _img_boxes
    _ce_normalize_dates(ctx)
    return ctx


#: CE's date-bearing context entries. It predates the schema-driven datasheets and uses its
#: own key names, so the generic normalizer's schema pass has nothing to read here - but the
#: FORMATTER is shared, so CE and the other ten cannot drift apart.
_CE_DATE_SCALARS = ("test_date", "tested_by_date", "date")
_CE_DATE_ROWS = (("modifications", "date"), ("equipment", "cal_due"))


def _ce_normalize_dates(ctx):
    """Print every CE date as DD/MM/YYYY, in place.

    The form posts ISO from its <input type=date> fields and equipment_candidates() returns
    calibration_due_date.isoformat(), so both were reaching the document as 2026-07-23.
    'NA' and any other unparseable text pass through untouched, and the pass is idempotent.
    """
    from .generic_service import _fmt_ddmmyyyy
    for k in _CE_DATE_SCALARS:
        if isinstance(ctx.get(k), str) and ctx[k]:
            ctx[k] = _fmt_ddmmyyyy(ctx[k])
    for key, cell in _CE_DATE_ROWS:
        for row in ctx.get(key) or []:
            if isinstance(row, dict) and isinstance(row.get(cell), str) and row[cell]:
                row[cell] = _fmt_ddmmyyyy(row[cell])
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
            # Equipment with no calibration due date prints 'NA', not an empty box (as RE)
            "serial": _s(eq.serial_no), "cal_due": cd.isoformat() if cd else "NA",
        })
    return rows


def _ce_extra_images(form_data, prefix):
    """Extra pictures added on the CE form, each with its own title.

    Posted as <prefix><n> (file) and <prefix>caption_<n> (title). The slot survives here
    even with a blank title; the generator drops the ones with no upload. Returns
    [{'n', 'key', 'caption'}] in slot order."""
    cap_prefix = f"{prefix}caption_"
    idxs = set()
    for k in (form_data or {}):
        if k.startswith(cap_prefix):
            suffix = k[len(cap_prefix):]
            if suffix.isdigit():
                idxs.add(int(suffix))
    return [{"n": i, "key": f"{prefix}{i}",
             "caption": _s((form_data or {}).get(f"{cap_prefix}{i}"))}
            for i in sorted(idxs)]


def _ce_functional_mode_names(request_obj):
    """'Mode A', 'Mode A, Mode B', ... from the Test Request's functional modes.
    Imported lazily because generic_service imports from this module."""
    from .generic_service import _re_functional_mode_names
    return _re_functional_mode_names(request_obj)


def _first_config_line(text):
    """Test Mode shows only the FIRST line/item of the (often long, numbered) EUT
    test-configuration text, with any leading '1.'/'1)' numbering + tab stripped."""
    t = _s(text).strip()
    if not t:
        return ""
    first = t.splitlines()[0].strip()
    first = re.sub(r"^\s*\d+[.)]\s*", "", first)
    return first.strip()


def _functional_modes_text(request_obj):
    """Authoritative Test Mode source: the request's Functional Modes
    (iec_emc_request_functional_modes.mode_value), ordered by sort_order — NOT the
    free-text EUT Test Configuration. One mode is returned as-is; multiple modes are
    numbered on their own lines. Returns '' when the request has no functional modes
    (callers fall back to the old test_configuration text so the field is never blank)."""
    rows = getattr(request_obj, "functional_modes", None) or []
    try:
        rows = sorted(rows, key=lambda m: getattr(m, "sort_order", 0) or 0)
    except Exception:
        pass
    modes = []
    for m in rows:
        v = _s(getattr(m, "mode_value", ""))
        if v:
            modes.append(v)
    if not modes:
        return ""
    if len(modes) == 1:
        return modes[0]
    return "\n".join("%d. %s" % (i + 1, v) for i, v in enumerate(modes))


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
        # Test Mode prints the mode NAMES ('Mode A, Mode B') derived from the Test
        # Request's functional modes, not the descriptions typed for each one. Reuses
        # RE's helper so the two datasheets can't drift apart; falls back to the old
        # free text when the request has no functional modes at all.
        "test_mode": (_ce_functional_mode_names(request_obj)
                      or _first_config_line(_functional_modes_text(request_obj)
                                            or _ra(request_obj, "test_configuration", "operation_modes"))),
        "eut_voltage_frequency": _fmt_supply(getattr(request_obj, "supply_vf_values", [])) if request_obj else "",
        "tested_by": tested_by,
        "tested_by_name": tested_by,
        # fixed constants now come from the admin-editable datasheet_fixed_values table
        "measurement_uncertainty": _cefv.get("measurement_uncertainty", "± 3.368 dB"),
        "basic_standard": basic_standard,
        "sop_reference": _cefv.get("sop_reference", "IEC-SOP-505"),
        "test_port": "Power Line",            # editable
        "coupling_method": "LISN",            # editable
        # Frequency Range comes from the CE test detail on the Test Request. A CUSTOM
        # specification ('From ... To ...', stored as e.g. '1 kHz to 6 GHz') is carried
        # through verbatim and names the whole datasheet; 'As per the standard' (or a blank
        # detail) falls back to CE's standard band. Without the _is_custom filter the
        # datasheet printed the literal words "As per the standard" as its frequency range.
        "frequency_range": ce_request_range(getattr(ce, "freq_range", "") if ce else ""),
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
