"""Build the docxtpl context for the CE datasheet from the submitted form, and
collect auto-fill values from the request to pre-populate the form.

Field names below are the canonical names used by the CE form page, the context,
and the template placeholders (all aligned).
"""

STANDARD_PROCEDURE = (
    "The EUT was placed on a wooden table / insulation support at 0.8 / 0.1 m height. "
    "The EUT was tested at the conducted emissions test site with a horizontal ground "
    "reference plane and a vertical ground reference plane bonded together. The power "
    "supply to the EUT and auxiliary equipment was fed through LISN.\n\n"
    "LISN (Voltage Method): The conducted emission was measured through the 50 Ω RF "
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
    "limit_qp_015_050", "limit_avg_015_050", "limit_qp_050_30", "limit_avg_050_30",
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
    meas_names = ["freq", "qp", "qp_limit", "qp_margin", "avg", "avg_limit", "avg_margin"]
    ctx["line_rows"] = _rows(
        form_data,
        ["line_freq[]", "line_qp[]", "line_qp_limit[]", "line_qp_margin[]",
         "line_avg[]", "line_avg_limit[]", "line_avg_margin[]"],
        meas_names,
    )
    ctx["neutral_rows"] = _rows(
        form_data,
        ["neutral_freq[]", "neutral_qp[]", "neutral_qp_limit[]", "neutral_qp_margin[]",
         "neutral_avg[]", "neutral_avg_limit[]", "neutral_avg_margin[]"],
        meas_names,
    )

    # Render classification selections as human-ticked checkboxes in the document
    # (the template uses {{r ... }} placeholders for these two fields).
    from .layout import human_checkbox
    ctx["classification_group"] = human_checkbox(ctx.get("classification_group"), ["Group 1", "Group 2"])
    ctx["classification_class"] = human_checkbox(ctx.get("classification_class"), ["Class A", "Class B"])
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


def collect_ce_prefill(request_obj, assignment=None):
    ce = _ce_detail(request_obj) if request_obj is not None else None
    # EUT model/serial come from the primary Product Identity columns (Model
    # Number/SKU, Serial Number). Fall back to the multi-valued child rows only
    # when the primary scalar is empty. (The old code read additional_models,
    # which is the OPTIONAL "Sample 2" model, so it was usually blank.)
    model = _ra(request_obj, "model_number") or _join(getattr(request_obj, "additional_models", []), "model_number")
    serial = _ra(request_obj, "serial_number") or _join(getattr(request_obj, "serial_numbers", []), "serial_number")
    tested_by = _s(getattr(assignment, "test_person_name", "")) if assignment else ""
    data = {
        # auto from request
        "job_number": _ra(request_obj, "job_number", "tco_id"),
        "eut_name": _ra(request_obj, "product_name"),
        "eut_model": model,
        "eut_serial": serial,
        "product_standard": _join(getattr(request_obj, "product_standards", []), "standard_value") if request_obj else "",
        # classification: overall Class/Group from the Test Request; per-test class as fallback
        "classification_class": _ra(request_obj, "class_type") or (_s(getattr(ce, "ce_class", "")) if ce else ""),
        "classification_group": _ra(request_obj, "product_group"),
        "eut_configuration": _eut_config(request_obj),
        "test_mode": _ra(request_obj, "test_configuration", "operation_modes"),
        "eut_voltage_frequency": _fmt_supply(getattr(request_obj, "supply_vf_values", [])) if request_obj else "",
        "tested_by": tested_by,
        "tested_by_name": tested_by,
        # sensible defaults from the form/document
        "measurement_uncertainty": "± 3.368 dB",
        "basic_standard": "Sysmex",
        "sop_reference": "IEC-SOP-505",
        "test_port": "Power Line",
        "coupling_method": "LISN",
        "frequency_range": _s(getattr(ce, "freq_range", "")) if ce and getattr(ce, "freq_range", "") else "150 kHz - 30 MHz",
        "detector": "Quasi-peak & Average",
        "deviation": "NA",
        "test_procedure": STANDARD_PROCEDURE,
        "eut_modification_state": "0 - Initial state",
    }
    return data
