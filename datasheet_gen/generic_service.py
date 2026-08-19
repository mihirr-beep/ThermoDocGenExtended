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
    # EUT Configuration prints ONE option per cell: the row carries two value cells, and
    # putting both options in the first crowds them into half the row. Mirrors RS_RI.
    _cfg = _s(form_data.get("eut_configuration"))
    ctx["eut_configuration_col_1"] = human_checkbox(_cfg, ["Tabletop"])
    ctx["eut_configuration_col_2"] = human_checkbox(_cfg, ["Floor standing"])
    # kept for compatibility with the single combined placeholder
    ctx["eut_configuration"] = human_checkbox(_cfg, ["Tabletop", "Floor standing"])

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


#: CRF Test Ports, in the order the document lists them.
_CRF_PORTS = ("Power Line", "Signal Line")


def _crf_ports(form_data):
    """The Test Port(s) chosen in the Test Specification, as canonical names.

    Normally one; a value naming both is honoured so the document can carry a row and a
    picture for each. Returns [] when nothing is selected, which leaves the observation
    table and the pictures exactly as the engineer left them."""
    raw = _s((form_data or {}).get("test_port")).lower()
    return [p for p in _CRF_PORTS if p.lower() in raw]


def _crf_freq_mhz(text):
    """The Frequency Range spec value as the observation table words it, in MHz:
    '150 kHz - 80 MHz' -> '0.15 to 80'. Anything that isn't a two-ended numeric range
    (e.g. 'ISM Band') is passed through untouched."""
    t = _s(text)
    if not t:
        return ""
    parts = re.split(r"\s*(?:-|–|—|to)\s*", t, maxsplit=1)
    if len(parts) != 2:
        return t
    out = []
    for part in parts:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(k|M|G)?Hz", part, re.I)
        if not m:
            return t
        val = float(m.group(1))
        unit = (m.group(2) or "M").upper()
        mhz = val / 1000.0 if unit == "K" else (val * 1000.0 if unit == "G" else val)
        out.append(("%f" % mhz).rstrip("0").rstrip("."))
    return "%s to %s" % (out[0], out[1])


def crf_normalize_procedure_breaks(values):
    """One blank line between the procedure's paragraphs, in place.

    A draft saved from the browser stores CRLF, and each '\\r\\n' renders as TWO <w:br/>
    in the document - so the '\\r\\n\\r\\n' before 'Power Line:' printed as three blank
    lines instead of one. Normalising to '\\n' and collapsing runs of blank lines fixes
    both the stored value and anything pasted in later. Idempotent."""
    if not isinstance(values, dict):
        return values
    txt = _s(values.get("test_procedure"))
    if not txt:
        return values
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    values["test_procedure"] = re.sub(r"\n{3,}", "\n\n", txt)
    return values


#: Test Level checkbox labels. The STORED value is the bare number ('3') - the observation
#: table's 'Test Level (Vrms)' column needs it that way - so the unit lives in the label.
_CRF_TEST_LEVELS = (("1", "1 Vrms"), ("3", "3 Vrms"), ("10", "10 Vrms"), ("Custom", "Custom___"))


def _crf_spec_checkboxes(form_data):
    """The Test Specification's ticked-box cells, as the reference document draws them.

    Test Port, Coupling Method, Test Level and EUT Configuration each occupy TWO cells -
    one option group per cell - so every row reads as a pair of boxes rather than a list
    crowded into half the row. Test Level repeats in both, matching the reference, since
    one level applies to whichever port was tested."""
    from .layout import human_checkbox, RunsXml, _box_run, _label_run
    out = {}
    _g = lambda k: _s((form_data or {}).get(k))

    out["immunity_test_requirement"] = human_checkbox(
        _g("immunity_test_requirement"), ["Basic", "Industrial", "Controlled", "Custom"])
    out["frequency_range"] = human_checkbox(
        _g("frequency_range"), ["150kHz-80MHz", "150kHz-230MHz", "Custom___"])

    out["test_port_col_1"] = human_checkbox(_g("test_port"), ["Power Line"])
    out["test_port_col_2"] = human_checkbox(_g("test_port"), ["Signal Line"])
    out["coupling_method_col_1"] = human_checkbox(_g("coupling_method"), ["CDN"])
    out["coupling_method_col_2"] = human_checkbox(_g("coupling_method"), ["EM Clamp"])
    out["eut_configuration_col_1"] = human_checkbox(_g("eut_configuration"), ["Tabletop"])
    out["eut_configuration_col_2"] = human_checkbox(_g("eut_configuration"), ["Floor standing"])

    # Test Level: two boxes per line, so the cell reads
    #   [ ] 1 Vrms [x] 3 Vrms
    #   [ ] 10 Vrms [ ] Custom___
    chosen = _g("test_level")
    rt = RunsXml()
    for i, (value, label) in enumerate(_CRF_TEST_LEVELS):
        rt.add(_box_run(chosen == value))
        rt.add(_label_run(" " + label + ("    " if i % 2 == 0 else "")))
        if i == 1:
            rt.add('<w:r><w:br/></w:r>')
    out["test_level_col_1"] = rt
    out["test_level_col_2"] = RunsXml(str(rt))
    return out


def _crf_build_context(form_data):
    """CRF docx context.

    The TEST OBSERVATION table is not free-form: every column except Observation is
    dictated by the Test Specification, and the row exists per Test Port selected there.
    The engineer's Observation letter is carried over by position, so choosing A/B/C/D
    survives a change of port or level."""
    out = _crf_spec_checkboxes(form_data)
    ports = _crf_ports(form_data)
    if not ports:
        return out
    freq = _crf_freq_mhz(form_data.get("frequency_range"))
    level = _s(form_data.get("test_level"))
    coupling = _s(form_data.get("coupling_method"))
    posted = _list(form_data, "test_observation_rows__c4[]")
    rows = []
    for i, port in enumerate(ports):
        rows.append({"c0": freq, "c1": port, "c2": level, "c3": coupling,
                     "c4": _s(posted[i]) if i < len(posted) else ""})
    out["test_observation_rows"] = rows
    return out


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


def _eft_ports(form_data):
    """Which Test Ports EFT was run on, from its single 'test_port' select.

    'Power Line' / 'Signal Line' / 'Both'. Nothing chosen counts as BOTH, so a half-filled
    draft keeps the whole procedure and both picture slots rather than losing content before
    the engineer has answered."""
    v = _s((form_data or {}).get("test_port")).lower()
    if not v or "both" in v:
        return {"power": True, "signal": True}
    return {"power": "power" in v, "signal": "signal" in v}


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
    # EUT Configuration prints ONE option per cell. The template used to carry the same
    # combined placeholder in both cells, so each printed the whole list.
    _cfg = _s(form_data.get("eut_configuration"))
    ctx["eut_configuration_col_1"] = human_checkbox(_cfg, ["Tabletop"])
    ctx["eut_configuration_col_2"] = human_checkbox(_cfg, ["Floor standing"])
    # kept for compatibility with the single combined placeholder
    ctx["eut_configuration"] = human_checkbox(_cfg, ["Tabletop", "Floor standing"])
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
    """Rebuild a Surge observation matrix the form posted (`kind` is a SLOT id: 'ac',
    'signal', 'dc', or a further instance of one, 'ac2' / 'signal3').
    Columns come from surge_obs_<slot>_cols (pipe-joined, e.g. 'CM L→PE 0°|...'),
    rows from surge_obs_<slot>_row_<ri> + cells surge_obs_<slot>_<ri>__c<ci>.
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


# TEST OBSERVATION carries as many tables as the engineer adds, in the order they were
# added, each one of these three kinds. A slot id is its kind plus an instance number for
# the second and later of that kind ('ac', 'ac2', 'ac3'), so the ids stay stable when one
# is removed and a draft written before this - which had exactly one of each - still reads.
_SURGE_OBS_KINDS = {"ac": "AC Power Line:", "signal": "Signal Line:", "dc": "DC Power Line:"}
_SURGE_SLOT_RE = re.compile(r"^(ac|signal|dc)(\d*)$")
# The paragraph in SURGE.docx that the generated tables replace.
_SURGE_OBS_ANCHOR = "[[observation tables]]"


def surge_slot_kind(slot):
    """'ac2' -> 'ac'; '' for anything that is not a slot id."""
    m = _SURGE_SLOT_RE.match(_s(slot))
    return m.group(1) if m else ""


def surge_obs_slots(form_data):
    """The ordered slot ids of the TEST OBSERVATION tables: what 'surge_obs_tables' says,
    or - for a draft saved before the section became a list - whichever of the three
    original slots carries data, in the order the document used to print them."""
    fd = form_data or {}
    listed = [s for s in (_s(fd.get("surge_obs_tables")).split(",")) if surge_slot_kind(s)]
    if listed:
        seen, out = set(), []
        for s in listed:                       # a duplicate id would print the same table twice
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out
    return [s for s in ("ac", "dc", "signal")
            if _s(fd.get("surge_obs_%s_cols" % s))]


def surge_obs_tables(form_data):
    """[{slot, kind, title, cols, rows}] for every table the engineer added, skipping any
    whose columns were never posted (an empty slot says nothing about the test)."""
    out = []
    for slot in surge_obs_slots(form_data):
        kind = surge_slot_kind(slot)
        data = _surge_obs(form_data, slot)
        if not data:
            continue
        out.append({"slot": slot, "kind": kind, "title": _SURGE_OBS_KINDS[kind],
                    "cols": data["cols"], "rows": data["rows"]})
    return out


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

    # Test Voltage (kV): the voltage options are always SHOWN, with 'NA' ticked when the
    # port was not tested - rather than replacing the whole cell with the words "Not
    # Applicable", which lost the reader's view of what the options were.
    POWER_NA = POWER + ["NA"]
    SIGNAL_NA = SIGNAL + ["NA"]

    def _tv(value, options_na, applicable):
        # cumulative: every option up to the level reached is ticked. 'NA' sits last, so a
        # real voltage never ticks it, and when the port is untested only 'NA' is ticked.
        return (cumulative_checkbox(_s(value), options_na) if applicable
                else human_checkbox("NA", options_na))

    ctx["tv_cm_power"] = _tv(form_data.get("surge_tv_cm_power"), POWER_NA, p_appl)
    ctx["tv_dm_power"] = _tv(form_data.get("surge_tv_dm_power"), POWER_NA, p_appl)
    ctx["tv_cm_signal"] = _tv(form_data.get("surge_tv_cm_signal"), SIGNAL_NA, s_appl)
    ctx["tv_dm_signal"] = _tv(form_data.get("surge_tv_dm_signal"), SIGNAL_NA, s_appl)
    # which ports were tested -> the finaliser drops the observation block of an untested one
    ctx["_surge_ports"] = {"power": p_appl, "signal": s_appl}

    # All four phases are ticked unless the engineer unticks some on the form. exact_checkbox
    # rather than human_checkbox: the latter matches substrings, so '0°' alone would also
    # tick '90°' and '180°'.
    from .layout import exact_checkbox as _exact
    ctx["coupling_phases"] = _exact(
        _s(form_data.get("coupling_phases")) or "0°, 90°, 180°, 270°", ["0°", "90°", "180°", "270°"])
    ctx["repetition_rate"] = human_checkbox(
        _s(form_data.get("repetition_rate")) or "60 Sec", ["60 Sec", "Custom"])

    ctx["eut_configuration_col_2"] = human_checkbox(_s(form_data.get("eut_configuration")), ["Tabletop"])
    ctx["eut_configuration_col_3"] = human_checkbox(_s(form_data.get("eut_configuration")), ["Floor standing"])

    ctx["monitoring_parameters"] = _s(form_data.get("monitoring_parameters")) or "No Error Message"

    # TEST OBSERVATION: every table the engineer added, in order — inserted post-render by
    # the generator, which builds each one to its kind's reference format.
    ctx["surge_obs_tables"] = surge_obs_tables(form_data)
    # The paragraph the generator replaces with those tables. Text rather than an empty
    # paragraph so it can be found after docxtpl has rendered.
    ctx["surge_obs_anchor"] = _SURGE_OBS_ANCHOR

    # Ambient / Humidity / Test Date / Tested by split into 1-3 per-day sections, as on RE
    # and CE. The field bases are the same, so RE's collector is reused verbatim; the
    # finaliser splits the value cell.
    ctx["_surge_meta"] = {"row_splits": _re_row_splits(form_data)}
    # Extra Test Setup pictures the engineer added, each with its own label. Shares RE's
    # slot naming (re_extra_photo_<n>) so the form repeater, the image-save allowlist and the
    # generator's resolver all work unchanged.
    ctx["re_extra_photos"] = _re_extra_photos(form_data)

    # Met Performance Criteria = the WORST code observed anywhere in the observation tables,
    # reported as a bare letter (a B2 counts as B). A hand-picked value on the form wins, so
    # the engineer can still override what was derived.
    _obs_cells = [c for _t in ctx["surge_obs_tables"]
                  for _row in _t.get("rows") or [] for c in (_row.get("cells") or [])]
    ctx["met_performance_criteria"] = (_s(form_data.get("met_performance_criteria"))
                                       or worst_performance_code(_obs_cells))
    # The procedure is written against the basic standard, not the product standards; this
    # also corrects a draft saved before that was true. Then drop the block of any port that
    # was not tested, so a Signal-Line paragraph cannot describe a test that never ran.
    ctx["test_procedure"] = _surge_filter_procedure(
        _surge_first_line_basic(
            _s(form_data.get("test_procedure")) or _s(ctx.get("test_procedure")),
            _DERIVED_BASIC_STANDARDS.get("SURGE", "")),
        p_appl, s_appl)

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


def _pfmf_methods(form_data):
    """Which coil-orientation column groups the TEST OBSERVATION table keeps.

    Test Method is 'Proximity method' (0/90/180/270), 'Immersion method' (X/Y/Z) or
    'Both'. Returns the set of group names; an unset value keeps both, so a half-filled
    draft still shows the whole table rather than losing columns."""
    v = _s((form_data or {}).get("test_method")).lower()
    if not v or "both" in v:
        return {"proximity", "immersion"}
    out = set()
    if "proximity" in v:
        out.add("proximity")
    if "immersion" in v:
        out.add("immersion")
    return out or {"proximity", "immersion"}


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


# ESD observation grids: prefix -> (how many rows the schema ships, the standard test-point
# names for those rows). The engineer can add rows on the form, so the real count comes from
# what was posted; these are the floor.
_ESD_OBS_GROUPS = {
    "ind": (8, ("HCP (0°)", "HCP (90°)", "HCP (180°)", "HCP (270°)",
                "VCP (0°)", "VCP (90°)", "VCP (180°)", "VCP (270°)")),
    "dir": (3, ()),
    "air": (3, ()),
}
_ESD_ROW_KEY_RE = re.compile(r"^(ind|dir|air)_r(\d+)_(?:name|c[1-6])$")


def esd_row_count(form_data, group):
    """How many rows the ESD grid `group` has: the schema's own, or more if the engineer
    added some.

    The form POSTS the count ('esd_rows_<group>'), and that wins: a draft save MERGES, so
    the cells of a row the engineer removed are still in the saved draft, and counting keys
    alone would bring the row back. Falling back to the highest '<group>_r<n>_' index keeps
    drafts saved before the count existed working (an added row that was left blank still
    posts empty strings, so the index is there either way).
    """
    base = _ESD_OBS_GROUPS.get(group, (0, ()))[0]
    fd = form_data or {}
    stated = _s(fd.get("esd_rows_%s" % group))
    if stated.isdigit():
        return max(base, int(stated))
    top = base
    for key in fd:
        m = _ESD_ROW_KEY_RE.match(_s(key))
        if m and m.group(1) == group:
            top = max(top, int(m.group(2)))
    return top


def _esd_row_has_content(fd, group, i):
    return bool(_s(fd.get("%s_r%d_name" % (group, i))) or
                any(_s(fd.get("%s_r%d_c%d" % (group, i, c))) for c in range(1, 7)))


def _esd_filled_groups(form_data):
    """Which ESD observation groups carry any data: {'ind','dir','air'} subset.

    The three tables are templated, so an untouched one would print as an empty grid. A
    group counts as filled when ANY of its cells (or, for the named groups, a test-point
    name) has content. `_re_row_splits`-style keys: ind_r<i>_c<j>, dir_r<i>_name, ...
    """
    fd = form_data or {}
    out = set()
    for grp in _ESD_OBS_GROUPS:
        for i in range(1, esd_row_count(fd, grp) + 1):
            # the Indirect rows carry their names from the standard, so only cells count there
            if any(_s(fd.get("%s_r%d_c%d" % (grp, i, c))) for c in range(1, 7)) or \
               (grp != "ind" and _s(fd.get("%s_r%d_name" % (grp, i)))):
                out.add(grp)
                break
    return out


def _esd_build_context(form_data):
    """ESD docx context: ticked EUT-Configuration cells, the two-line Indirect
    Contact Discharge cell (HCP line + VCP line), and all observation-table cell
    values (Indirect 8 fixed rows; Direct/Air 3 rows with editable names)."""
    from .layout import human_checkbox, cumulative_checkbox, RunsXml
    ctx = {}
    cfg = _s(form_data.get("eut_configuration"))
    ctx["eut_configuration_tabletop"] = human_checkbox(cfg, ["Tabletop"])
    ctx["eut_configuration_floor"] = human_checkbox(cfg, ["Floor standing"])

    # Discharge levels are CUMULATIVE: the EUT is stressed up to the chosen level, so
    # selecting +-4kV means +-2kV was applied too and both boxes tick. 'NA' and 'Custom'
    # are not levels, so they only ever tick themselves.
    ctx["direct_contact_discharge"] = cumulative_checkbox(
        _s(form_data.get("direct_contact_discharge")), ["±2kV", "±4kV", "±8kV", "Custom"])
    hcp = cumulative_checkbox(_s(form_data.get("indirect_hcp")),
                              ["NA", "±2kV", "±4kV", "±8kV", "Custom"])
    vcp = cumulative_checkbox(_s(form_data.get("indirect_vcp")),
                              ["±2kV", "±4kV", "±8kV", "Custom"])
    ctx["indirect_contact_discharge_hcp_vcp"] = RunsXml(str(hcp)).add('<w:r><w:br/></w:r>').add(str(vcp))

    # The three grids are row loops in the template ({%tr for r in esd_ind_rows %}), so the
    # engineer can add test points on the form. An ADDED row that was left completely blank
    # is dropped - it is a row they opened and did not use. The shipped rows always print,
    # blank or not, which is how this datasheet has always read.
    for grp, (base, names) in _ESD_OBS_GROUPS.items():
        rows = []
        for i in range(1, esd_row_count(form_data, grp) + 1):
            name = _s(form_data.get("%s_r%d_name" % (grp, i)))
            if not name and i <= len(names):
                name = names[i - 1]
            cells = [_s(form_data.get("%s_r%d_c%d" % (grp, i, c))) for c in range(1, 7)]
            if i > base and not _esd_row_has_content(form_data, grp, i):
                continue
            row = {"sno": str(len(rows) + 1), "name": name}
            row.update({"c%d" % c: cells[c - 1] for c in range(1, 7)})
            rows.append(row)
            # kept alongside the loop for anything still reading the flat keys
            ctx["%s_r%d_name" % (grp, i)] = name
            for c in range(1, 7):
                ctx["%s_r%d_c%d" % (grp, i, c)] = cells[c - 1]
        ctx["esd_%s_rows" % grp] = rows

    # Met Performance Criteria = the WORST code observed anywhere in the three grids, as a
    # bare letter: A (no degradation) is the least severe and D the worst, a sub-case such as
    # B2 counts as its letter, and 'NA' or a blank cannot outrank a real observation. Same
    # rule as SURGE. What was recorded decides; the field only keeps its own value when not
    # one cell has been filled in.
    ctx["met_performance_criteria"] = (
        worst_performance_code([_c for grp in _ESD_OBS_GROUPS
                                for _row in ctx.get("esd_%s_rows" % grp, [])
                                for _c in (_row.get("c%d" % i) for i in range(1, 7))])
        or _s(form_data.get("met_performance_criteria")))
    return ctx


def esd_met_criteria(form_data):
    """The Met Performance Criteria the ESD observation grids imply, or '' if nothing was
    recorded. Used on the FORM path too, so the value on screen is the one the document
    will carry."""
    cells = []
    for grp in _ESD_OBS_GROUPS:
        for i in range(1, esd_row_count(form_data, grp) + 1):
            cells.extend(_s((form_data or {}).get("%s_r%d_c%d" % (grp, i, c)))
                         for c in range(1, 7))
    return worst_performance_code(cells)


# RS_RI's TEST OBSERVATION: one row per frequency band, columns 1-2 the test level and the
# dwell time, then the eight polarisation/angle cells that carry a performance criterion.
_RS_RI_OBS_CELL_RE = re.compile(r"^(f_\d+_to_\d+)_col_(\d+)$")
_RS_RI_OBS_FIRST_CODE_COL = 3


def rs_ri_met_criteria(form_data):
    """The Met Performance Criteria the RS_RI observation table implies, or '' if nothing
    was recorded. Read from whatever bands the form posted rather than a fixed pair, so a
    band added to the schema is covered without a second edit here."""
    cells = []
    for key, value in (form_data or {}).items():
        m = _RS_RI_OBS_CELL_RE.match(_s(key))
        if m and int(m.group(2)) >= _RS_RI_OBS_FIRST_CODE_COL:
            cells.append(_s(value))
    return worst_performance_code(cells)


def _rs_ri_build_context(form_data):
    """RS Field Strength cells: render the ticked options with a fill-in value on
    Custom (e.g. '☐ 3V/m ☐ 10V/m ☐ 30V/m ☒ Custom 5V/m') — the generic checkbox
    can only tick a fixed option, not carry the custom numeric value.

    Also derives Met Performance Criteria from the observation table."""
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

    out = {
        "field_strength_col_1": fs(form_data.get("field_strength_col_1")),
        "field_strength_col_2": fs(form_data.get("field_strength_col_2")),
    }
    # What was recorded decides, exactly as on ESD; the field keeps its own value only when
    # not one observation cell has been filled in.
    out["met_performance_criteria"] = (rs_ri_met_criteria(form_data)
                                       or _s(form_data.get("met_performance_criteria")))
    return out


def build_context(schema, form_data, request_obj=None):
    """Map the posted form into the docxtpl context for this schema.

    `request_obj` is the parent Test Request, when the caller has it. A few values can only
    be resolved from the request rather than from the form - SURGE's Test Mode needs the
    named Functional Modes - and passing it lets a datasheet regenerated from an older draft
    pick those up instead of keeping whatever the draft happened to store. Optional, so
    callers without a request (the preview script) still work."""
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
    _size_keys = list(image_keys(schema))
    if schema.get("code") in ("RE", "SURGE", "HARMONIC", "CRF", "PFMF", "RS_RI", "EFT"):
        # extra test-setup pictures aren't in the schema, but their size is set the
        # same way by the image editor
        _size_keys += [s["key"] for s in re_extra_photo_slots(form_data)]
    for _ik in _size_keys:
        _w, _h = _s(form_data.get(_ik + "__wcm")), _s(form_data.get(_ik + "__hcm"))
        if _w and _h:
            try:
                _img_boxes[_ik] = (float(_w) * 10.0, float(_h) * 10.0)
            except ValueError:
                pass
    ctx["_img_boxes"] = _img_boxes
    # Upload-driven tables (columns come from the uploaded file), one per flagged section.
    for _ut in upload_tables(schema):
        ctx[_ut["key"]] = collect_upload_table(form_data, _ut["key"])
    # Product Standard: drop the RF-emissions entries on the datasheets they do not belong
    # to. Done once here rather than per datasheet, so the rule cannot be applied to the
    # prefill and then forgotten in the context a stored draft is re-rendered from.
    if (schema.get("code") or "").upper() in _NON_RF_EMISSION_CODES:
        ctx["product_standard"] = drop_emission_standards(ctx.get("product_standard"))
    if schema.get("code") == "RE":
        re_normalize_legacy_values(ctx)          # legacy draft values -> current format
        ctx["measurement_groups"] = _re_measurement_groups(form_data)
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
        freq = _s(form_data.get("frequency_range"))
        _prod_std = _s(form_data.get("product_standard"))
        ctx.update(_re_test_spec_columns(freq, _prod_std))
        ctx.update(_re_limit_tables(_prod_std,
                                    _s(form_data.get("classification_col_2")),
                                    freq))
        s30, s16 = _re_selected_ranges(freq)
        DASH = "-"
        # EUT Input Voltage is band-specific: the value prints in the selected band's
        # column ('-' in the other). Ambient Temperature / Relative Humidity are NOT
        # band-specific — they are split per test DAY by the RE finaliser below.
        _v = ctx.get("eut_input_voltage_frequency", "")
        ctx["eut_input_voltage_frequency_col_1"] = _v if s30 else DASH
        ctx["eut_input_voltage_frequency_col_2"] = _v if s16 else DASH
        # Test Procedure: resolve the EUT-support wording from the selected EUT
        # Configuration, then keep only the paragraphs for the selected range(s).
        ctx["test_procedure"] = _re_filter_procedure(
            _re_apply_support_mapping(ctx.get("test_procedure"),
                                      form_data.get("eut_configuration")),
            s30, s16)
        # Per-day Test Date / Ambient / Humidity + layout metadata for the finaliser.
        _days = _re_days(form_data)
        ctx["test_date"] = " / ".join(d["date"] for d in _days if d["date"]) or _fmt_ddmmyyyy(ctx.get("test_date"))
        ctx["_re_meta"] = {
            "show_30m_1g": s30,
            "show_1g_6g": s16,
            # non-empty when the Test Request asked for a custom range; the finaliser
            # renames the Test Setup photo captions to it
            "custom_range": re_custom_range(freq),
            "rotation_steps": _re_rotation_steps(_prod_std),
            "basic_standard": _s(form_data.get("basic_standard")) or _s(ctx.get("basic_standard")),
            "days": _days,
            "row_splits": _re_row_splits(form_data),
        }
    if schema.get("code") == "RS_RI":
        # Test Mode prints the mode NAMES ('Mode A, Mode B'), not the description the
        # requester typed. Only the Test Request knows them, so this needs the request the
        # caller passes in; a draft saved before this keeps working.
        if request_obj is not None:
            _rmodes = _re_functional_mode_names(request_obj)
            if _rmodes:
                ctx["test_mode"] = _rmodes
        # Extra Test Setup pictures beyond the four polarization x band slots, sharing RE's
        # slot naming so the form repeater, the image-save allowlist and the generator's
        # resolver all work unchanged.
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
        # Per-band, per-day sections for Ambient / Humidity / Test Date / Tested by. The
        # finaliser splits the matching value CELL, so both bands stay on the same row.
        ctx["_rs_ri_meta"] = {"row_splits": _rs_ri_row_splits(form_data)}
        # EUT Configuration prints as two ticked boxes, one per option, the way Frequency
        # Range already does - a cross on the one chosen and an empty box on the other.
        from .layout import human_checkbox as _hcb
        _cfg = _s(form_data.get("eut_configuration")) or _s(ctx.get("eut_configuration"))
        ctx["eut_configuration_col_1"] = _hcb(_cfg, ["Tabletop"])
        ctx["eut_configuration_col_2"] = _hcb(_cfg, ["Floor standing"])
        # ... and the procedure's EUT-support wording follows that choice: a non-conductive
        # table of 0.8 m for Tabletop, an insulation support of 0.1 m for Floor standing.
        # The template ships the combined "0.8/0.1m" placeholder; the mapping is idempotent
        # so a draft saved with either wording is corrected too.
        ctx["test_procedure"] = _re_apply_support_mapping(ctx.get("test_procedure"), _cfg)
        # A draft saved before the rule below holds '0 - Initial state'; the spec row shows
        # the state NUMBER only, so normalise whatever the draft carries.
        re_normalize_legacy_values(ctx)
        # TEST OBSERVATION's "Dwell time (s)" column carries the unit in its heading, so the
        # cell holds the bare number. The form mirrors it that way now, but a draft saved
        # earlier holds the Test-Specification wording ("3 seconds") - strip it here so
        # regenerating an old draft is corrected too.
        for _k in list(ctx):
            if re.match(r"^f_\d+_to_\d+_col_2$", _k):
                _m = re.search(r"-?\d+(?:\.\d+)?", _s(ctx.get(_k)))
                ctx[_k] = _m.group(0) if _m else ""
    if schema.get("code") == "HARMONIC":
        ctx.update(_harmonic_build_context(form_data))
        # The procedure's opening sentence mirrors the Basic Standard row, so a draft that
        # stored the product standards there (or an older basic standard) is corrected.
        normalize_procedure_basic(ctx)
        # HARMONIC is always run on the mains supply, so 230 V, 50 Hz is THE value, not a
        # fallback for a blank: it overwrites whatever the form posts. Needed here as well
        # as on the form because generate-final re-renders from the STORED draft, which can
        # still carry a request's multi-supply text ('230 V, 50 Hz; 120 V, 60 Hz').
        # The spec row shows the state NUMBER only; a draft saved before that rule holds
        # '0 - Initial state'. Applied on the FULL context - eut_modification_state comes
        # from the scalar loop above, not from _harmonic_build_context.
        re_normalize_legacy_values(ctx)
        # Test Mode prints the mode NAMES ('Mode A, Mode B'), not the description the
        # requester typed for each. Only the Test Request knows them, so this needs the
        # request the caller passes in; drafts saved before this keep working.
        _normalize_mains_and_modes(ctx, request_obj)
        # EUT support: a wooden table at 0.8m height for Tabletop, an insulation support at
        # 0.1m height for Floor standing - whichever the spec table shows.
        ctx["test_procedure"] = _harmonic_apply_support_mapping(
            ctx.get("test_procedure"), ctx.get("eut_configuration"))
        # The harmonic-current RESULT grids print 'NA' in an empty cell rather than leaving
        # a blank box: the imported measurement rows and the Average/Maximum results.
        _harmonic_fill_na(ctx.get("harmonic_rows"))
        _harmonic_fill_na(ctx.get("avgmax_rows"))
        # Ambient / Humidity / Test Date / Tested by split into 1-3 per-day sections; the
        # field bases match RE's, so its collector is reused. The finaliser splits the cell.
        ctx["_harmonic_meta"] = {"row_splits": _re_row_splits(form_data)}
        # Extra Test Setup pictures, sharing RE's slot naming so the form repeater, the
        # image-save allowlist and the generator's resolver all work unchanged.
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
    if schema.get("code") == "VOLTAGEDIPS":
        ctx.update(_vdips_build_context(form_data))
        # The spec row shows the state NUMBER only; a draft saved before that rule holds
        # '0 - Initial state'. Applied on the FULL context.
        re_normalize_legacy_values(ctx)
        # The procedure's opening sentence names the BASIC standard, replacing the
        # '<Standard name>' placeholder a draft may still carry.
        normalize_procedure_basic(
            ctx, _s(ctx.get("basic_standard")) or _DERIVED_BASIC_STANDARDS.get("VOLTAGEDIPS", ""))
        # Test Mode prints the mode NAMES ('Mode A, Mode B'), not the description the
        # requester typed for each.
        if request_obj is not None:
            _vmodes = _re_functional_mode_names(request_obj)
            if _vmodes:
                ctx["test_mode"] = _vmodes
        # Ambient / Humidity / Test Date / Tested by split into the 1-3 per-day sections
        # the engineer chose; the finaliser divides the value cell.
        ctx["_vdips_meta"] = {"row_splits": _re_row_splits(form_data)}
    if schema.get("code") == "EFT":
        ctx.update(_eft_build_context(form_data))
        # The spec row shows the modification state NUMBER only.
        re_normalize_legacy_values(ctx)
        # Only the selected Test Port's block belongs in the procedure. EFT records the
        # choice in ONE select ('Power Line' / 'Signal Line' / 'Both'), so the flags are
        # derived from it and handed to the same filter SURGE uses.
        _ep = _eft_ports(form_data)
        ctx["test_procedure"] = _surge_filter_procedure(
            ctx.get("test_procedure"), _ep["power"], _ep["signal"])
        # ... and the finaliser drops the picture of a port that was not tested, and splits
        # the per-day cells.
        ctx["_eft_meta"] = {"ports": _ep, "row_splits": _re_row_splits(form_data)}
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
        # The procedure's opening sentence names the BASIC standard, replacing the
        # '<Standard name>' placeholder a draft may still carry.
        normalize_procedure_basic(
            ctx, _s(ctx.get("basic_standard")) or _DERIVED_BASIC_STANDARDS.get("EFT", ""))
        # Test Mode prints the mode NAMES ('Mode A, Mode B'), not the description the
        # requester typed for each.
        if request_obj is not None:
            _emodes = _re_functional_mode_names(request_obj)
            if _emodes:
                ctx["test_mode"] = _emodes
    if schema.get("code") == "SURGE":
        ctx.update(_surge_build_context(form_data))
        # The spec row shows the state NUMBER only; a draft saved before that holds
        # '0 - Initial state'. Applied here, on the FULL context - eut_modification_state is
        # set by the scalar loop above, not by _surge_build_context.
        re_normalize_legacy_values(ctx)
        # Test Mode prints the mode NAMES ('Mode A, Mode B'), not the description the
        # requester typed for each. Only the Test Request knows them, so this needs the
        # request that the caller passes in; drafts saved before this keep working.
        if request_obj is not None:
            _modes = _re_functional_mode_names(request_obj)
            if _modes:
                ctx["test_mode"] = _modes
    if schema.get("code") == "PFMF":
        ctx.update(_pfmf_build_context(form_data))
        # The spec row shows the modification state NUMBER only.
        re_normalize_legacy_values(ctx)
        # Test Mode prints the mode NAMES ('Mode A, Mode B').
        if request_obj is not None:
            _pmodes = _re_functional_mode_names(request_obj)
            if _pmodes:
                ctx["test_mode"] = _pmodes
        # TEST OBSERVATION keeps only the chosen Test Method's coil columns, and the
        # per-day sections go on their existing rows; both are applied by the finaliser.
        ctx["_pfmf_meta"] = {"methods": _pfmf_methods(form_data),
                             "row_splits": _re_row_splits(form_data)}
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
    if schema.get("code") == "ESD":
        ctx.update(_esd_build_context(form_data))
        # The spec row shows the modification state NUMBER only.
        re_normalize_legacy_values(ctx)
        # The procedure's opening sentence names the BASIC standard.
        normalize_procedure_basic(
            ctx, _s(ctx.get("basic_standard")) or _DERIVED_BASIC_STANDARDS.get("ESD", ""))
        # Per-day sections for the five environment rows, and which observation groups the
        # engineer actually filled - the finaliser drops the tables of the empty ones.
        ctx["_esd_meta"] = {"row_splits": _re_row_splits(form_data),
                            "filled": _esd_filled_groups(form_data)}
    if schema.get("code") == "RS_RI":
        ctx.update(_rs_ri_build_context(form_data))
    if schema.get("code") == "CRF":
        ctx.update(_crf_build_context(form_data))
        # The spec row shows the modification state NUMBER only.
        re_normalize_legacy_values(ctx)
        # Test Mode prints the mode NAMES ('Mode A, Mode B').
        if request_obj is not None:
            _cmodes = _re_functional_mode_names(request_obj)
            if _cmodes:
                ctx["test_mode"] = _cmodes
        # A CRLF draft would print three blank lines before 'Power Line:'.
        crf_normalize_procedure_breaks(ctx)
        # The procedure describes the port that was tested. The template ships only the
        # Power Line paragraph, so a signal-line test printed the wrong setup entirely -
        # a CDN on the power lines instead of an EM clamp on the signal lines. The Signal
        # Line block lives in procedures.PORT_BLOCKS; it is added when that port is the one
        # under test, and then the blocks for the untested port are dropped by the same
        # filter SURGE and EFT use (it reads a block by its heading).
        _cport = _s(form_data.get("test_port"))
        if _cport and not _s(form_data.get("test_procedure_manual")):
            from . import procedures as _procedures
            _ctxt = _procedures.ensure_port_block("CRF", ctx.get("test_procedure"), _cport)
            _low = _cport.lower()
            ctx["test_procedure"] = _surge_filter_procedure(
                _ctxt, "power" in _low or "both" in _low, "signal" in _low or "both" in _low)
            # Coupling Method follows the port unless the engineer chose otherwise.
            if not _s(form_data.get("coupling_method")):
                _cm = _procedures.coupling_default("CRF", _cport)
                if _cm:
                    ctx["coupling_method"] = _cm
        # The Test Port decides which Test Setup picture and which TEST OBSERVATION block
        # survive; Ambient / Humidity / Test Date / Tested by split into per-day sections.
        ctx["_crf_meta"] = {"ports": _crf_ports(form_data),
                            "row_splits": _re_row_splits(form_data)}
        # Extra Test Setup pictures, sharing RE's slot naming so the form repeater, the
        # image-save allowlist and the generator's resolver all work unchanged.
        ctx["re_extra_photos"] = _re_extra_photos(form_data)
    if schema.get("code") == "VOLTAGEFLICKER":
        # Same Test Specification rules as HARMONIC - it is the other mains-supply test:
        # the spec row shows the modification state NUMBER only, the supply is fixed at
        # 230 V, 50 Hz, and Test Mode prints the mode NAMES.
        re_normalize_legacy_values(ctx)
        _normalize_mains_and_modes(ctx, request_obj)
        # Ambient / Humidity / Test Date / Tested by split into the 1-3 per-day sections the
        # engineer chose. Field bases match RE's, so its collector is reused; the finaliser
        # splits the value cell.
        ctx["_flicker_meta"] = {"row_splits": _re_row_splits(form_data)}
    # The EUT-support wording follows EUT Configuration on every datasheet that has a rule
    # (procedures.SUPPORT_RULES), not just the three that grew one by hand. Applied here so a
    # branch above cannot miss it and a draft saved before the rule existed is corrected;
    # idempotent, so the datasheets that already resolved it above are unaffected.
    #
    # A hand-edited procedure is left alone, exactly as the form leaves it: once the engineer
    # has taken the text over, rewriting a phrase inside it would edit their words.
    if not _s(form_data.get("test_procedure_manual")):
        from . import procedures as _procedures
        ctx["test_procedure"] = _procedures.apply_support(
            (schema.get("code") or "").upper(), ctx.get("test_procedure"),
            _s(form_data.get("eut_configuration")))
    # LAST, so nothing a per-datasheet branch above added can slip past: every date in the
    # document prints DD/MM/YYYY, whatever the form or the equipment master supplied.
    normalize_context_dates(schema, ctx)
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


def _re_test_spec_columns(freq, product_standard=""):
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
    s30, s16 = _re_selected_ranges(freq)     # 'Both' -> both columns carry real values
    DASH = "-"

    def band(k1, k2, d1="", d2=""):
        v30, v16 = sd.get(k1, d1), sd.get(k2, d2)
        return (v30 if s30 else DASH), (v16 if s16 else DASH)

    cols = {}
    _custom = re_custom_range(freq)
    if _custom:
        # Neither standard option applies, so print the requested range as plain text
        # across the value area instead of two tick-boxes.
        from docxtpl import RichText
        cols["frequency_range_col_1"] = RichText(_custom)
        cols["frequency_range_col_2"] = RichText("")
    else:
        # real ticked/unticked checkboxes (RunsXml -> {{r ... }}), same rendering as Classification
        cols["frequency_range_col_1"] = human_checkbox("30MHz-1GHz" if s30 else "", ["30MHz-1GHz"])
        cols["frequency_range_col_2"] = human_checkbox("1GHz-6GHz" if s16 else "", ["1GHz-6GHz"])
    cols["resolution_bandwidth_col_1"], cols["resolution_bandwidth_col_2"] = band("resolution_bandwidth_col_1", "resolution_bandwidth_col_2", "120k", "1M")
    cols["video_bandwidth_col_1"], cols["video_bandwidth_col_2"] = band("video_bandwidth_col_1", "video_bandwidth_col_2", "1M", "3M")
    cols["step_size_col_1"], cols["step_size_col_2"] = band("step_size_col_1", "step_size_col_2", "40k", "400k")
    # Turn-table rotation step: driven by the standard family (15deg CISPR / 22.5deg
    # CFR), not by the frequency band. The value cell is split into one section per
    # applicable family by the RE finaliser.
    _rot = _re_rotation_steps(product_standard) or ["15°"]
    # Both frequency columns read identically (one section per applicable family);
    # the RE finaliser splits the row into those sections.
    cols["turn_table_rotation_step_col_1"] = _rot[0]
    cols["turn_table_rotation_step_col_2"] = _rot[0] if s16 else DASH
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


def re_normalize_legacy_values(values):
    """Coerce legacy RE values saved in older drafts to the current format, so a
    resumed draft doesn't resurrect them (e.g. '0 - Initial state' -> '0')."""
    if not isinstance(values, dict):
        return values
    v = _s(values.get("eut_modification_state"))
    m = re.match(r"^\s*(\d+)\s*[-–—].*$", v)
    if m:
        values["eut_modification_state"] = m.group(1)
    return values


#: Emissions-only standards. They are named on the Test Request because one request
#: covers emissions and immunity together, but an immunity datasheet must not cite them.
_EMISSION_ONLY_STANDARDS = ("fcc", "ices")
#: Datasheets whose Product Standard row drops the emissions-only entries.
#:
#: Everything except CE and RE. Those two ARE radio-frequency emissions tests - conducted
#: and radiated - so FCC Part 15 and ICES-001 are genuinely their product standards. The
#: rest are immunity tests, or mains-emissions ones (HARMONIC / VOLTAGEFLICKER), where an
#: RF emissions limit says nothing about the test being reported.
_NON_RF_EMISSION_CODES = ("RS_RI", "SURGE", "HARMONIC", "CRF", "EFT", "ESD",
                          "PFMF", "VOLTAGEDIPS", "VOLTAGEFLICKER")


def drop_emission_standards(raw):
    """The Product Standard list with the emissions-only entries removed.

    'IEC 61326-1 : 2020; EN 61326-1 : 2021; FCC Subpart 15B : 2024; ICES-001 Issue 5 : 2020'
    -> 'IEC 61326-1 : 2020; EN 61326-1 : 2021'

    Separators are preserved: the request joins with '; ', RE's display mapping with ' & '.
    Idempotent, so a resumed draft is corrected too."""
    txt = _s(raw)
    if not txt:
        return txt
    sep = "; " if ";" in txt else (" & " if "&" in txt else "; ")
    parts = [p.strip() for p in re.split(r"\s*[;&]\s*", txt) if p.strip()]
    kept = [p for p in parts
            if not any(tok in p.lower() for tok in _EMISSION_ONLY_STANDARDS)]
    return sep.join(kept) if kept else txt      # never blank the row entirely


def _re_product_standard_display(raw):
    """Map the request's product standards to the client's canonical display text
    (admin-editable via RE.product_standard_display in the fixed-values table).
    Drops the intake 'Other' placeholder and keeps unmapped values verbatim, so the
    datasheet never prints just 'Other' when real standards were selected."""
    from .fixed_store import get_fixed_values, SEED_FIXED_VALUES
    # DB fixed-values row (admin-editable) wins; fall back to the code seed so a
    # pre-existing DB row that predates this mapping still canonicalizes correctly.
    mapping = ((get_fixed_values("RE") or {}).get("product_standard_display")
               or SEED_FIXED_VALUES.get("RE", {}).get("product_standard_display", {}) or {})
    out = []
    for part in re.split(r"[;\n|,/&]+", _s(raw)):
        p = part.strip()
        if not p:
            continue
        key = re.sub(r"[^a-z0-9]", "", p.lower())
        if key in ("other", "others", "pleasespecify", "otherpleasespecify"):
            continue
        disp = next((label for tok, label in mapping.items() if tok and tok in key), p)
        if disp not in out:
            out.append(disp)
    return " & ".join(out)


def _re_test_procedure(template, basics_text, cfg, fams):
    """Resolve the RE test-procedure boilerplate: EUT support (non-conductive table
    0.8 m for Tabletop / insulation support 0.1 m for Floor standing) and rotation
    step (15deg CISPR / 22.5deg CFR / both), plus the basic standard name(s)."""
    txt = _s(template)
    if basics_text:
        txt = txt.replace("<Standard name>", basics_text.replace("; ", ", "))
    return _re_apply_support_mapping(txt, cfg)


#: Basic standard per test code, for the datasheets whose mapping is not yet in the
#: admin-editable basic_standard_map table (RE / HARMONIC / VOLTAGEFLICKER / CE use that).
_DERIVED_BASIC_STANDARDS = {
    "VOLTAGEDIPS": "IEC 61000-4-11:2020 & EN 61000-4-11:2020",
    "EFT": "IEC 61000-4-4:2012 & EN 61000-4-4:2012",
    "SURGE": "IEC 61000-4-5:2014+A1:2017 & EN 61000-4-5:2014+A1:2017",
    "CRF": "IEC 61000-4-6:2023 & EN 61000-4-6:2023",
    "RS_RI": "EN 61000-4-3:2020 & IEC 61000-4-3:2020",
    "PFMF": "IEC 61000-4-8:2009 & EN 61000-4-8:2010",
    "ESD": "IEC 61000-4-2:2008 & EN 61000-4-2:2009",
}

#: Observation codes ranked least to most severe. Sub-cases (B1, C2, ...) collapse to their
#: letter, so the worst code across a matrix is reported as a plain A/B/C/D.
_PERF_ORDER = ("A", "B", "C", "D")

#: HARMONIC and VOLTAGEFLICKER are both measured on the mains supply, so this is THE value
#: of their EUT Input Voltage & Frequency row - it overwrites whatever the Test Request or a
#: saved draft carries, rather than only standing in for a blank.
_MAINS_DEFAULT_SUPPLY = "230 V, 50 Hz"
#: ... and these are the datasheets it applies to.
_MAINS_SUPPLY_CODES = ("HARMONIC", "VOLTAGEFLICKER")


def worst_performance_code(codes):
    """The most severe observation code in `codes`, as a bare letter.

    'A' is the least severe (no degradation) and 'D' the worst (permanent damage). Sub-cases
    such as B1/C3 count as their letter, and anything unrecognised is ignored, so a stray
    blank or 'NA' cannot outrank a real observation. Returns '' when nothing was recorded."""
    worst = -1
    for c in codes or ():
        letter = _s(c).strip().upper()[:1]
        if letter in _PERF_ORDER:
            worst = max(worst, _PERF_ORDER.index(letter))
    return _PERF_ORDER[worst] if worst >= 0 else ""


def _surge_first_line_basic(text, basic):
    """Point the procedure's opening sentence at the BASIC standard.

    The template ships '<Standard name>', which the generic prefill fills with the PRODUCT
    standards - the procedure is written against the basic standard instead. Rewrites the
    first sentence, so a draft saved with the wrong list is corrected as well.

    Also collapses runs of blank lines to a single one: a draft saved with CRLF endings came
    back with three blank lines after the opening sentence, a gap wide enough to read as if
    the sentence had been repeated."""
    txt = _s(text)
    if not txt:
        return txt
    if basic:
        txt = re.sub(r"^(The test procedure was in accordance with\s+)[^\n]*?\.",
                     lambda m: m.group(1) + basic + ".", txt, count=1)
    return re.sub(r"(?:\r\n|\r|\n){3,}", "\n\n", txt)


def _surge_filter_procedure(text, power_applicable, signal_applicable):
    """Keep only the Test Procedure sections for the ports actually tested.

    The template carries a 'Power Line:' block and a 'Signal Line:' block; with Signal Line
    marked Not Applicable its paragraph should not be in the document at all. Each heading
    owns the paragraphs that follow it until the next heading, so both go together.

    If NEITHER port reads as applicable, nothing is stripped: that is far more likely to mean
    the field has not been filled in yet than that no port was tested, and silently deleting
    the whole procedure would be worse than leaving it complete.
    """
    from .procedures import block_port as _block_port
    txt = _s(text)
    if not txt or not (power_applicable or signal_applicable):
        return txt
    blocks = re.split(r"\n\s*\n", txt)
    keep, section = [], None
    for b in blocks:
        # The heading may stand alone in its own block (SURGE) or open the paragraph it
        # belongs to (EFT: 'Power Line: The power supply to the EUT was fed ...'), so match
        # on the start of the block's FIRST LINE rather than on the whole block.
        # procedures.block_port owns what counts as a port heading, so this filter and
        # the admin page's preview cannot disagree about where a block belongs.
        _port = _block_port(b)
        if _port == "Power Line":
            section = "power"
        elif _port == "Signal Line":
            section = "signal"
        elif section is None:
            keep.append(b)                     # preamble: always kept
            continue
        if section == "power" and not power_applicable:
            continue
        if section == "signal" and not signal_applicable:
            continue
        keep.append(b)
    return "\n\n".join(b for b in keep if b.strip())


def normalize_procedure_basic(values, basic=None):
    """Point a procedure's opening sentence at the basic standard, in place.

    `basic` defaults to the dict's OWN 'basic_standard' value, so the sentence always mirrors
    what the Test Specification table shows - including a value the engineer edited or one an
    older draft carries, rather than a constant that might have moved on.

    Needed on the FORM path as well as at generation: a draft saved before this rule holds the
    PRODUCT standards there, and a draft value overrides the prefill, so fixing the prefill
    alone left the old sentence on screen. Idempotent."""
    if not isinstance(values, dict):
        return values
    txt = _s(values.get("test_procedure"))
    if not txt:
        return values
    std = _s(basic) or _s(values.get("basic_standard"))
    values["test_procedure"] = _surge_first_line_basic(txt, std)
    return values


def _normalize_mains_and_modes(values, request_obj=None):
    """The two Test Specification rules shared by the mains-supply datasheets, in place.

    Needed because a saved draft OVERRIDES the prefill on the form path, so correcting the
    prefill alone leaves an already-saved draft showing the old values:

      * EUT Input Voltage & Frequency - the test is always run on the mains supply, so
        230 V, 50 Hz replaces whatever the draft carries. This is deliberately an
        overwrite, not a blank-fill: it is the value, not a fallback.
      * Test Mode - the mode NAMES ('Mode A, Mode B'), not the description the requester
        typed. Left alone when the request yields no modes, so the draft keeps its text
        rather than being blanked.

    EUT Modification state is handled by re_normalize_legacy_values(). Idempotent."""
    if not isinstance(values, dict):
        return values
    values["eut_input_voltage_frequency"] = _MAINS_DEFAULT_SUPPLY
    if request_obj is not None:
        modes = _re_functional_mode_names(request_obj)
        if modes:
            values["test_mode"] = modes
    return values


def flicker_normalize_values(values, request_obj=None):
    """VOLTAGEFLICKER's form-path corrections: the mains supply and Test Mode rules, plus
    its EUT-support wording - a wooden table of 0.8 m height for Tabletop, an insulation
    support of 0.1 m height for Floor standing."""
    if not isinstance(values, dict):
        return values
    _normalize_mains_and_modes(values, request_obj)
    if not _s(values.get("test_procedure_manual")):
        values["test_procedure"] = _harmonic_apply_support_mapping(
            values.get("test_procedure"), values.get("eut_configuration"), "VOLTAGEFLICKER")
    return values


def harmonic_normalize_values(values, request_obj=None):
    """HARMONIC's form-path corrections: the shared mains supply / Test Mode rules, plus
    its own EUT-support wording."""
    if not isinstance(values, dict):
        return values
    _normalize_mains_and_modes(values, request_obj)
    # The procedure's EUT-support phrase follows EUT Configuration. A draft saved with the
    # combined 'wooden table/insulation support at 0.8/0.1m height' placeholder - or with
    # the other option's wording - is corrected to match what the form is showing.
    values["test_procedure"] = _harmonic_apply_support_mapping(
        values.get("test_procedure"), values.get("eut_configuration"))
    return values


def surge_normalize_procedure(values):
    """SURGE flavour of normalize_procedure_basic(): falls back to SURGE's derived basic
    standard when the dict has no basic_standard of its own."""
    return normalize_procedure_basic(
        values, _s((values or {}).get("basic_standard")) or _DERIVED_BASIC_STANDARDS.get("SURGE", ""))


def _re_apply_support_mapping(text, cfg):
    """RE / RS_RI: a non-conductive table of 0.8 m for Tabletop, an insulation support of
    0.1 m for Floor standing.

    The wording now lives in procedures.SUPPORT_RULES with every other datasheet's, so the
    form, the document and the admin page read one table. Kept as a name because several
    call sites use it, and both datasheets share RE's rule.
    """
    from .procedures import apply_support
    return apply_support("RE", _s(text), cfg)


#: HARMONIC's EUT-support wording. Its procedure ships the combined
#: 'wooden table/insulation support at 0.8/0.1m height' placeholder and uses a WOODEN
#: table, so it needs its own phrases rather than RE/RS_RI's non-conductive-table ones.
_HARMONIC_SUPPORT_TABLETOP = "wooden table at 0.8m height"
_HARMONIC_SUPPORT_FLOOR = "insulation support at 0.1m height"
#: Longest first, so the combined placeholder is consumed before the single wordings.
_HARMONIC_SUPPORT_PHRASES = (
    "wooden table/ insulation support at 0.8/0.1 m height",
    "wooden table/insulation support at 0.8/0.1 m height",
    "wooden table/ insulation support at 0.8/0.1m height",
    "wooden table/insulation support at 0.8/0.1m height",
    "wooden table at 0.8 m height",
    "wooden table at 0.8m height",
    "insulation support at 0.1 m height",
    "insulation support at 0.1m height",
)


def _harmonic_apply_support_mapping(text, cfg, code="HARMONIC"):
    """HARMONIC / VOLTAGEFLICKER: a wooden table of 0.8 m height for Tabletop, an
    insulation support of 0.1 m height for Floor standing.

    `code` because FLICKER ships 'of 0.8/0.1m height' where HARMONIC ships 'at' - passing
    HARMONIC's phrase list at a Flicker procedure matched nothing, which is why Flicker's
    wording never followed the dropdown. Each datasheet now brings its own list.
    """
    from .procedures import apply_support
    return apply_support(code, _s(text), cfg)


def _harmonic_fill_na(rows, value="NA"):
    """Empty cells in HARMONIC's harmonic-current RESULT grids print 'NA' rather than a
    blank box. Applied to the rows that survive the 'row carries some content' filter, so
    a wholly empty row is still dropped instead of becoming a row of NAs."""
    for row in rows or []:
        for k, v in list(row.items()):
            if not _s(v):
                row[k] = value
    return rows


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
            _ps = (_re_product_standard_display(standard) if _code == "RE" else standard) or standard
            # RS_RI is an immunity test: the request's FCC / ICES entries are emissions
            # standards and must not appear on its Test Specification.
            pre[f["key"]] = drop_emission_standards(_ps) if _code in _NON_RF_EMISSION_CODES else _ps
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
                pre[f["key"]] = _DERIVED_BASIC_STANDARDS.get(_bcode, "Sysmex")
        elif "monitoring_parameters" in k:
            # Pull from the Test Request; if the TR has nothing, fall back to the
            # schema's constant default so the field is never blank on the datasheet.
            pre[f["key"]] = monitoring or default
        elif "voltage" in k and "frequency" in k:
            # HARMONIC is always run on the mains supply, so 230 V, 50 Hz is the value the
            # field starts at - it WINS over whatever the Test Request carries, rather than
            # only filling a blank. The engineer can still type over it on the form.
            pre[f["key"]] = _MAINS_DEFAULT_SUPPLY if _code in _MAINS_SUPPLY_CODES else vf
        elif k == "test_mode":
            # RE prints the mode NAMES ('Mode A, Mode B'), not the descriptions the
            # requester typed for each one. Other datasheets keep the full text.
            pre[f["key"]] = (_re_functional_mode_names(request_obj) or test_mode) \
                if _code in ("RE", "SURGE", "HARMONIC", "VOLTAGEFLICKER", "VOLTAGEDIPS",
                             "CRF", "PFMF", "RS_RI", "EFT") \
                else test_mode
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
            # RE and RS_RI: engineers want just the state number (0), not "0 - Initial
            # state" - the description already has its own column in 1.2.
            pre[f["key"]] = "0" if _code in ("RE", "RS_RI", "SURGE", "HARMONIC",
                                             "VOLTAGEFLICKER", "VOLTAGEDIPS", "CRF",
                                             "PFMF", "EFT", "ESD") \
                else "0 - Initial state"
        elif _code == "RE" and k == "test_procedure":
            try:
                from .fixed_store import basic_standard as _bs
                _basics = _bs(standard, "RE")
            except Exception:
                _basics = ""
            from . import re_logic as _rl
            pre[f["key"]] = _re_test_procedure(
                f.get("default", ""), _basics, cfg, _rl.families(standard))
        elif _code == "HARMONIC" and k == "test_procedure":
            # '<Standard name>' in the opening sentence means the BASIC standard, which for
            # HARMONIC comes from the admin-editable basic_standard_map table - so the
            # sentence matches whatever the Basic Standard row shows.
            try:
                from .fixed_store import basic_standard as _bs
                _hb = _bs(standard, "HARMONIC")
            except Exception:
                _hb = ""
            # ... and the EUT-support phrase follows the EUT Configuration, the way RE's does.
            pre[f["key"]] = _harmonic_apply_support_mapping(
                _s(f.get("default", "")).replace("<Standard name>", _hb), cfg)
        elif _code == "ESD" and k == "test_procedure":
            # '<Standard name>' in the opening sentence means the BASIC standard
            # (IEC/EN 61000-4-2), not the product standards.
            pre[f["key"]] = _s(f.get("default", "")).replace(
                "<Standard name>", _DERIVED_BASIC_STANDARDS.get("ESD", ""))
        elif _code == "EFT" and k == "test_procedure":
            # '<Standard name>' in the opening sentence means the BASIC standard
            # (IEC/EN 61000-4-4), not the product standards.
            _full = _s(f.get("default", "")).replace(
                "<Standard name>", _DERIVED_BASIC_STANDARDS.get("EFT", ""))
            # The UNFILTERED text is kept alongside so the form can rebuild the procedure
            # when a Test Port is switched back on - filtering the stored value would delete
            # that block for good. Same arrangement as SURGE.
            pre["test_procedure_full"] = _full
            pre[f["key"]] = _full
        elif _code == "CRF" and k == "test_procedure":
            # The template ships the Power Line block only; the Signal Line one lives in
            # procedures.PORT_BLOCKS. The form is given BOTH (as test_procedure_full) so
            # switching Test Port rebuilds the right paragraph - the same arrangement SURGE
            # and EFT use - and the visible text starts filtered to the port on the request.
            from . import procedures as _procedures
            _full = _procedures.ensure_port_block(
                "CRF", _s(f.get("default", "")), "Signal Line")
            pre["test_procedure_full"] = _full
            _cp = _s(pre.get("test_port")).lower()
            pre[f["key"]] = _surge_filter_procedure(
                _full, ("power" in _cp) or not _cp, ("signal" in _cp) or not _cp)
        elif _code == "VOLTAGEDIPS" and k == "test_procedure":
            # '<Standard name>' in the opening sentence means the BASIC standard
            # (IEC/EN 61000-4-11), not the product standards the generic path would use.
            pre[f["key"]] = _s(f.get("default", "")).replace(
                "<Standard name>", _DERIVED_BASIC_STANDARDS.get("VOLTAGEDIPS", ""))
        elif _code == "SURGE" and k == "test_procedure":
            # '<Standard name>' in the opening sentence means the BASIC standard here, not
            # the product standards the generic substitution would drop in.
            _full = _s(f.get("default", "")).replace(
                "<Standard name>", _DERIVED_BASIC_STANDARDS.get("SURGE", ""))
            # The UNFILTERED text is kept alongside, so the form can rebuild the procedure
            # when a Test Port is switched back on - filtering the stored value would delete
            # the block for good.
            pre["test_procedure_full"] = _full
            pre[f["key"]] = _full
        elif _code == "RS_RI" and k == "test_procedure":
            # Resolve the EUT-support wording on the FORM as well, not just in the
            # generated document: the template default carries the combined
            # "non-conductive table/ insulation support of 0.8/0.1m" placeholder, and the
            # engineer should see the phrase that will actually print.
            pre[f["key"]] = _re_apply_support_mapping(f.get("default", ""), cfg)
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
        # Frequency Range default from the RE test detail. A CUSTOM specification on the
        # Test Request ('From ... To ...', stored as e.g. '30 MHz to 6 GHz') is carried
        # through verbatim and names the whole datasheet; only when the request used one
        # of the standard options do we fall back to the 30M-1G / 1G-6G choice.
        # This drives the Test Specification columns + Test Limit tables at generation.
        _detail_fr = _s(getattr(detail, "freq_range", ""))
        if _is_custom(_detail_fr) and re_custom_range(_detail_fr):
            pre["frequency_range"] = _detail_fr
        else:
            fr = _detail_fr.lower().replace(" ", "")
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
    # The engineer should see on the FORM the support wording that will print, on every
    # datasheet with a rule rather than the three that had one by hand. Runs after the
    # per-datasheet arms above (idempotent, so it does not undo them) and after the fixed
    # values, which can supply a procedure of their own.
    from . import procedures as _procedures
    _pcode = (schema.get("code") or "").upper()
    # A procedure an admin saved on the config page replaces the one the schema ships, for
    # every datasheet. The '<Standard name>' placeholder is resolved the same way, so a
    # stored text can keep it and still name the right standard.
    _stored_proc = _procedures.stored_procedure(_pcode)
    if _stored_proc:
        _basic = _s(pre.get("basic_standard")) or _DERIVED_BASIC_STANDARDS.get(_pcode, "")
        pre["test_procedure"] = (_stored_proc.replace("<Standard name>", _basic)
                                 if _basic else _stored_proc)
        # SURGE / EFT / CRF rebuild the procedure from the unfiltered text when a port is
        # switched, so that copy has to be the stored one too or the next change of port
        # would silently restore the shipped wording.
        if _s(pre.get("test_procedure_full")):
            pre["test_procedure_full"] = pre["test_procedure"]
    if _procedures.support_rule(_pcode):
        pre["test_procedure"] = _procedures.apply_support(
            _pcode, pre.get("test_procedure"), _s(pre.get("eut_configuration")))
    # Coupling Method follows the Test Port: a power line is driven through a CDN, a signal
    # line through an EM clamp. A default, not a lock - the field stays editable.
    _cm = _procedures.coupling_default(_pcode, _s(pre.get("test_port")))
    if _cm:
        pre["coupling_method"] = _cm
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


def equipment_candidates(code):
    """Every Equipment Master row tagged for this test code - instruments AND
    software together.

    Equipment and Software Used were two separate queries against the same table
    with the same filter; on a remote database that is a wasted round trip on
    every form load. Callers fetch this once and hand it to both row-builders.
    """
    code = (code or "").upper()
    if not code:
        return []
    try:
        from models import db, Equipment
        return Equipment.query.filter(
            Equipment.status.in_(["Active", "Available"]),
            Equipment.test_name.isnot(None),
            db.or_(Equipment.test_name.ilike(f"%{code}%"),
                   Equipment.test_name.ilike(f"%,{code}%"),
                   Equipment.test_name.ilike(f"%{code},%")),
        ).order_by(Equipment.sl_no.asc(), Equipment.name.asc()).all()
    except Exception:
        return []


def _equipment_rows_for(code, candidates=None):
    """Test Equipment Used rows from the Equipment Master tagged for this test
    code, as generic-table rows ({c0..c4}). Equipment is selected by the
    Equipment.test_name text column (a comma-separated list of codes, e.g.
    'RE,CE'); the exact code token must be present (avoids loose substring hits)."""
    code = (code or "").upper()
    if not code:
        return []
    if candidates is None:
        candidates = equipment_candidates(code)
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
            "c4": cd.isoformat() if cd else "NA",   # no calibration due date -> NA
        })
    return rows


_SOFTWARE_TYPES = ("software", "application", "tool")


def _software_rows_for(code, candidates=None):
    """Prefill 'Software Used' rows from the Equipment Master for a given test code.

    Shares the equipment_candidates() fetch with _equipment_rows_for and filters
    to the software types here, rather than issuing a second near-identical query.
    """
    if candidates is None:
        candidates = equipment_candidates(code)
    candidates = [c for c in candidates
                  if str(getattr(c, "type", "") or "").lower().strip() in _SOFTWARE_TYPES]
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
    # one Equipment Master query, shared by the equipment and software tables
    eq_candidates = None
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it.get("type") == "table":
                key = it.get("key")
                low = key.lower()
                if "equipment" in low or "software" in low:
                    if eq_candidates is None:
                        eq_candidates = equipment_candidates(code)
                if "equipment" in low:
                    rows = _equipment_rows_for(code, eq_candidates)
                    if rows:
                        out[key] = rows
                elif "software" in low:
                    rows = _software_rows_for(code, eq_candidates)
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


#: Default columns of an RE measurement-data table. The engineer can rename these or add
#: more on the form; the document table is generated from whatever is posted.
RE_MEAS_DEFAULT_HEADERS = (
    "Frequency(MHz)", "Polarization", "EUT Angle(deg)", "Antenna Height(cm)",
    "(QP) EMI(dBuV/m)", "QP Limit(dBuV/m)", "(QP) Margin(dB)",
)
_RE_MEAS_MAX_COLS = 20            # sanity bound; a portrait page cannot hold more


def upload_tables(schema):
    """The upload_table declarations in a schema, one per flagged section."""
    out = []
    for sec in schema.get("sections", []):
        ut = sec.get("upload_table")
        if ut and ut.get("key"):
            out.append(ut)
    return out


def collect_upload_table(form_data, key, max_cols=None):
    """Read one upload-driven table from the posted form.

    Its shape comes from the uploaded file, so both the headings (<key>__h<j>) and the
    values (<key>__c<j>[]) are read dynamically. Returns
    {'headers': [...], 'rows': [{'cells': [...]}], 'has_data': bool} - has_data is False
    when every cell is blank, so the generator can leave the table out of the document
    rather than printing an empty grid."""
    limit = int(max_cols or _RE_MEAS_MAX_COLS)
    headers = []
    for j in range(limit):
        hk = f"{key}__h{j}"
        if hk not in (form_data or {}):
            break
        headers.append(_s(form_data.get(hk)) or f"Column {j + 1}")
    if not headers:
        return {"headers": [], "rows": [], "has_data": False}

    cols = [f"c{j}" for j in range(len(headers))]
    arrs = {c: _list(form_data, f"{key}__{c}[]") for c in cols}
    n = max((len(a) for a in arrs.values()), default=0)
    rows = []
    for r_idx in range(n):
        cells = [(_s(arrs[c][r_idx]) if r_idx < len(arrs[c]) else "") for c in cols]
        if any(cells):
            rows.append({"cells": cells})
    return {"headers": headers, "rows": rows, "has_data": bool(rows)}


def _re_meas_table_headers(form_data, table_key):
    """Column headers posted for one measurement table, in order (may be renamed/extended).

    Returns [] when the form posted none, so the caller can supply the right default for
    that table's detector - a Peak table must not fall back to the quasi-peak headings."""
    out = []
    for j in range(_RE_MEAS_MAX_COLS):
        key = f"meas_table_{table_key}__h{j}"
        if key not in (form_data or {}):
            break
        out.append(_s(form_data.get(key)) or f"Column {j + 1}")
    return out


def _re_measurement_groups(form_data):
    indices = _list(form_data, "meas_index[]")
    groups = []
    for i in indices:
        i_str = str(i).strip()
        if not i_str:
            continue
        label = _s(form_data.get(f"meas_label_{i_str}"))
        img_vert_key = f"meas_img_vertical_{i_str}"
        img_horiz_key = f"meas_img_horizontal_{i_str}"

        group_idx = len(groups)
        fig_vert_num = 3 + 2 * group_idx
        fig_horiz_num = 4 + 2 * group_idx
        
        default_vert_cap = f"Figure {fig_vert_num}: RE plot_Vertical_Peak_30MHz - 1GHz"
        default_horiz_cap = f"Figure {fig_horiz_num}: RE plot_Horizontal_Peak_30MHz - 1GHz"

        img_vert_cap = _s(form_data.get(f"meas_img_vertical_caption_{i_str}")) or default_vert_cap
        img_horiz_cap = _s(form_data.get(f"meas_img_horizontal_caption_{i_str}")) or default_horiz_cap

        groups.append({
            "label": label,
            "index": i_str,
            "img_vertical_key": img_vert_key,
            "img_horizontal_key": img_horiz_key,
            "img_vertical_caption": img_vert_cap,
            "img_horizontal_caption": img_horiz_cap,
            # second-band captions + extra plots, so a draft reload rebuilds those slots
            "img_vertical_b2_caption": _s(form_data.get(f"meas_img_vertical_b2_caption_{i_str}")),
            "img_horizontal_b2_caption": _s(form_data.get(f"meas_img_horizontal_b2_caption_{i_str}")),
            "extra_images": [{"n": int(k.rsplit("_", 1)[1]), "caption": cap}
                             for k, cap in re_meas_extra_images(form_data, i_str)],
        })
    _re_meas_group_tables(groups, form_data)
    _re_meas_group_images(groups, form_data)
    return groups


#: A band's data tables. 30MHz-1GHz is measured quasi-peak, so it needs ONE table; above
#: 1GHz the limits are Peak AND Average, so that band needs TWO.
#: (detector name, header tag, caption tag) - the header uses the short form '(QP) EMI'
#: while the caption spells it out, e.g. 'RE_Quasi-peak_30MHz - 1GHz' / 'RE_Avg_1-6GHz'.
#: A custom range keeps a single quasi-peak table regardless of where it sits, so a
#: bespoke range never silently gains the Peak/Average pair.
_RE_BAND_DETECTORS = {
    "30": (("Quasi-peak", "QP", "Quasi-peak"),),
    "16": (("Peak", "Peak", "Peak"), ("Average", "Avg", "Avg")),
    "custom": (("Quasi-peak", "QP", "Quasi-peak"),),
}
#: Human band text used in the table captions ('custom' uses the engineer's own text).
_RE_BAND_CAPTION = {"30": "30MHz - 1GHz", "16": "1-6GHz"}


def re_meas_detector_headers(tag):
    """Default column headers for a table measured with the given detector."""
    return ["Frequency(MHz)", "Polarization", "EUT Angle(deg)", "Antenna Height(cm)",
            f"({tag}) EMI(dBuV/m)", f"{tag} Limit(dBuV/m)", f"({tag}) Margin(dB)"]


def re_meas_group_table_specs(label, freq):
    """The data tables one measurement group needs, as
    [{'suffix', 'detector', 'tag', 'band', 'band_label'}] in document order.

    A group covering only 30MHz-1GHz gets a single quasi-peak table (suffix '' - the
    original field names, so existing drafts keep working). A group covering 1GHz-6GHz
    gets a Peak table and an Average table."""
    specs = []
    for band_tag, human in re_meas_group_bands(label, freq):
        for detector, tag, caption_tag in _RE_BAND_DETECTORS[band_tag]:
            specs.append({
                "detector": detector,
                "tag": tag,
                "caption_tag": caption_tag,
                "band": band_tag,
                # a custom band is named with the engineer's own text
                "band_label": _RE_BAND_CAPTION.get(band_tag, human),
            })
    # first table keeps the legacy un-suffixed field names
    for n, spec in enumerate(specs):
        spec["suffix"] = "" if n == 0 else f"_{n + 1}"
    return specs


def _re_meas_group_tables(groups, form_data):
    """Attach rec['tables'] = [{key, headers, rows, caption}] to every group.

    Table numbering ("Table 1:", "Table 2:", ...) runs continuously across all groups so
    the document reads in order."""
    freq = _s((form_data or {}).get("frequency_range"))
    num = 1
    for rec in groups:
        i_str = rec["index"]
        tables = []
        for spec in re_meas_group_table_specs(rec.get("label"), freq):
            key = f"{i_str}{spec['suffix']}"
            headers = _re_meas_table_headers(form_data, key) or re_meas_detector_headers(spec["tag"])
            cols = [f"c{j}" for j in range(len(headers))]
            arrs = {c: _list(form_data, f"meas_table_{key}__{c}[]") for c in cols}
            n = max((len(a) for a in arrs.values()), default=0)
            rows = []
            for r_idx in range(n):
                row = {c: (_s(arrs[c][r_idx]) if r_idx < len(arrs[c]) else "") for c in cols}
                if any(row.values()):
                    # 'cells' drives the template's column loop; c0..cN stay for the form
                    row["cells"] = [row[c] for c in cols]
                    rows.append(row)
            if not rows:
                blank = {c: "" for c in cols}
                blank["cells"] = ["" for _ in cols]
                rows = [blank]

            caption = (_s(form_data.get(f"meas_table_caption_{key}"))
                       or f"Table {num}: RE_{spec['caption_tag']}_{spec['band_label']}")
            tables.append({
                "key": key,
                "detector": spec["detector"],
                "tag": spec["tag"],
                "band": spec["band"],
                "band_label": spec["band_label"],
                "headers": headers,
                "rows": rows,
                "caption": caption,
                # what the engineer typed (blank = use the automatic caption). Kept
                # separate so a draft reload does not freeze the auto caption into the box.
                "caption_input": _s(form_data.get(f"meas_table_caption_{key}")),
            })
            num += 1
        rec["tables"] = tables
        # keep the single-table keys so older callers / the form's restore path still work
        if tables:
            rec["table_headers"] = tables[0]["headers"]
            rec["table_rows"] = tables[0]["rows"]
            rec["table_caption"] = tables[0]["caption"]


#: Bands a measurement group needs plots for. FCC is specified over both bands (quasi-peak
#: to 1GHz, peak/average above it), so with Both selected it needs a Vertical + Horizontal
#: pair per band. CISPR/ICES only has 30MHz-1GHz limits, so it keeps a single pair.
_RE_BAND_30 = ("30", "30MHz - 1GHz")
_RE_BAND_16 = ("16", "1GHz - 6GHz")


def re_meas_group_bands(label, freq):
    """The bands one measurement group needs plots for, as [(tag, human label)].

    A CUSTOM range is a single band named with the engineer's own text, whatever the
    group's family - the standard 30MHz/1GHz split does not apply to it."""
    custom = re_custom_range(freq)
    if custom:
        return [("custom", custom)]
    s30, s16 = _re_selected_ranges(freq)
    is_fcc = "fcc" in (label or "").lower()
    bands = []
    if s30:
        bands.append(_RE_BAND_30)
    if s16 and (is_fcc or not s30):
        # Above 1GHz only applies to FCC; a non-FCC group falls back to the single
        # selected band so it always has one pair of slots.
        bands.append(_RE_BAND_16)
    return bands or [_RE_BAND_30]


def re_meas_extra_images(form_data, i_str):
    """Extra plots the engineer added to one measurement group, each with its own title.
    Posted as meas_img_extra_<group>_<n> / meas_img_extra_caption_<group>_<n>."""
    prefix = f"meas_img_extra_caption_{i_str}_"
    out = []
    for k in (form_data or {}):
        if k.startswith(prefix) and k[len(prefix):].isdigit():
            n = int(k[len(prefix):])
            out.append((n, f"meas_img_extra_{i_str}_{n}", _s(form_data.get(k))))
    return [(key, cap) for _n, key, cap in sorted(out)]


def _re_meas_group_images(groups, form_data):
    """Attach rec['images'] = [{key, caption}] to every measurement group: the standard
    Vertical/Horizontal pair per applicable band, then any extra plots the engineer added.
    Figures are numbered continuously across all groups, starting after the two Functional
    Check figures. The generator drops entries whose slot holds no image."""
    freq = _s((form_data or {}).get("frequency_range"))
    order = _list(form_data, "meas_index[]")
    idxs = [_s(i).strip() for i in order if _s(i).strip()]
    fig = 3                                    # Figures 1-2 are the Functional Check plots
    for rec, i_str in zip(groups, idxs):
        images = []
        bands = re_meas_group_bands(rec.get("label"), freq)
        for b, (tag, human) in enumerate(bands):
            suffix = "" if b == 0 else "_b2"
            # a custom range keeps quasi-peak only, so it reads 'Peak' like the low band
            detector = "Peak & Average" if tag == "16" else "Peak"
            for role in ("vertical", "horizontal"):
                key = f"meas_img_{role}{suffix}_{i_str}"
                typed = _s(form_data.get(f"meas_img_{role}{suffix}_caption_{i_str}"))
                images.append({
                    "key": key,
                    "caption": typed or ("Figure %d: RE plot_%s_%s_%s"
                                         % (fig, role.capitalize(), detector, human)),
                })
                fig += 1
        for key, cap in re_meas_extra_images(form_data, i_str):
            # The engineer's title keeps its own wording but still joins the document's
            # Figure sequence, so the numbering stays unbroken. _re_renumber_figures()
            # fixes the actual number after empty slots have been dropped.
            if not cap.lower().startswith("figure"):
                cap = f"Figure {fig}: {cap}".rstrip() if cap else f"Figure {fig}: RE plot"
            images.append({"key": key, "caption": cap})
            fig += 1
        rec["images"] = images


def _re_functional_mode_names(request_obj):
    """RE's Test Mode = the NAMES of the Test Request's functional modes, not their
    descriptions: 'Mode A', 'Mode A, Mode B', ... The request form asks for a number of
    functional modes and then labels the description boxes Mode A / Mode B / ... (letters
    from 'A'), so the names are derived from the count the same way here.

    The count comes from the stored mode rows; a request that recorded only the number
    (no rows yet) falls back to iec_emc_requests.number_of_modes. Returns '' when the
    request has neither, so the caller keeps the old text rather than blanking the field."""
    if request_obj is None:
        return ""
    rows = getattr(request_obj, "functional_modes", None) or []
    n = len([m for m in rows if _s(getattr(m, "mode_value", ""))])
    if not n:
        try:
            n = int(_s(getattr(request_obj, "number_of_modes", "")) or 0)
        except (TypeError, ValueError):
            n = 0
    if n < 1:
        return ""
    n = min(n, 26)                        # 'Mode A'..'Mode Z'; the form caps input at 10
    return ", ".join("Mode %s" % chr(ord("A") + i) for i in range(n))


RE_EXTRA_PHOTO_PREFIX = "re_extra_photo_"


def re_extra_photo_slots(form_data):
    """The extra TEST SETUP PICTURES the engineer added on the form, beyond RE's four
    standard (polarization x band) slots. Each slot posts a file 're_extra_photo_<i>'
    plus its label 're_extra_photo_caption_<i>'; the slot survives here even with an
    empty label / no upload, and render() drops the ones with no image.

    Returns [{'idx': i, 'key': 're_extra_photo_<i>', 'caption': <as typed>}] in slot
    order. Used both to rebuild the form on draft reload and to build the document
    context (where the label gets its 'Photo N:' number)."""
    cap_prefix = RE_EXTRA_PHOTO_PREFIX + "caption_"
    idxs = set()
    for k in (form_data or {}):
        if k.startswith(cap_prefix):
            suffix = k[len(cap_prefix):]
            if suffix.isdigit():
                idxs.add(int(suffix))
    return [{"idx": i,
             "key": f"{RE_EXTRA_PHOTO_PREFIX}{i}",
             "caption": _s((form_data or {}).get(f"{cap_prefix}{i}"))}
            for i in sorted(idxs)]


def _re_extra_photos(form_data):
    """Document-side view of re_extra_photo_slots(): the label is given a provisional
    'Photo N:' number so it matches the caption pattern the generator recognises.
    _re_renumber_photos() then renumbers every photo caption in document order, which
    is what makes the extras continue after the standard slots that actually printed."""
    out = []
    for n, slot in enumerate(re_extra_photo_slots(form_data), start=1):
        cap = slot["caption"]
        if not cap.lower().startswith("photo"):
            cap = f"Photo {n}: {cap}".rstrip()
        out.append({"key": slot["key"], "caption": cap})
    return out


def _re_filter_procedure(text, s30, s16):
    """Keep only the Test Procedure paragraphs for the selected Frequency Range, and
    when BOTH ranges apply merge the two near-identical setup paragraphs into one
    ('For 30MHz to 1GHz and 1GHz to 6GHz, ...'). The pre-scan paragraphs stay
    separate because their detector + antenna-height details genuinely differ."""
    txt = _s(text)
    if not txt:
        return txt
    paras = [p for p in re.split(r"\n\s*\n", txt)]

    def kind(p):
        s = p.strip().lower()
        if s.startswith("for 30mhz"):
            return "setup30"
        if s.startswith("for 1ghz"):
            return "setup16"
        if s.startswith("pre-scan (peak &"):
            return "scan16"
        if s.startswith("pre-scan"):
            return "scan30"
        return "other"

    tagged = [(kind(p), p) for p in paras]
    if s30 and s16:
        # Both ranges: each frequency keeps its OWN setup + pre-scan paragraphs,
        # under the single opening 'in accordance with' line.
        return "\n\n".join(p.strip() for _, p in tagged if p.strip())

    drop = {"setup16", "scan16"} if not s16 else {"setup30", "scan30"}
    return "\n\n".join(p.strip() for k, p in tagged if k not in drop and p.strip())


def _re_range_label(form_data):
    """Human range text used in RE captions ('30MHz - 1GHz' / '1GHz - 6GHz'), or the
    engineer's own text when the Test Request specified a custom range."""
    freq = _s(form_data.get("frequency_range"))
    custom = re_custom_range(freq)
    if custom:
        return custom
    return "1GHz - 6GHz" if freq == "1GHz-6GHz" else "30MHz - 1GHz"


#: The three standard Frequency Range options. Anything else in the field is a CUSTOM
#: range carried over from the Test Request's RE custom specification (e.g. '30 MHz to
#: 6 GHz'), and the whole datasheet is then named after that text.
_RE_STANDARD_RANGES = {"30mhz-1ghz", "1ghz-6ghz", "both"}


def re_custom_range(freq):
    """The custom Frequency Range text, or '' when one of the standard options is used."""
    v = _s(freq)
    if not v:
        return ""
    return "" if v.lower().replace(" ", "") in _RE_STANDARD_RANGES else v


def _re_selected_ranges(freq):
    """(show_30MHz_1GHz, show_1GHz_6GHz) for the selected Frequency Range.
    'Both' (or blank, for legacy drafts) shows both.

    A CUSTOM range is one band, so it reports (True, False): the spec table keeps a
    single value column and only one pair of photo/plot slots is produced. Its NAME comes
    from re_custom_range(), not from the 30MHz-1GHz label."""
    f = (freq or "").strip().lower()
    if re_custom_range(freq):
        return True, False
    if f == "30mhz-1ghz":
        return True, False
    if f == "1ghz-6ghz":
        return False, True
    return True, True                      # 'Both' / unset


def _re_rotation_steps(product_standard):
    """Turn-table rotation step sections: 15deg for a CISPR-family basic standard,
    22.5deg for the CFR/ANSI family, both when the standard names both."""
    from . import re_logic
    fams = re_logic.families(product_standard)
    steps = []
    if "CISPR" in fams:
        steps.append("15°")
    if "FCC" in fams:
        steps.append("22.5°")
    return steps


#: Spec-table rows the engineer can split into 1-3 sections, with the field prefix
#: holding each section's value (<base>, <base>_2, <base>_3).
_RE_SPLIT_ROWS = (("ambient temperature", "ambient_temperature"),
                  ("relative humidity", "relative_humidity"),
                  ("test date", "test_date"),
                  ("tested by", "tested_by"))

#: RS_RI's spec rows already carry TWO value cells - one per frequency band (80M-1G and
#: 1G-6G) - so a split has to name the CELL as well as the row. Cell 1 is the 80M-1G
#: column, cell 2 is 1G-6G; 'Tested by' has a single cell spanning both bands.
#:     (row label needle, value-cell index, field base)
#: (row needle, which value CELL, which form field feeds it).
#:
#: cell 0 means the WHOLE value area, not one band's cell. The form asks for Ambient
#: Temperature once rather than once per frequency band, so the row shows the engineer's
#: day sections once across the row - splitting each band cell instead gave 3 days x 2
#: bands = six boxes of the same value. 'Tested by' already worked this way.
#: The frequency-range row keeps its two band cells; only these four collapse.
_RS_RI_SPLIT_ROWS = (
    ("ambient temperature", 0, "ambient_temperature_col_1"),
    ("relative humidity",   0, "relative_humidity_col_1"),
    ("test date",           0, "test_date_col_1"),
    ("tested by",           0, "tested_by"),
)


def _rs_ri_row_splits(form_data):
    """Per-CELL section counts (1/2/3) for RS_RI's spec rows, with each section's value.

    Same idea as _re_row_splits, but every band column is independent: the 80M-1G sweep can
    have run over two days while 1G-6G ran on one. Dates are reformatted to DD/MM/YYYY the
    way the document shows them."""
    out = []
    for needle, cell, base in _RS_RI_SPLIT_ROWS:
        try:
            n = int(_s(form_data.get(base + "_sections")) or 1)
        except ValueError:
            n = 1
        n = max(1, min(3, n))
        vals = []
        for i in range(n):
            v = _s(form_data.get(base if i == 0 else "%s_%d" % (base, i + 1)))
            vals.append(_fmt_ddmmyyyy(v) if base.startswith("test_date") else v)
        out.append({"needle": needle, "cell": cell, "values": vals})
    return out


def _re_row_splits(form_data):
    """Per-row section counts (1/2/3) the engineer chose, with the values for each
    section. Each row is independent, so Ambient can be 2 sections while Test Date
    is 3."""
    out = []
    for needle, base in _RE_SPLIT_ROWS:
        try:
            n = int(_s(form_data.get(base + "_sections")) or 1)
        except ValueError:
            n = 1
        n = max(1, min(3, n))
        vals = []
        for i in range(n):
            v = _s(form_data.get(base if i == 0 else "%s_%d" % (base, i + 1)))
            vals.append(_fmt_ddmmyyyy(v) if base == "test_date" else v)
        out.append({"needle": needle, "values": vals})
    return out


def _re_days(form_data):
    """Per-day Test Date / Ambient Temperature / Relative Humidity entries (up to 3).
    A day is kept when any of its three values was filled in; dates print DD/MM/YYYY."""
    days = []
    for suffix in ("", "_2", "_3"):
        d = _fmt_ddmmyyyy(_s(form_data.get("test_date" + suffix)))
        t = _s(form_data.get("ambient_temperature" + suffix))
        h = _s(form_data.get("relative_humidity" + suffix))
        if d or t or h:
            days.append({"date": d, "temp": t, "hum": h})
    return days


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d")


def to_iso_date(value):
    """Normalize a date to YYYY-MM-DD for an <input type="date">, which shows blank for
    anything else. Drafts saved while these were free-text fields hold DD/MM/YYYY, so
    without this the value would disappear from the form. Unparseable text -> ''."""
    v = _s(value)
    if not v:
        return ""
    from datetime import datetime as _dt
    for fmt in _DATE_FORMATS:
        try:
            return _dt.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


#: Scalar context keys that hold a date. Matches 'date', 'test_date', 'test_date_2',
#: 'test_date_col_1', 'test_date_col_1_2' - and deliberately NOT 'test_date_sections',
#: which is a count of 1/2/3, nor 'test_duration'.
_DATE_KEY_RE = re.compile(r"^(?:[a-z0-9_]*_)?date(?:_col_\d+)?(?:_\d+)?$", re.I)


def _is_date_key(key):
    return bool(_DATE_KEY_RE.match(_s(key)))


def normalize_context_dates(schema, ctx):
    """Print every date in the document as DD/MM/YYYY, in place.

    The forms post ISO (an <input type=date> always does, and must keep doing so for the
    browser), and the equipment master returns whatever the database holds. Formatting was
    happening only where a per-datasheet builder remembered to call _fmt_ddmmyyyy, so the
    sign-off Date and the Calibration Due column still printed as 2026-07-23.

    Two passes, both driven by the SCHEMA rather than by guessing at key names:
      * scalars whose key is a date key (see _DATE_KEY_RE);
      * repeating-table cells whose COLUMN LABEL mentions 'date' or 'due' - which is how
        'Calibration Due' and 'Date modification fitted' are found without hard-coding c3/c4.

    _fmt_ddmmyyyy returns unparseable text unchanged, so a value like 'NA' is safe, and the
    whole pass is idempotent - applying it to already-formatted values is a no-op.
    """
    if not isinstance(ctx, dict):
        return ctx
    for k, v in list(ctx.items()):
        if isinstance(v, str) and v and _is_date_key(k):
            ctx[k] = _fmt_ddmmyyyy(v)

    date_cols = {}                      # table key -> the cell keys holding dates
    for sec in (schema or {}).get("sections", []) or []:
        for it in sec.get("items", []) or []:
            if it.get("type") != "table":
                continue
            cells = [c.get("key") for c in it.get("columns", []) or []
                     if re.search(r"date|due", _s(c.get("label")) + " " + _s(c.get("key")), re.I)]
            if cells:
                date_cols[it.get("key")] = cells
    for tkey, cells in date_cols.items():
        for row in ctx.get(tkey) or []:
            if not isinstance(row, dict):
                continue
            for ck in cells:
                if isinstance(row.get(ck), str) and row[ck]:
                    row[ck] = _fmt_ddmmyyyy(row[ck])
    return ctx


def _fmt_ddmmyyyy(value):
    """Normalize a date to DD/MM/YYYY; unparseable text is returned unchanged."""
    v = _s(value)
    if not v:
        return ""
    from datetime import datetime as _dt
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return _dt.strptime(v, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return v
