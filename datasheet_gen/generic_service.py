"""Schema-driven context building + prefill for the generic datasheet engine."""
import json
import re

from .service import _join, _fmt_supply, _ra, _eut_config, as_checkbox_line, _functional_modes_text  # reuse CE helpers


def _normalize_numbered(text):
    """Put each numbered point ('1.', '2.', ...) of a multi-point value on its own
    left-aligned line. Tabs become single spaces; decimals like '3.3' are left
    alone (only 'N.' followed by whitespace starts a new line)."""
    t = (text or "").replace("\t", " ").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ ]{2,}", " ", t)
    t = re.sub(r"\s*(\d{1,2})\.\s+", lambda m: "\n" + m.group(1) + ". ", t)
    return t.strip()

# registry code -> (EMCRequestTest.test_code, detail relationship attribute)
_DETAIL_ATTR = {
    "RE": ("RE", "re_detail"),
    "ESD": ("ESD", "esd_detail"),
    "HARMONIC": ("HARMONIC", "harmonic_detail"),
    "VOLTAGEFLICKER": ("FLICKER", "flicker_detail"),
    "RS_RI": ("RS", "rs_detail"),
    "EFT": ("EFT", "eft_detail"),
    "SURGE": ("SURGE", "surge_detail"),
    "CRF": ("CRF", "crf_detail"),
    "PFMF": ("POWER_FREQ", "power_freq_detail"),
    "VOLTAGEDIPS": ("VOLTAGE_DIPS", "voltage_dips_detail"),
}


def _test_detail(request_obj, schema_code):
    """The per-test detail row (EMCRequestTest{X}) for this schema's test, if any."""
    mapping = _DETAIL_ATTR.get((schema_code or "").upper())
    if not mapping or request_obj is None:
        return None
    test_code, attr = mapping
    for test in getattr(request_obj, "tests", []) or []:
        if str(getattr(test, "test_code", "")).upper() == test_code:
            return getattr(test, attr, None)
    return None


def _norm_class(value):
    """'A' / 'a' / 'Class A' -> 'Class A' (so it matches checkbox options)."""
    v = str(value or "").strip()
    if len(v) == 1 and v.upper() in "ABCD":
        return "Class " + v.upper()
    return v


def _is_custom(value):
    """A TR spec value is only worth prefilling when it's a real custom value,
    not the 'As per standard' marker (the printed option row covers that)."""
    v = str(value or "").strip()
    return bool(v) and "standard" not in v.lower()


def _s(value):
    if value is None:
        return ""
    if isinstance(value, list):
        for x in value:
            if x not in (None, ""):
                return str(x).strip()
        return ""
    return str(value).strip()


def _list(form_data, key):
    v = form_data.get(key)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def iter_scalar_fields(schema):
    """Yield every scalar field dict ({key,label,input}) in the schema, flattening
    'fields' groups. Image fields are included (callers filter as needed) so this is
    the single source of truth for "what fields does this schema have"."""
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it["type"] == "fields":
                for f in it.get("fields", []):
                    yield f
            elif it["type"] in ("field", "textarea"):
                yield it


def image_keys(schema):
    keys = []
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it["type"] == "fields":
                keys += [f["key"] for f in it.get("fields", []) if f.get("input") == "image"]
            elif it["type"] == "image" or (it["type"] == "field" and it.get("input") == "image"):
                keys.append(it["key"])
    return keys


# Voltage-Dips derived Test Level sets. MUST stay in sync with VDIPS_LEVELS in
# generic_form.html (the form preview); durations follow the reference document
# (25 / 250 for every frequency). Can move to the DB fixed-values table later.
VDIPS_LEVELS = {
    "Basic": {
        "dips": [
            {"pct": "0", "spec": "0.5 cycle", "dur": "0.5", "crit": "B"},
            {"pct": "0", "spec": "1 cycle", "dur": "1", "crit": "B"},
            {"pct": "70", "spec": "25/30 cycles", "dur": "25", "crit": "C"},
        ],
        "intr": [{"pct": "0", "spec": "250/300 cycles", "dur": "250", "crit": "C"}],
    },
    "Industrial": {
        "dips": [
            {"pct": "0", "spec": "1 cycle", "dur": "1", "crit": "B"},
            {"pct": "40", "spec": "10/12 cycles", "dur": "10", "crit": "C"},
            {"pct": "70", "spec": "25/30 cycles", "dur": "25", "crit": "C"},
        ],
        "intr": [{"pct": "0", "spec": "250/300 cycles", "dur": "250", "crit": "C"}],
    },
}


def _vdips_groups(form_data, kind):
    """Rebuild the per-combo observation groups the form posted (kind='dips'|'intr').
    Reads vdips_<kind>_combo_<ci> + vdips_<kind>_<ci>__{pct,dur,obs}[]."""
    groups = []
    ci = 0
    while form_data.get("vdips_%s_combo_%d" % (kind, ci)) is not None:
        pcts = _list(form_data, "vdips_%s_%d__pct[]" % (kind, ci))
        durs = _list(form_data, "vdips_%s_%d__dur[]" % (kind, ci))
        obs = _list(form_data, "vdips_%s_%d__obs[]" % (kind, ci))
        n = max(len(pcts), len(durs), len(obs), 0)
        rows = [{
            "pct": _s(pcts[i]) if i < len(pcts) else "",
            "dur": _s(durs[i]) if i < len(durs) else "",
            "obs": _s(obs[i]) if i < len(obs) else "",
        } for i in range(n)]
        groups.append({"combo": _s(form_data.get("vdips_%s_combo_%d" % (kind, ci))), "rows": rows})
        ci += 1
    return groups


def _vdips_build_context(form_data):
    """VOLTAGEDIPS docx context: ticked checkboxes, derived Test Level, per-combo
    observation groups, and the derived Required + user-chosen Met criteria."""
    from .layout import human_checkbox
    ctx = {}
    # checkbox cells (tick the selected option) -> {{r key }} placeholders
    ctx["immunity_test_requirement"] = human_checkbox(
        _s(form_data.get("immunity_test_requirement")), ["Basic", "Industrial", "Controlled", "Custom"])
    ctx["test_port"] = human_checkbox(_s(form_data.get("test_port")) or "Power Line", ["Power Line"])
    ctx["number_of_dips_interruptions"] = human_checkbox(
        _s(form_data.get("number_of_dips_interruptions")), ["3 times", "Custom"])
    ctx["time_between_dips_interruptions"] = human_checkbox(
        _s(form_data.get("time_between_dips_interruptions")), ["10 sec", "Custom"])
    ctx["phase_angle"] = human_checkbox(
        _s(form_data.get("phase_angle")), ["0° & 180°", "0° – 360° in 45° steps"])
    ctx["eut_configuration"] = human_checkbox(
        _s(form_data.get("eut_configuration")), ["Tabletop", "Floor standing"])

    # Derived Test Level columns (3 dips + 1 interruption); the 3rd dip is shown
    # twice in the doc's merged spec table, so the template repeats tl_d2_*.
    lv = VDIPS_LEVELS.get(_s(form_data.get("immunity_test_requirement"))) or {}
    d, it = lv.get("dips", []), lv.get("intr", [])
    def _pct(lst, i): return (lst[i]["pct"] + " %") if i < len(lst) else ""
    def _dur(lst, i): return lst[i]["spec"] if i < len(lst) else ""
    ctx.update({
        "tl_d0_pct": _pct(d, 0), "tl_d1_pct": _pct(d, 1), "tl_d2_pct": _pct(d, 2), "tl_i_pct": _pct(it, 0),
        "tl_d0_dur": _dur(d, 0), "tl_d1_dur": _dur(d, 1), "tl_d2_dur": _dur(d, 2), "tl_i_dur": _dur(it, 0),
    })

    # per-combo observation tables
    ctx["vdips_dips_groups"] = _vdips_groups(form_data, "dips")
    ctx["vdips_intr_groups"] = _vdips_groups(form_data, "intr")

    # RESULT: % + Required criteria are derived (posted as hidden); Met is user-chosen.
    ctx["res_pct"] = [(_s(x) + " %") for x in _list(form_data, "vdips_result_pct[]")]
    ctx["res_crit"] = [_s(x) for x in _list(form_data, "vdips_req_criteria[]")]
    ctx["res_met"] = [_s(x) for x in _list(form_data, "vdips_met_criteria[]")]
    return ctx


def _eft_obs(form_data, kind):
    """Rebuild an EFT observation table the form posted (kind='power'|'signal').
    Columns from eft_obs_<kind>_cols; rows from eft_obs_<kind>_row_<ri> +
    eft_obs_<kind>_<ri>__c<ci>. Returns {'cols', 'rows':[{label, cells}]} or None."""
    cols = [c for c in _s(form_data.get("eft_obs_%s_cols" % kind)).split(",") if c]
    if not cols:
        return None
    rows = []
    ri = 0
    while form_data.get("eft_obs_%s_row_%d" % (kind, ri)) is not None:
        cells = [_s(form_data.get("eft_obs_%s_%d__c%d" % (kind, ri, ci))) for ci in range(len(cols))]
        rows.append({"label": _s(form_data.get("eft_obs_%s_row_%d" % (kind, ri))), "cells": cells})
        ri += 1
    return {"cols": cols, "rows": rows}


def _eft_build_context(form_data):
    """EFT/BURST docx context: ticked checkboxes, cumulative test voltages, single
    PRF, and the dynamic observation tables (inserted post-render by the generator)."""
    from .layout import human_checkbox, cumulative_checkbox
    ctx = {}
    ctx["immunity_test_requirement"] = human_checkbox(
        _s(form_data.get("immunity_test_requirement")), ["Basic", "Industrial", "Controlled", "Custom"])
    tp = _s(form_data.get("test_port"))
    ctx["test_port_power"] = human_checkbox("Power Line" if tp in ("Power Line", "Both") else "", ["Power Line"])
    ctx["test_port_signal"] = human_checkbox("Signal Line" if tp in ("Signal Line", "Both") else "", ["Signal Line"])
    ctx["test_voltage_power_line"] = cumulative_checkbox(
        _s(form_data.get("test_voltage_power_line")), ["±0.5 kV", "±1 kV", "±2 kV", "±4 kV"])
    ctx["test_voltage_signal_line"] = cumulative_checkbox(
        _s(form_data.get("test_voltage_signal_line")), ["±0.25 kV", "±0.5 kV", "±1 kV", "±2 kV"])
    ctx["pulse_repetition_frequency"] = human_checkbox(
        _s(form_data.get("pulse_repetition_frequency")), ["5 kHz", "100 kHz"])
    ctx["eut_configuration"] = human_checkbox(
        _s(form_data.get("eut_configuration")), ["Tabletop", "Floor standing"])
    # dynamic observation tables — consumed by the generator after render (not in the template)
    ctx["eft_obs_power"] = _eft_obs(form_data, "power")
    ctx["eft_obs_signal"] = _eft_obs(form_data, "signal")
    # observation legend: one {code, desc} per unique observation code entered
    codes = _list(form_data, "eft_obs_legend_code[]")
    descs = _list(form_data, "eft_obs_legend_desc[]")
    legend, seen = [], set()
    for i, code in enumerate(codes):
        code = _s(code)
        if code and code not in seen:
            seen.add(code)
            legend.append({"code": code, "desc": _s(descs[i]) if i < len(descs) else ""})
    ctx["eft_obs_legend"] = legend
    return ctx


def _surge_obs(form_data, kind):
    """Rebuild a Surge observation matrix the form posted (kind='ac'|'dc'|'signal').
    Columns come from surge_obs_<kind>_cols (pipe-joined, e.g. 'CM L→PE 0°|...'),
    rows from surge_obs_<kind>_row_<ri> + cells surge_obs_<kind>_<ri>__c<ci>.
    Returns {'cols':[...], 'rows':[{label, cells}]} or None when nothing was posted."""
    cols = [c for c in _s(form_data.get("surge_obs_%s_cols" % kind)).split("|") if c]
    if not cols:
        return None
    rows = []
    ri = 0
    while form_data.get("surge_obs_%s_row_%d" % (kind, ri)) is not None:
        cells = [_s(form_data.get("surge_obs_%s_%d__c%d" % (kind, ri, ci))) for ci in range(len(cols))]
        rows.append({"label": _s(form_data.get("surge_obs_%s_row_%d" % (kind, ri))), "cells": cells})
        ri += 1
    return {"cols": cols, "rows": rows}


def _surge_build_context(form_data):
    """SURGE docx context: ticked checkboxes, cumulative test voltages (Power/Signal
    x CM/DM), fixed Coupling Phases + Repetition Rate, a never-blank Monitoring value,
    and the dynamic observation matrices (inserted post-render by the generator)."""
    from .layout import human_checkbox, cumulative_checkbox, RunsXml, _label_run
    ctx = {}
    POWER = ["±0.5 kV", "±1 kV", "±2 kV", "±4 kV"]
    SIGNAL = ["±0.5 kV", "±1 kV", "±2 kV", "Custom"]

    def _plain(text):
        return RunsXml(_label_run(text))

    ctx["immunity_test_requirement"] = human_checkbox(
        _s(form_data.get("immunity_test_requirement")), ["Basic", "Industrial", "Controlled", "Custom"])

    p_appl = _s(form_data.get("test_port_power")).strip().lower().startswith("appl")
    s_appl = _s(form_data.get("test_port_signal")).strip().lower().startswith("appl")
    ctx["test_port_power"] = human_checkbox("Power Line" if p_appl else "", ["Power Line"])
    ctx["test_port_signal"] = human_checkbox("Signal Line" if s_appl else "", ["Signal Line"])

    # Test Voltage (kV): cumulative checkboxes for tested ports; "Not Applicable" otherwise.
    ctx["tv_cm_power"] = cumulative_checkbox(_s(form_data.get("surge_tv_cm_power")), POWER) if p_appl else _plain("Not Applicable")
    ctx["tv_dm_power"] = cumulative_checkbox(_s(form_data.get("surge_tv_dm_power")), POWER) if p_appl else _plain("Not Applicable")
    ctx["tv_cm_signal"] = cumulative_checkbox(_s(form_data.get("surge_tv_cm_signal")), SIGNAL) if s_appl else _plain("Not Applicable")
    ctx["tv_dm_signal"] = cumulative_checkbox(_s(form_data.get("surge_tv_dm_signal")), SIGNAL) if s_appl else _plain("Not Applicable")

    ctx["coupling_phases"] = human_checkbox(
        _s(form_data.get("coupling_phases")) or "0°, 90°, 180°, 270°", ["0°", "90°", "180°", "270°"])
    ctx["repetition_rate"] = human_checkbox(
        _s(form_data.get("repetition_rate")) or "60 Sec", ["60 Sec", "Custom"])

    ctx["eut_configuration_col_2"] = human_checkbox(_s(form_data.get("eut_configuration")), ["Tabletop"])
    ctx["eut_configuration_col_3"] = human_checkbox(_s(form_data.get("eut_configuration")), ["Floor standing"])

    ctx["monitoring_parameters"] = _s(form_data.get("monitoring_parameters")) or "No Error Message"

    # dynamic observation matrices — consumed by the generator after render
    ctx["surge_obs_ac"] = _surge_obs(form_data, "ac")
    ctx["surge_obs_dc"] = _surge_obs(form_data, "dc")
    ctx["surge_obs_signal"] = _surge_obs(form_data, "signal")
    codes = _list(form_data, "surge_obs_legend_code[]")
    descs = _list(form_data, "surge_obs_legend_desc[]")
    legend, seen = [], set()
    for i, code in enumerate(codes):
        code = _s(code)
        if code and code not in seen:
            seen.add(code)
            legend.append({"code": code, "desc": _s(descs[i]) if i < len(descs) else ""})
    ctx["surge_obs_legend"] = legend
    return ctx


def _pfmf_build_context(form_data):
    """PFMF docx context: ticked checkboxes for Test Method (Proximity/Immersion),
    EUT Configuration (Tabletop/Floor standing) and the multi-select Coil Orientation
    (proximity angles + immersion axes), each rendered into its own {{r ... }} cell."""
    from .layout import human_checkbox, RunsXml, _box_run, _label_run
    ctx = {}

    def multi(selected, options):
        sel = {_s(x) for x in selected if _s(x)}
        rt = RunsXml()
        for i, opt in enumerate(options):
            rt.add(_box_run(opt in sel))
            sep = "    " if i < len(options) - 1 else ""
            rt.add(_label_run(" " + opt + sep))
        return rt

    method = _s(form_data.get("test_method"))
    ctx["test_method_proximity"] = human_checkbox(method, ["Proximity method"])
    ctx["test_method_immersion"] = human_checkbox(method, ["Immersion method"])

    cfg = _s(form_data.get("eut_configuration"))
    ctx["eut_configuration_tabletop"] = human_checkbox(cfg, ["Tabletop"])
    ctx["eut_configuration_floor"] = human_checkbox(cfg, ["Floor standing"])

    ctx["coil_orientation_proximity"] = multi(_list(form_data, "coil_angles[]"), ["0°", "90°", "180°", "270°"])
    ctx["coil_orientation_immersion"] = multi(_list(form_data, "coil_axes[]"), ["X", "Y", "Z"])

    # Test Level: tick the chosen fixed option, or tick Custom and fill in its value.
    def _level(value):
        v = _s(value)
        rt = RunsXml()
        matched = False
        for opt in ["1A/m", "3A/m", "30A/m"]:
            on = (v == opt)
            matched = matched or on
            rt.add(_box_run(on)).add(_label_run(" " + opt + "    "))
        custom_on = bool(v) and not matched
        rt.add(_box_run(custom_on)).add(_label_run(" Custom " + (v if custom_on else "______")))
        return rt
    ctx["test_level"] = _level(form_data.get("test_level"))

    # Observation legend: one {code, desc} per unique A/B/C/D/NA value the engineer used.
    codes = _list(form_data, "pfmf_obs_legend_code[]")
    descs = _list(form_data, "pfmf_obs_legend_desc[]")
    legend, seen = [], set()
    for i, c in enumerate(codes):
        c = _s(c)
        if c and c not in seen:
            seen.add(c)
            legend.append({"code": c, "desc": _s(descs[i]) if i < len(descs) else ""})
    ctx["pfmf_obs_legend"] = legend
    return ctx


def _esd_build_context(form_data):
    """ESD docx context: ticked EUT-Configuration cells, the two-line Indirect
    Contact Discharge cell (HCP line + VCP line), and all observation-table cell
    values (Indirect 8 fixed rows; Direct/Air 3 rows with editable names)."""
    from .layout import human_checkbox, RunsXml
    ctx = {}
    cfg = _s(form_data.get("eut_configuration"))
    ctx["eut_configuration_tabletop"] = human_checkbox(cfg, ["Tabletop"])
    ctx["eut_configuration_floor"] = human_checkbox(cfg, ["Floor standing"])

    hcp = human_checkbox(_s(form_data.get("indirect_hcp")), ["NA", "±2kV", "±4kV", "±8kV", "Custom"])
    vcp = human_checkbox(_s(form_data.get("indirect_vcp")), ["±2kV", "±4kV", "±8kV", "Custom"])
    ctx["indirect_contact_discharge_hcp_vcp"] = RunsXml(str(hcp)).add('<w:r><w:br/></w:r>').add(str(vcp))

    for i in range(1, 9):                       # Indirect: 8 fixed rows
        for c in range(1, 7):
            k = "ind_r%d_c%d" % (i, c)
            ctx[k] = _s(form_data.get(k))
    for grp in ("dir", "air"):                  # Direct / Air: 3 rows + editable name
        for i in range(1, 4):
            ctx["%s_r%d_name" % (grp, i)] = _s(form_data.get("%s_r%d_name" % (grp, i)))
            for c in range(1, 7):
                k = "%s_r%d_c%d" % (grp, i, c)
                ctx[k] = _s(form_data.get(k))
    return ctx


def _rs_ri_build_context(form_data):
    """RS Field Strength cells: render the ticked options with a fill-in value on
    Custom (e.g. '☐ 3V/m ☐ 10V/m ☐ 30V/m ☒ Custom 5V/m') — the generic checkbox
    can only tick a fixed option, not carry the custom numeric value."""
    from .layout import RunsXml, _box_run, _label_run
    fixed = ["3V/m", "10V/m", "30V/m"]

    def fs(value):
        v = _s(value)
        rt = RunsXml()
        matched = False
        for opt in fixed:
            on = (v == opt)
            matched = matched or on
            rt.add(_box_run(on)).add(_label_run(" " + opt + "    "))
        custom_on = bool(v) and not matched
        rt.add(_box_run(custom_on)).add(_label_run(" Custom " + (v if custom_on else "______")))
        return rt

    return {
        "field_strength_col_1": fs(form_data.get("field_strength_col_1")),
        "field_strength_col_2": fs(form_data.get("field_strength_col_2")),
    }


def build_context(schema, form_data):
    """Map the posted form into the docxtpl context for this schema."""
    ctx = {}
    # scalar fields (incl. those inside 'fields' groups); images set by the generator
    for f in iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
        raw_val = form_data.get(f["key"])
        if raw_val is None:
            raw_val = f.get("default") or ""
            if isinstance(raw_val, str) and "<Standard name>" in raw_val:
                prod_std = form_data.get("product_standard") or ""
                raw_val = raw_val.replace("<Standard name>", prod_std)
        val = _s(raw_val)
        # Fields declared with a "checkbox" option list render as human-ticked
        # checkboxes (their template placeholder is {{r key }}).
        if f.get("checkbox"):
            from .layout import human_checkbox
            val = human_checkbox(val, f["checkbox"])
        elif (schema.get("code") or "").upper() in ("RE", "HARMONIC", "VOLTAGEFLICKER") and f["key"] == "eut_configuration":
            from .layout import human_checkbox
            ctx["eut_configuration_col_1"] = human_checkbox(val, ["Tabletop"])
            ctx["eut_configuration_col_2"] = human_checkbox(val, ["Floor standing"])
        ctx[f["key"]] = val
    # optional per-image captions ({key}_caption) for image items flagged caption:true
    for sec in schema["sections"]:
        for it in sec["items"]:
            cap_imgs = []
            if it["type"] == "fields":
                cap_imgs = [f for f in it.get("fields", []) if f.get("input") == "image" and f.get("caption")]
            elif it.get("caption") and (it["type"] == "image" or (it["type"] == "field" and it.get("input") == "image")):
                cap_imgs = [it]
            for f in cap_imgs:
                ctx[f["key"] + "_caption"] = _s(form_data.get(f["key"] + "_caption")) or f.get("label", "")
    # repeating tables
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it["type"] != "table":
                continue
            cols = [c["key"] for c in it["columns"]]
            arrs = {c: _list(form_data, f"{it['key']}__{c}[]") for c in cols}
            n = max((len(a) for a in arrs.values()), default=0)
            rows = []
            for i in range(n):
                row = {c: (_s(arrs[c][i]) if i < len(arrs[c]) else "") for c in cols}
                if any(row.values()):
                    rows.append(row)
            ctx[it["key"]] = rows
    # Observation legend (RS_RI / ESD / CRF / VOLTAGEDIPS generic mechanism): one
    # {code, desc} per unique observation code the engineer selected on the form
    # (posted as obs_legend_code[] / obs_legend_desc[]). EFT/SURGE/PFMF use their own
    # prefixed fields, handled in their per-code build_context.
    _leg_codes = _list(form_data, "obs_legend_code[]")
    _leg_descs = _list(form_data, "obs_legend_desc[]")
    _legend, _seen = [], set()
    for _i, _c in enumerate(_leg_codes):
        _c = _s(_c)
        if _c and _c not in _seen:
            _seen.add(_c)
            _legend.append({"code": _c, "desc": _s(_leg_descs[_i]) if _i < len(_leg_descs) else ""})
    ctx["obs_legend"] = _legend
    # Per-image document size (Word-style Shape Width x Height in cm), posted as
    # <key>__wcm / <key>__hcm; stored in mm for the generator. When absent, the
    # generator falls back to that slot's default box.
    _img_boxes = {}
    for _ik in image_keys(schema):
        _w, _h = _s(form_data.get(_ik + "__wcm")), _s(form_data.get(_ik + "__hcm"))
        if _w and _h:
            try:
                _img_boxes[_ik] = (float(_w) * 10.0, float(_h) * 10.0)
            except ValueError:
                pass
    ctx["_img_boxes"] = _img_boxes
    if schema.get("code") == "RE":
        ctx["measurement_groups"] = _re_measurement_groups(form_data)
        freq = _s(form_data.get("frequency_range"))
        ctx.update(_re_test_spec_columns(freq))
        ctx.update(_re_limit_tables(_s(form_data.get("product_standard")),
                                    _s(form_data.get("classification_col_2")),
                                    freq))
        # EUT Input Voltage, Ambient Temp, Relative Humidity:
        # Single user value goes into the SELECTED frequency column; the other gets "-"
        s30 = (freq == "30MHz-1GHz")
        s16 = (freq == "1GHz-6GHz")
        DASH = "-"
        for base_key in ("eut_input_voltage_frequency", "ambient_temperature", "relative_humidity"):
            val = ctx.get(base_key, "")
            ctx[base_key + "_col_1"] = val if s30 else (DASH if s16 else val)
            ctx[base_key + "_col_2"] = val if s16 else (DASH if s30 else val)
    if schema.get("code") == "HARMONIC":
        ctx.update(_harmonic_build_context(form_data))
    if schema.get("code") == "VOLTAGEDIPS":
        ctx.update(_vdips_build_context(form_data))
    if schema.get("code") == "EFT":
        ctx.update(_eft_build_context(form_data))
    if schema.get("code") == "SURGE":
        ctx.update(_surge_build_context(form_data))
    if schema.get("code") == "PFMF":
        ctx.update(_pfmf_build_context(form_data))
    if schema.get("code") == "ESD":
        ctx.update(_esd_build_context(form_data))
    if schema.get("code") == "RS_RI":
        ctx.update(_rs_ri_build_context(form_data))
    return ctx


def _harmonic_build_context(form_data):
    """Build HARMONIC-specific context entries from posted form data.

    harmonic_rows : list of dicts {c0..c9} — the 40-row measurement table
                    submitted as harmonic_row__cN[] hidden arrays from the CSV upload.
    test_limits_rows / test_limits_rows_2 : Odd/Even harmonic limit rows,
                    submitted as hidden inputs derived from Classification in JS.
    """
    out = {}

    # --- Measurement data rows (4 columns c0..c3) ---
    cols = ["c" + str(i) for i in range(4)]
    arrs = {c: _list(form_data, f"harmonic_row__{c}[]") for c in cols}
    n = max((len(a) for a in arrs.values()), default=0)
    rows = []
    for i in range(n):
        row = {c: (_s(arrs[c][i]) if i < len(arrs[c]) else "") for c in cols}
        if any(row.values()):
            rows.append(row)
    out["harmonic_rows"] = rows

    # --- Test limits (Odd harmonics) ---
    odd_cols = ["c0", "c1"]
    odd_arrs = {c: _list(form_data, f"test_limits_rows__{c}[]") for c in odd_cols}
    n_odd = max((len(a) for a in odd_arrs.values()), default=0)
    odd_rows = []
    for i in range(n_odd):
        row = {c: (_s(odd_arrs[c][i]) if i < len(odd_arrs[c]) else "") for c in odd_cols}
        if any(row.values()):
            odd_rows.append(row)
    out["test_limits_rows"] = odd_rows

    # --- Test limits (Even harmonics) ---
    even_arrs = {c: _list(form_data, f"test_limits_rows_2__{c}[]") for c in odd_cols}
    n_even = max((len(a) for a in even_arrs.values()), default=0)
    even_rows = []
    for i in range(n_even):
        row = {c: (_s(even_arrs[c][i]) if i < len(even_arrs[c]) else "") for c in odd_cols}
        if any(row.values()):
            even_rows.append(row)
    out["test_limits_rows_2"] = even_rows

    # --- Average & Maximum harmonic current results (10 cols c0..c9) ---
    # Imported from the instrument RTF via the Functional Check 'Import TXT' button.
    amx_cols = ["c" + str(i) for i in range(10)]
    amx_arrs = {c: _list(form_data, f"avgmax_row__{c}[]") for c in amx_cols}
    n_amx = max((len(a) for a in amx_arrs.values()), default=0)
    amx_rows = []
    for i in range(n_amx):
        row = {c: (_s(amx_arrs[c][i]) if i < len(amx_arrs[c]) else "") for c in amx_cols}
        if any(row.values()):
            amx_rows.append(row)
    out["avgmax_rows"] = amx_rows

    return out


def _re_limit_tables(product_standard, cls, freq):
    """Test-Limit tables driven by the (Product Standard family x Class x Frequency
    Range) combination. Returns row lists the template loops over:
      re_limit_cispr_qp : CISPR/ICES Quasi-peak (30 MHz-1 GHz)  [{c0 band, c1 limit}]
      re_limit_fcc_qp   : FCC Quasi-peak (30 MHz-1 GHz)         [{c0, c1}]
      re_limit_fcc_pa   : FCC Peak & Average (1 GHz-6 GHz)      [{c0, c1 peak, c2 avg}]
    A table's list is empty (so the template hides it) when that family/band doesn't
    apply. 30 MHz-1 GHz -> the QP tables for whichever families the standard names;
    1 GHz-6 GHz -> the FCC Peak/Average table (CISPR has no 1-6 GHz RE limit)."""
    from . import re_logic
    # Selection LOGIC stays here; the limit NUMBERS come from the admin-editable
    # datasheet_fixed_values table (RE.test_limits). re_logic stays as fallback.
    from .fixed_store import get_fixed_values
    tl = (get_fixed_values("RE") or {}).get("test_limits", {})
    qp = tl.get("qp_30m_1g", {})
    pa = tl.get("pa_1g_6g", {})
    fams = re_logic.families(product_standard)
    c = re_logic._norm_class(cls) or "B"
    is30 = (freq == "30MHz-1GHz")
    is16 = (freq == "1GHz-6GHz")
    out = {"re_limit_cispr_qp": [], "re_limit_fcc_qp": [], "re_limit_fcc_pa": []}
    # CISPR/ICES have no separate 1-6 GHz radiated-emission limit — their only RE
    # limit is the 30 MHz-1 GHz Quasi-peak table, so it always applies when a CISPR
    # standard is named (regardless of the band toggle). FCC's QP (30M-1G) and
    # Peak/Average (1-6G) tables stay gated by the selected frequency range.
    if "CISPR" in fams:
        out["re_limit_cispr_qp"] = [{"c0": r[0], "c1": r[1]} for r in qp.get("CISPR", {}).get(c, [])]
    if "FCC" in fams and (is30 or not is16):
        out["re_limit_fcc_qp"] = [{"c0": r[0], "c1": r[1]} for r in qp.get("FCC", {}).get(c, [])]
    if "FCC" in fams and is16:
        out["re_limit_fcc_pa"] = [{"c0": r[0], "c1": r[1], "c2": r[2]} for r in pa.get("FCC", {}).get(c, [])]
    return out


def _re_test_spec_columns(freq):
    """Derive the Test Specification two-column values from the single selected
    Frequency Range (col_1 = 30 MHz–1 GHz, col_2 = 1 GHz–6 GHz).

    Band-specific FIXED parameters print their value in the SELECTED band's column
    and '-' in the other (matching the reference datasheet, which keeps both columns).
    Detector and Polarization are definitional and shown for both bands. The
    Frequency Range row itself renders as a ticked/unticked checkbox per band.
    """
    from .layout import human_checkbox
    from .fixed_store import get_fixed_values
    sd = (get_fixed_values("RE") or {}).get("spec_defaults", {})   # fixed values from DB
    s30 = (freq == "30MHz-1GHz")
    s16 = (freq == "1GHz-6GHz")
    DASH = "-"

    def band(k1, k2, d1="", d2=""):
        v30, v16 = sd.get(k1, d1), sd.get(k2, d2)
        return (v30 if s30 else DASH), (v16 if s16 else DASH)

    cols = {}
    # real ticked/unticked checkboxes (RunsXml -> {{r ... }}), same rendering as Classification
    cols["frequency_range_col_1"] = human_checkbox("30MHz-1GHz" if s30 else "", ["30MHz-1GHz"])
    cols["frequency_range_col_2"] = human_checkbox("1GHz-6GHz" if s16 else "", ["1GHz-6GHz"])
    cols["resolution_bandwidth_col_1"], cols["resolution_bandwidth_col_2"] = band("resolution_bandwidth_col_1", "resolution_bandwidth_col_2", "120k", "1M")
    cols["video_bandwidth_col_1"], cols["video_bandwidth_col_2"] = band("video_bandwidth_col_1", "video_bandwidth_col_2", "1M", "3M")
    cols["step_size_col_1"], cols["step_size_col_2"] = band("step_size_col_1", "step_size_col_2", "40k", "400k")
    cols["turn_table_rotation_step_col_1"], cols["turn_table_rotation_step_col_2"] = band("turn_table_rotation_step_col_1", "turn_table_rotation_step_col_2", "15°", "22.5°")
    cols["antenna_height_variation_step_for_pre_scan_mea_2"], cols["antenna_height_variation_step_for_pre_scan_mea_3"] = band("antenna_height_variation_step_for_pre_scan_mea_2", "antenna_height_variation_step_for_pre_scan_mea_3", "1", "1")
    cols["antenna_height_variation_for_final_measurement_2"], cols["antenna_height_variation_for_final_measurement_3"] = band("antenna_height_variation_for_final_measurement_2", "antenna_height_variation_for_final_measurement_3", "1-4", "1-2")
    cols["pre_scan_measurement_time_col_1"], cols["pre_scan_measurement_time_col_2"] = band("pre_scan_measurement_time_col_1", "pre_scan_measurement_time_col_2", "20", "20")
    cols["final_scan_measurement_time_col_1"], cols["final_scan_measurement_time_col_2"] = band("final_scan_measurement_time_col_1", "final_scan_measurement_time_col_2", "1", "1")
    cols["attenuation_col_1"], cols["attenuation_col_2"] = band("attenuation_col_1", "attenuation_col_2", "Auto", "Auto")
    # definitional — shown for both bands
    cols["polarization_col_1"] = cols["polarization_col_2"] = sd.get("polarization_col_1", "Horizontal and Vertical")
    cols["detector_col_1"] = sd.get("detector_col_1", "Peak and Quasi-peak")
    cols["detector_col_2"] = sd.get("detector_col_2", "Peak and Average")
    return cols


def _immunity_from_env(request_obj):
    """Datasheet Immunity Test Requirement (Basic / Industrial / Controlled /
    Custom) derived from the request's Product Environments (the intake
    'Non-Medical / Medical Environment' selection). Returns '' when it can't be
    told (e.g. medical, where the datasheet derives levels from the product
    standard instead)."""
    try:
        rows = getattr(request_obj, "product_environments", []) or []
    except Exception:  # noqa: BLE001
        return ""
    blob = " ".join(
        (_s(getattr(r, "environment_key", "")) + " " + _s(getattr(r, "environment_value", "")))
        for r in rows
        if _s(getattr(r, "environment_value", "")).lower() not in ("", "no", "none", "false", "0")
    ).lower()
    if "industrial" in blob:
        return "Industrial"
    if "controlled" in blob:
        return "Controlled"
    if "basic" in blob:
        return "Basic"
    if "custom" in blob:
        return "Custom"
    return ""


def _rs_field_strength(standard, immunity, band):
    """RS test Field Strength (V/m) auto-fill, derived from the Product Standard x
    Immunity Test Requirement, per frequency band. `band` is 'low' (80 MHz-1 GHz,
    col_1) or 'high' (1 GHz-6 GHz, col_2). Returns "" for combinations left to
    manual entry. Values match the field's checkbox options so the box gets ticked.

        61326-1   Basic                 -> 3 V/m  (low) / 3 V/m (high)
        61326-1   Industrial            -> 10 V/m (low) / 3 V/m (high)
        60601-1-2 (Home / Professional) -> 3 V/m across 80 MHz-2.7 GHz (both bands)
    """
    psn = re.sub(r"[^0-9a-z]", "", (standard or "").lower())
    imm = (immunity or "").strip().lower()
    if "6060112" in psn:                       # IEC/EN 60601-1-2 (medical)
        return "3V/m"
    if "613261" in psn:                        # IEC/EN 61326-1 (non-medical)
        if imm == "basic":
            return "3V/m"
        if imm == "industrial":
            return "10V/m" if band == "low" else "3V/m"
    return ""


def collect_prefill(schema, request_obj, assignment):
    """Best-effort auto-fill from the request, mapped onto this schema's field keys.

    Mirrors the values CE auto-fills (job/TCO, EUT name/model/serial, product
    standard, supply voltage/frequency, tested-by, deviation). Model/serial fall
    back to the request's own scalar columns when the multi-valued child rows are
    empty (e.g. a single-model request), so prefill works regardless of how the
    request was captured.
    """
    job = name = model = serial = standard = vf = monitoring = test_mode = cfg = ""
    vf_rows = []   # individual supply rows for RE col_1 / col_2 split
    if request_obj is not None:
        job = _ra(request_obj, "job_number", "tco_id")
        name = _ra(request_obj, "product_name")
        # Primary Product-Identity columns first, multi-valued child rows as fallback.
        model = _ra(request_obj, "model_number") or _join(getattr(request_obj, "additional_models", []), "model_number")
        serial = _ra(request_obj, "serial_number") or _join(getattr(request_obj, "serial_numbers", []), "serial_number")
        standard = _join(getattr(request_obj, "product_standards", []), "standard_value")
        vf_rows = getattr(request_obj, "supply_vf_values", []) or []
        vf = _fmt_supply(vf_rows)
        monitoring = _ra(request_obj, "monitoring_parameters")
        test_mode = _normalize_numbered(_functional_modes_text(request_obj) or _ra(request_obj, "test_configuration", "operation_modes"))
        cfg = _eut_config(request_obj)  # 'Tabletop' / 'Floor standing' / ''
    eng = _s(getattr(assignment, "test_person_name", "")) if assignment else ""
    detail = _test_detail(request_obj, schema.get("code"))
    _code = (schema.get("code") or "").upper()
    # CRF: Immunity Test Requirement / Test Port / Coupling Method are chosen at
    # Test-Request intake and stored in the CRF detail's custom_spec JSON blob
    # (keys immunityTestRequirement / testPort / couplingMethod). Parse once here
    # so the loop below can prefill the datasheet dropdowns from them.
    crf_spec = {}
    if _code == "CRF" and detail is not None:
        try:
            _raw = getattr(detail, "custom_spec", None)
            crf_spec = json.loads(_raw) if isinstance(_raw, str) else (_raw or {})
            if not isinstance(crf_spec, dict):
                crf_spec = {}
        except Exception:
            crf_spec = {}

    # SURGE stores its intake in custom_spec too (scalar columns are blank):
    #   cables.power/signal -> Test Ports; testLevel / customCommonKV etc. -> voltages.
    surge_spec = {}
    if _code == "SURGE" and detail is not None:
        try:
            _raw = getattr(detail, "custom_spec", None)
            surge_spec = json.loads(_raw) if isinstance(_raw, str) else (_raw or {})
            if not isinstance(surge_spec, dict):
                surge_spec = {}
        except Exception:
            surge_spec = {}

    is_re = (schema.get("code") or "").upper() == "RE"
    basic_std = "Sysmex"
    turn_table_step = "15°"
    if is_re:
        basic_std = "EN 55011:2016+A2:2021"
        if standard:
            ps_clean = "".join(c for c in standard.lower() if c.isalnum())
            if "en61326" in ps_clean or "en55011" in ps_clean:
                basic_std = 'EN 55011:2016+A2:2021'
            elif "iec61326" in ps_clean or "ices" in ps_clean or "cispr11" in ps_clean:
                basic_std = 'CISPR 11:2015+A1:2016+A2:2019'
            elif "part15" in ps_clean or "cfr" in ps_clean or "fcc" in ps_clean or "c634" in ps_clean:
                basic_std = 'ANSI C63.4:2024'
        turn_table_step = '22.5°' if basic_std == 'ANSI C63.4:2024' else '15°'

    pre = {}
    # Immunity Test Requirement derived from the TR environment — reused by the
    # immunity checkbox and the RS field-strength derivation below.
    immunity_env = _immunity_from_env(request_obj) if request_obj is not None else ""
    for f in iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
        default = str(f.get("default") or "")
        if "default" in f and "{{" not in default and "{%" not in default:
            val = f["default"]
            # source-doc boilerplate token -> the request's actual standard
            if isinstance(val, str) and "<Standard name>" in val and standard:
                val = val.replace("<Standard name>", standard)
            pre[f["key"]] = val            # never surface template syntax as a value
        k = f["key"].lower()
        if "job_number" in k:
            pre[f["key"]] = job
        elif "eut_name" in k:
            pre[f["key"]] = name
        elif "eut_model" in k:
            pre[f["key"]] = model
        elif "eut_serial" in k:
            pre[f["key"]] = serial
        elif "product_standard" in k:
            pre[f["key"]] = standard
        elif k == "basic_standard":
            # Product -> Basic standard now comes from the admin-editable
            # basic_standard_map table (per test_code; emission is shared/global).
            _bcode = (schema.get("code") or "").upper()
            if _bcode in ("RE", "HARMONIC", "VOLTAGEFLICKER", "CE"):
                from .fixed_store import basic_standard as _bs
                pre[f["key"]] = _bs(standard, _bcode) or default
            else:
                # Derived basic standard per test (kept in code for now; can move to
                # the basic_standard_map table later, like the DB-backed datasheets).
                _basic_map = {
                    "VOLTAGEDIPS": "IEC 61000-4-11:2020 & EN 61000-4-11:2020",
                    "EFT": "IEC 61000-4-4:2012 & EN 61000-4-4:2012",
                    "SURGE": "IEC 61000-4-5:2014+A1:2017 & EN 61000-4-5:2014+A1:2017",
                    "CRF": "IEC 61000-4-6:2023 & EN 61000-4-6:2023",
                    "RS_RI": "EN 61000-4-3:2020 & IEC 61000-4-3:2020",
                    "PFMF": "IEC 61000-4-8:2009 & EN 61000-4-8:2010",
                    "ESD": "IEC 61000-4-2:2008 & EN 61000-4-2:2009",
                }
                pre[f["key"]] = _basic_map.get(_bcode, "Sysmex")
        elif "monitoring_parameters" in k:
            # Pull from the Test Request; if the TR has nothing, fall back to the
            # schema's constant default so the field is never blank on the datasheet.
            pre[f["key"]] = monitoring or default
        elif "voltage" in k and "frequency" in k:
            pre[f["key"]] = vf
        elif k == "test_mode":
            pre[f["key"]] = test_mode
        elif _code == "CRF" and k == "immunity_test_requirement":
            v = _s(crf_spec.get("immunityTestRequirement"))
            if v:
                pre[f["key"]] = v
        elif _code == "CRF" and k == "test_port":
            v = _s(crf_spec.get("testPort"))
            if v:
                pre[f["key"]] = v
        elif _code == "CRF" and k == "coupling_method":
            v = _s(crf_spec.get("couplingMethod"))
            if v:
                pre[f["key"]] = v
        elif k == "immunity_test_requirement" and _code in (
                "SURGE", "VOLTAGEDIPS", "EFT", "ESD", "RS_RI", "PFMF"):
            # From the TR's Non-Medical/Medical Environment (CRF has its own
            # explicit source above). Only fills the field when it is otherwise
            # blank, so it can never override an intake-specific value. For SURGE
            # it also drives the derived Test Voltage + observation matrix.
            v = immunity_env
            if v:
                pre[f["key"]] = v
        elif _code == "RS_RI" and k in ("field_strength_col_1", "field_strength_col_2"):
            # Field Strength (V/m) derived from Product Standard x Immunity Test
            # Requirement, per band (col_1 = 80 MHz-1 GHz, col_2 = 1 GHz-6 GHz).
            band = "low" if k.endswith("_col_1") else "high"
            v = _rs_field_strength(standard, immunity_env, band)
            if v:
                pre[f["key"]] = v
        elif _code == "RS_RI" and k in ("f_80_to_1000_col_1", "f_1000_to_6000_col_1"):
            # TEST OBSERVATION "Test level (V/m)" mirrors the band's derived Field
            # Strength (numeric only — the column header already carries the V/m unit).
            band = "low" if k.startswith("f_80_to_1000") else "high"
            v = re.sub(r"[^0-9.]", "", _rs_field_strength(standard, immunity_env, band))
            if v:
                pre[f["key"]] = v
        elif _code == "SURGE" and k in ("test_port_power", "test_port_signal"):
            cbl = surge_spec.get("cables") or {}
            side = "power" if k == "test_port_power" else "signal"
            pre[f["key"]] = "Applicable" if _s(cbl.get(side)).lower() in ("yes", "true", "1") else "Not Applicable"
        elif "modification_state" in k:
            pre[f["key"]] = "0 - Initial state"   # manager: modification defaults to 0
        elif k.startswith("eut_configuration"):
            if (schema.get("code") or "").upper() in ("RE", "HARMONIC", "VOLTAGEFLICKER", "CRF", "RS_RI", "PFMF", "ESD"):
                if cfg in ("Tabletop", "Floor standing"):
                    pre[f["key"]] = cfg
                elif cfg and "table" in cfg.lower():
                    pre[f["key"]] = "Tabletop"
                elif cfg and "floor" in cfg.lower():
                    pre[f["key"]] = "Floor standing"
                else:
                    pre[f["key"]] = default
            else:
                # The doc prints Tabletop / Floor standing in adjacent cells; keep the
                # cell matching the request's form factor, blank the other one.
                if cfg:
                    d = default.lower()
                    if "tabletop" in d:
                        pre[f["key"]] = "Tabletop" if cfg == "Tabletop" else ""
                    elif "floor" in d:
                        pre[f["key"]] = "Floor standing" if cfg == "Floor standing" else ""
        elif f.get("checkbox") and k.startswith("classification"):
            # tick Group from TR product_group, Class from TR class (or the
            # harmonics equipment class for the HARMONIC datasheet)
            opts = " ".join(str(o) for o in f["checkbox"]).lower()
            if "group" in opts:
                pre[f["key"]] = _ra(request_obj, "product_group")
            else:
                pre[f["key"]] = _norm_class(
                    _s(getattr(detail, "harmonic_class", "")) or _ra(request_obj, "class_type"))
        elif k == "direct_contact_discharge" or k.startswith("indirect_contact_discharge"):
            v = _s(getattr(detail, "contact_level", ""))
            if _is_custom(v):
                pre[f["key"]] = v
        elif k == "air_discharge":
            v = _s(getattr(detail, "air_level", ""))
            if _is_custom(v):
                pre[f["key"]] = v
        elif k == "frequency_range":
            v = _s(getattr(detail, "freq_range", ""))
            if _is_custom(v):
                pre[f["key"]] = v
        elif k == "result_class":
            # 'Class A' -> 'A' (the doc sentence reads 'as per Class {{ result_class }} limit')
            ct = _ra(request_obj, "class_type")
            pre[f["key"]] = ct.split()[-1].upper() if ct and ct.split()[-1].upper() in ("A", "B") else ""
        elif "tested_by" in k or k == "tested_by":
            pre[f["key"]] = eng
        elif k == "name":
            # RESULT section "Name" = the test engineer (Tested By – Name), auto-filled
            # from the Job Assignment, same source as tested_by.
            pre[f["key"]] = eng
        elif k == "deviation":
            pre[f["key"]] = "NA"

        # RE specific scalar fields
        if is_re:
            if k == "resolution_bandwidth_col_1":
                pre[f["key"]] = '120k'
            elif k == "resolution_bandwidth_col_2":
                pre[f["key"]] = '1M'
            elif k == "video_bandwidth_col_1":
                pre[f["key"]] = '1M'
            elif k == "video_bandwidth_col_2":
                pre[f["key"]] = '3M'
            elif k == "step_size_col_1":
                pre[f["key"]] = '40k'
            elif k == "step_size_col_2":
                pre[f["key"]] = '400k'
            elif k == "antenna_height_variation_step_for_pre_scan_mea_2":
                pre[f["key"]] = '1'
            elif k == "antenna_height_variation_step_for_pre_scan_mea_3":
                pre[f["key"]] = '1'
            elif k == "antenna_height_variation_for_final_measurement_2":
                pre[f["key"]] = '1-4'
            elif k == "antenna_height_variation_for_final_measurement_3":
                pre[f["key"]] = '1-4'
            elif k == "pre_scan_measurement_time_col_1":
                pre[f["key"]] = '20'
            elif k == "pre_scan_measurement_time_col_2":
                pre[f["key"]] = '20'
            elif k == "final_scan_measurement_time_col_1":
                pre[f["key"]] = '1'
            elif k == "final_scan_measurement_time_col_2":
                pre[f["key"]] = '1'
            elif k == "attenuation_col_1":
                pre[f["key"]] = 'Auto'
            elif k == "attenuation_col_2":
                pre[f["key"]] = 'Auto'
            elif k == "test_distance":
                pre[f["key"]] = '3'
            elif k == "polarization_col_1":
                pre[f["key"]] = 'Horizontal and Vertical'
            elif k == "polarization_col_2":
                pre[f["key"]] = 'Horizontal and Vertical'
            elif k == "detector_col_1":
                pre[f["key"]] = 'Peak and Quasi-peak'
            elif k == "detector_col_2":
                pre[f["key"]] = 'Peak and Average'
            elif k == "turn_table_rotation_step_col_1":
                pre[f["key"]] = turn_table_step
            elif k == "turn_table_rotation_step_col_2":
                pre[f["key"]] = '22.5°'   # 1 GHz–6 GHz band (FCC Part 15 / ANSI C63.4)

    if is_re:
        # Frequency Range single-select default (from the RE test detail; else 30M-1G).
        # This drives the Test Specification columns + Test Limit tables at generation.
        fr = _s(getattr(detail, "freq_range", "")).lower().replace(" ", "")
        pre["frequency_range"] = "1GHz-6GHz" if ("6g" in fr) else "30MHz-1GHz"

        from . import re_logic
        cls_letter = re_logic._norm_class(_ra(request_obj, "class_type")) or "B"
        # CISPR/ICES QP limits (30 MHz – 1 GHz)
        cispr_qp = dict(re_logic._QP_30M_1G.get(("CISPR", cls_letter), []))
        pre["f_30_to_230"] = cispr_qp.get("30 to 230", "")
        pre["f_230_to_1000"] = cispr_qp.get("230 to 1000", "")
        # FCC QP limits (30 MHz – 1 GHz)
        fcc_qp = dict(re_logic._QP_30M_1G.get(("FCC", cls_letter), []))
        pre["f_30_to_88_fcc"] = fcc_qp.get("30 to 88", "")
        pre["f_88_to_216_fcc"] = fcc_qp.get("88 to 216", "")
        pre["f_216_to_960_fcc"] = fcc_qp.get("216 to 960", "")
        pre["f_960_to_1000_fcc"] = fcc_qp.get("960 to 1000", "")

    _apply_db_fixed_scalars(pre, schema)
    return pre


def _apply_db_fixed_scalars(pre, schema):
    """Override the scalar fixed values (Measurement Uncertainty, Functional-Check
    SOP reference, and the fixed Test-Specification parameters) from the admin-
    editable datasheet_fixed_values table, so the DB is the single source of truth.
    Falls back silently to the schema defaults already in `pre` when a value is
    absent from the DB."""
    from .fixed_store import get_fixed_values
    fv = get_fixed_values(schema.get("code"))
    if not fv:
        return
    # Measurement Uncertainty -> the value field of the MEASUREMENT UNCERTAINTY section
    unc = fv.get("measurement_uncertainty")
    if unc:
        for sec in schema.get("sections", []):
            if "UNCERTAIN" in (sec.get("title") or "").upper():
                for it in sec.get("items", []):
                    for f in (it.get("fields", []) if it.get("type") == "fields" else []):
                        if f.get("key") != "name_of_the_test":
                            pre[f["key"]] = unc
    # SOP reference -> the Functional-Check SOP field
    if fv.get("sop_reference") is not None:
        for f in iter_scalar_fields(schema):
            if "sop" in f["key"].lower():
                pre[f["key"]] = fv["sop_reference"]
    # Fixed Test-Specification parameters -> their fields directly (by key)
    for key, val in (fv.get("spec_defaults") or {}).items():
        pre[key] = val


def _equipment_rows_for(code):
    """Test Equipment Used rows from the Equipment Master tagged for this test
    code, as generic-table rows ({c0..c4}). Equipment is selected by the
    Equipment.test_name text column (a comma-separated list of codes, e.g.
    'RE,CE'); the exact code token must be present (avoids loose substring hits)."""
    code = (code or "").upper()
    if not code:
        return []
    try:
        from models import db, Equipment
        candidates = Equipment.query.filter(
            Equipment.status.in_(["Active", "Available"]),
            Equipment.test_name.isnot(None),
            db.or_(Equipment.test_name.ilike(f"%{code}%"),
                   Equipment.test_name.ilike(f"%,{code}%"),
                   Equipment.test_name.ilike(f"%{code},%")),
        ).order_by(Equipment.sl_no.asc(), Equipment.name.asc()).all()
    except Exception:
        return []
    rows = []
    for eq in candidates:
        tn = eq.test_name or ""
        tokens = [t.strip().upper() for t in tn.split(",")]
        is_match = (code in tokens)
        if not is_match:
            if code == 'RE' and 'radiated' in tn.lower():
                is_match = True
            elif code == 'CE' and 'conducted' in tn.lower():
                is_match = True
        if not is_match:
            continue
            
        etype = str(eq.type or "").lower().strip()
        if etype in ("software", "application", "tool"):
            continue
            
        cd = getattr(eq, "calibration_due_date", None)
        rows.append({
            "c0": _s(eq.name),
            "c1": _s(eq.make),
            "c2": _s(eq.model_no),
            "c3": _s(eq.serial_no),
            "c4": cd.isoformat() if cd else "",
        })
    return rows


def _software_rows_for(code):
    """Prefill 'Software Used' rows from the Equipment Master for a given test code."""
    try:
        from models import db, Equipment
        candidates = Equipment.query.filter(
            Equipment.status.in_(["Active", "Available"]),
            Equipment.type.isnot(None),
            db.func.lower(Equipment.type).in_(["software", "application", "tool"]),
            Equipment.test_name.isnot(None),
            db.or_(Equipment.test_name.ilike(f"%{code}%"),
                   Equipment.test_name.ilike(f"%,{code}%"),
                   Equipment.test_name.ilike(f"%{code},%")),
        ).order_by(Equipment.sl_no.asc(), Equipment.name.asc()).all()
    except Exception:
        candidates = []
    rows = []
    for sw in candidates:
        tn = sw.test_name or ""
        tokens = [t.strip().upper() for t in tn.split(",")]
        if code.upper() not in tokens:
            continue
        rows.append({
            "c0": str(sw.name or sw.make or "").strip(),
            "c1": str(sw.model_no or sw.serial_no or "").strip(),
        })
    if not rows and code.upper() == "RE":
        rows = [{"c0": "TDK Emission Lab", "c1": "14.43"}]
    elif not rows and code.upper() in ("HARMONIC", "VOLTAGEFLICKER"):
        rows = [{"c0": "Net.Control", "c1": "3.2.6"}]
    elif not rows and code.upper() in ("VOLTAGEDIPS", "EFT", "SURGE"):
        rows = [{"c0": "iec.control", "c1": "10.3.2"}]
    return rows


def collect_prefill_tables(schema, request_obj, assignment):
    """Prefill repeating-table rows, returned as {table_key: [row dicts]}. The
    route injects these into the schema only when the engineer has no saved
    draft rows for that table.

    Generic: any 'equipment' table is filled from the Equipment Master (matched
    by the schema's test code). RE-specific: the Test Limits tables are filled
    from the (standard family x class) combination, and Software Used defaults."""
    code = (schema.get("code") or "").upper()
    out = {}
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it.get("type") == "table":
                key = it.get("key")
                if "equipment" in key.lower():
                    rows = _equipment_rows_for(code)
                    if rows:
                        out[key] = rows
                elif "software" in key.lower():
                    rows = _software_rows_for(code)
                    if rows:
                        out[key] = rows
    # Software Used is a fixed constant per datasheet — take it from the DB
    # (datasheet_fixed_values.software), overriding the Equipment-Master lookup.
    from .fixed_store import get_fixed_values
    fv = get_fixed_values(code)
    if fv.get("software"):
        for sec in schema["sections"]:
            for it in sec["items"]:
                if it.get("type") == "table" and "software" in (it.get("key", "").lower()):
                    out[it["key"]] = [dict(r) for r in fv["software"]]

    def _rowN(r, n):
        return {"c%d" % i: (r[i] if i < len(r) else "") for i in range(n)}

    if code == "RE":
        from . import re_logic
        ps = _join(getattr(request_obj, "product_standards", []), "standard_value") if request_obj else ""
        cls = _ra(request_obj, "class_type")
        qp, pa = re_logic.limit_rows(ps, cls)
        if qp:
            out["re_limits_qp_rows"] = qp
        if pa:
            out["re_limits_pa_rows"] = pa
        out.setdefault("eut_modification_rec_rows", [{"c0": "0", "c1": "Initial state", "c2": "", "c3": ""}])
        out.setdefault("re_table1_rows", [{"c0": "", "c1": "", "c2": "", "c3": "", "c4": "", "c5": "", "c6": ""}])
    elif code == "HARMONIC":
        out.setdefault("eut_modification_rec_rows", [{"c0": "0", "c1": "Initial state", "c2": "-", "c3": "-"}])
    elif code in ("VOLTAGEDIPS", "EFT", "SURGE"):
        out.setdefault("eut_modification_rec_rows", [{"c0": "0", "c1": "Initial state", "c2": "-", "c3": "-"}])
    elif code == "VOLTAGEFLICKER":
        out.setdefault("eut_modification_rec_rows", [{"c0": "0", "c1": "Initial state", "c2": "-", "c3": "-"}])
        tl = fv.get("test_limits", {})
        if tl.get("fc_rows"):
            out.setdefault("flicker_fc_rows", [_rowN(r, 6) for r in tl["fc_rows"]])
        if tl.get("limits_rows"):
            out.setdefault("flicker_limits_rows", [_rowN(r, 2) for r in tl["limits_rows"]])
        if tl.get("meas_rows"):
            out.setdefault("flicker_meas_rows", [_rowN(r, 3) for r in tl["meas_rows"]])
    return out


def _re_measurement_groups(form_data):
    indices = _list(form_data, "meas_index[]")
    groups = []
    for i in indices:
        i_str = str(i).strip()
        if not i_str:
            continue
        label = _s(form_data.get(f"meas_label_{i_str}"))
        cols = [f"c{j}" for j in range(7)]
        arrs = {c: _list(form_data, f"meas_table_{i_str}__{c}[]") for c in cols}
        n = max((len(a) for a in arrs.values()), default=0)
        rows = []
        for r_idx in range(n):
            row = {c: (_s(arrs[c][r_idx]) if r_idx < len(arrs[c]) else "") for c in cols}
            if any(row.values()):
                rows.append(row)
        if not rows:
            rows = [{"c0": "", "c1": "", "c2": "", "c3": "", "c4": "", "c5": "", "c6": ""}]

        img_vert_key = f"meas_img_vertical_{i_str}"
        img_horiz_key = f"meas_img_horizontal_{i_str}"

        group_idx = len(groups)
        fig_vert_num = 3 + 2 * group_idx
        fig_horiz_num = 4 + 2 * group_idx
        
        default_vert_cap = f"Figure {fig_vert_num}: RE plot_ Vertical_Peak_30MHz - 1GHz"
        default_horiz_cap = f"Figure {fig_horiz_num}: RE plot_Horizontal_Peak_30MHz - 1GHz"

        img_vert_cap = _s(form_data.get(f"meas_img_vertical_caption_{i_str}")) or default_vert_cap
        img_horiz_cap = _s(form_data.get(f"meas_img_horizontal_caption_{i_str}")) or default_horiz_cap

        groups.append({
            "label": label,
            "img_vertical_key": img_vert_key,
            "img_horizontal_key": img_horiz_key,
            "img_vertical_caption": img_vert_cap,
            "img_horizontal_caption": img_horiz_cap,
            "table_rows": rows
        })
    return groups
