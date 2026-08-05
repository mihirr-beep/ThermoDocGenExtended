# -*- coding: utf-8 -*-
"""Per-test data mapping: datasheet form_json -> IEC-FRM-516 report tables.

The report's per-test sections and the per-test datasheets are generated from
the same IEC-FRM-5xx source forms, so their TEST SPECIFICATION rows line up
almost exactly: matching the report's row label against the datasheet schema's
field labels resolves 187 of 197 rows automatically (see ``resolve_key``).

The remaining rows are ones the report prints as a *matrix* where the datasheet
stores separate scalars - EFT's two test-voltage columns, SURGE's CM/DM x
power/signal grid, Voltage Dips' three-row level block, PFMF's coil orientation.
Those get an explicit handler in ``SPEC_HANDLERS``.

Everything a handler needs is the posted form dict, so the same functions work
for a live request and for a fixture.
"""
import re

from . import docx_tools as T

# --------------------------------------------------------------------------
# label normalisation / key resolution
# --------------------------------------------------------------------------

def norm_label(s):
    """Normalise a table row label or field label for matching.

    Drops parenthetical units and cross-references ("(Hz)", "(Refer 2.7)"),
    then keeps alphanumerics only - so "Step size (Hz)" == "step_size".
    """
    s = (s or "").replace(" ", " ")
    s = re.sub(r"\(.*?\)", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# Report label -> datasheet key, where the two genuinely differ in name only.
KEY_ALIASES = {
    "CE": {
        # the bespoke CE form calls it eut_voltage_frequency
        "eutinputvoltagefrequency": "eut_voltage_frequency",
        "classification": "classification_class",
    },
    "RS_RI": {
        "frequencyrange": "frequency_range_col_1",
        "fieldstrength": "field_strength_col_1",
        "ambienttemperature": "ambient_temperature_col_1",
        "relativehumidity": "relative_humidity_col_1",
        "testdate": "test_date_col_1",
    },
    "RE": {
        "classification": "classification_col_2",
    },
    "SURGE": {
        "testport": "test_port_power",
    },
}


def schema_key_index(code):
    """{normalised label: form key} for every scalar field of a datasheet.

    Both the field's label and its key are indexed, so a report row matches
    whichever the source form happened to use.
    """
    index = {}

    def _add(norm, key):
        if norm and norm not in index:
            index[norm] = key

    if code == "CE":
        from datasheet_gen.service import SCALAR_FIELDS
        for key in SCALAR_FIELDS:
            _add(norm_label(key.replace("_", " ")), key)
    else:
        from datasheet_gen.registry import load_schema
        from datasheet_gen import generic_service as gs
        schema = load_schema(code)
        for f in gs.iter_scalar_fields(schema):
            if f.get("input") == "image":
                continue
            _add(norm_label(f.get("label") or f["key"]), f["key"])
            _add(norm_label(f["key"].replace("_", " ")), f["key"])
    return index


def resolve_key(code, label, index):
    """Datasheet form key for a report row label, or None."""
    n = norm_label(label)
    alias = KEY_ALIASES.get(code, {}).get(n)
    if alias:
        return alias
    if n in index:
        return index[n]
    # band-split rows: the report prints one label for two columns
    for suffix in ("_col_1", "_2"):
        if n + norm_label(suffix) in index:
            return index[n + norm_label(suffix)]
    # last resort: a close substring match, guarded on length so that e.g.
    # "Modulation" does not swallow "Modulation depth"
    best = None
    for sn, key in index.items():
        if not sn or abs(len(sn) - len(n)) > 6:
            continue
        if sn in n or n in sn:
            if best is None or abs(len(sn) - len(n)) < abs(len(best[0]) - len(n)):
                best = (sn, key)
    return best[1] if best else None


def _val(form, key):
    v = form.get(key)
    if isinstance(v, list):
        for x in v:
            if x not in (None, ""):
                return str(x).strip()
        return ""
    return "" if v is None else str(v).strip()


def band_values(form, key):
    """(col_1, col_2) for a band-split field, e.g. RE's 30M-1G / 1G-6G columns.

    Handles both naming conventions the generated schemas use: ``x_col_1`` /
    ``x_col_2`` and ``x_2`` / ``x_3``.
    """
    base = re.sub(r"(_col_[12]|_[23])$", "", key)
    for a, b in (("_col_1", "_col_2"), ("_2", "_3")):
        v1, v2 = _val(form, base + a), _val(form, base + b)
        if v1 or v2:
            return v1, v2
    v = _val(form, base) or _val(form, key)
    return v, v


# --------------------------------------------------------------------------
# checkbox helpers mirroring datasheet_gen.layout
# --------------------------------------------------------------------------

def tick_cumulative(cell, level):
    """Tick every printed option up to and including ``level``.

    Mirrors ``datasheet_gen.layout.cumulative_checkbox``: for a derived test
    voltage, applying ±1 kV implies ±0.5 kV was applied too, so both boxes are
    ticked. A "Custom" level ticks only Custom (and fills its blank).
    """
    level = str(level or "").strip()
    slots = T.checkbox_slots(cell) or T.literal_checkbox_slots(cell)
    if not slots or not level:
        return []
    labels = [s[-1] for s in slots]
    sel = -1
    for i, lab in enumerate(labels):
        if _loose_eq(level, lab):
            sel = i
            break
    if sel < 0:                                    # not printed -> Custom slot
        T.tick_or_custom(cell, level)
        return []
    if "custom" in labels[sel].lower():
        T.tick_checkboxes(cell, labels[sel])
        T.fill_custom_slot(cell, level)
        return [labels[sel]]
    wanted = [lab for i, lab in enumerate(labels)
              if i <= sel and "custom" not in lab.lower()]
    return T.tick_checkboxes(cell, wanted, multi=True)


_UNIT_SUFFIX = re.compile(
    r"(kvrms|vrms|kv|kw|mhz|ghz|khz|hz|db|am|vm|ms|ns|sec|s|v|a|w)$")


def _strip_unit(norm):
    """Drop a trailing unit token from an already-normalised label."""
    return _UNIT_SUFFIX.sub("", norm) or norm


def _numeric(value):
    """The first signed decimal in a value, or None."""
    m = re.search(r"[-+±]?\s*\d+(?:\.\d+)?", str(value or ""))
    if not m:
        return None
    try:
        return float(re.sub(r"[^0-9.]", "", m.group(0)))
    except ValueError:
        return None


def _loose_eq(a, b):
    """Compare an option label against a value, tolerating unit drift.

    The datasheet stores "±2 kV" while the report prints "±2" (its unit sits in
    the row label "Test Voltage(kV)"), so a plain string compare misses. Falls
    back to comparing the numeric part when both sides carry a number.
    """
    na, nb = norm_label(a), norm_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    sa, sb = _strip_unit(na), _strip_unit(nb)
    if sa and sa == sb:
        return True
    xa, xb = _numeric(a), _numeric(b)
    if xa is None or xb is None or xa != xb:
        return False
    # same number - only equal if neither side carries a *different* sign
    sign_a = "-" if str(a).strip().startswith("-") else ("±" if "±" in str(a) else "+")
    sign_b = "-" if str(b).strip().startswith("-") else ("±" if "±" in str(b) else "+")
    return sign_a == sign_b or "±" in (sign_a, sign_b)


def _applicable(value):
    return str(value or "").strip().lower().startswith("appl")


# --------------------------------------------------------------------------
# explicit TEST SPECIFICATION row handlers
# --------------------------------------------------------------------------
# Each handler gets (value_cells, form, ctx) where value_cells are the row's
# writable cells (label cell excluded) and ctx carries per-test extras.

def _h_eft_test_ports(cells, form, ctx):
    """EFT 'Number of Test Ports': ☐ Power Line: | ☐ Signal:"""
    port = _val(form, "test_port")
    if len(cells) >= 2:
        T.tick_checkboxes(cells[0], "Power Line" if port in ("Power Line", "Both") else "-")
        T.tick_checkboxes(cells[1], "Signal" if port in ("Signal Line", "Both") else "-")


def _h_eft_test_voltage(cells, form, ctx):
    """EFT 'Test Voltage': cumulative levels for power | signal."""
    if len(cells) >= 2:
        tick_cumulative(cells[0], _val(form, "test_voltage_power_line"))
        tick_cumulative(cells[1], _val(form, "test_voltage_signal_line"))


def _h_surge_test_port(cells, form, ctx):
    """SURGE 'Test Port': ☐ Power Line | ☐ Signal Line, from Applicable flags."""
    if len(cells) >= 2:
        if _applicable(_val(form, "test_port_power")):
            T.tick_checkboxes(cells[0], "Power Line")
        if _applicable(_val(form, "test_port_signal")):
            T.tick_checkboxes(cells[1], "Signal Line")


def _h_surge_test_voltage(cells, form, ctx):
    """SURGE 'Test Voltage(kV)': the CM row then the DM row.

    Each row is [mode label, power cell, signal cell]; the mode is read off the
    row itself so the two rows cannot be swapped.
    """
    mode = "cm"
    if cells:
        label = T.full_text(cells[0]).lower()
        if "differential" in label or re.search(r"\bdm\b", label):
            mode = "dm"
    power_cell = cells[1] if len(cells) > 2 else (cells[0] if cells else None)
    signal_cell = cells[2] if len(cells) > 2 else (cells[1] if len(cells) > 1 else None)
    if power_cell is not None and _applicable(_val(form, "test_port_power")):
        tick_cumulative(power_cell, _val(form, "surge_tv_%s_power" % mode))
    if signal_cell is not None and _applicable(_val(form, "test_port_signal")):
        tick_cumulative(signal_cell, _val(form, "surge_tv_%s_signal" % mode))


def _h_esd_indirect(cells, form, ctx):
    """ESD 'Indirect Contact Discharge HCP/VCP': one cell, two option lists.

    The cell prints NA/±2/±4/±8/Custom for HCP then ±2/±4/±8/Custom for VCP, so
    the two are separated by slot index rather than by text.
    """
    if not cells:
        return
    cell = cells[-1]
    slots = T.checkbox_slots(cell)
    # The HCP list is the first one and ends at its 'Custom' option; whatever
    # follows is the VCP list.
    hcp_end = len(slots)
    for i, (_sdt, label) in enumerate(slots):
        if "custom" in label.lower():
            hcp_end = i + 1
            break
    T.tick_checkboxes(cell, _val(form, "indirect_hcp"), slot_range=(0, hcp_end))
    if hcp_end < len(slots):
        T.tick_checkboxes(cell, _val(form, "indirect_vcp"),
                          slot_range=(hcp_end, len(slots)))


def _h_pfmf_coil(cells, form, ctx):
    """PFMF 'Coil Orientation': proximity angles | immersion axes (multi-select)."""
    angles = form.get("coil_angles[]") or form.get("coil_angles") or []
    axes = form.get("coil_axes[]") or form.get("coil_axes") or []
    angles = angles if isinstance(angles, list) else [angles]
    axes = axes if isinstance(axes, list) else [axes]
    if len(cells) >= 2:
        T.tick_checkboxes(cells[0], angles, multi=True)
        T.tick_checkboxes(cells[1], axes, multi=True)
    elif cells:
        T.tick_checkboxes(cells[0], list(angles) + list(axes), multi=True)


def _h_pfmf_test_level(cells, form, ctx):
    """PFMF 'Test Level': 1A/m | 3A/m | 30A/m | Custom____."""
    if cells:
        T.tick_or_custom(cells[-1], _val(form, "test_level"))


def _h_vdips_level(cells, form, ctx):
    """Voltage Dips 'Test Level': the derived percentage / duration rows.

    The report prints a 3-row merged block: a header row (Voltage Dips /
    Interruption), a percentage row and a duration row. The values are derived
    from the immunity test requirement exactly as the datasheet derives them, so
    the same VDIPS_LEVELS table is reused.
    """
    kind = ctx.setdefault("_vdips_row", 0)
    ctx["_vdips_row"] = kind + 1
    if kind == 0:                                  # the header row - nothing to fill
        return
    from datasheet_gen.generic_service import VDIPS_LEVELS
    lv = VDIPS_LEVELS.get(_val(form, "immunity_test_requirement")) or {}
    dips, intr = lv.get("dips", []), lv.get("intr", [])
    if not dips and not intr:
        return
    # the report shows 3 dip columns + 1 interruption column
    if kind == 1:
        vals = [(d["pct"] + " %") for d in dips[:3]] + \
               [(intr[0]["pct"] + " %") if intr else ""]
    else:
        vals = [d["spec"] for d in dips[:3]] + [intr[0]["spec"] if intr else ""]
    for i, cell in enumerate(cells):
        if i < len(vals) and vals[i]:
            T.set_cell_text(cell, vals[i])


def _h_classification(cells, form, ctx):
    """'Classification': Group 1/2 in one cell, Class A/B(/C/D) in the next."""
    code = ctx.get("code")
    group = _val(form, "classification_group") or ctx.get("product_group", "")
    cls = (_val(form, "classification_class") or _val(form, "classification")
           or _val(form, "classification_col_2") or ctx.get("class_type", ""))
    if len(cells) >= 2:
        if group:
            T.tick_checkboxes(cells[0], group)
        if cls:
            T.tick_checkboxes(cells[1], cls)
    elif cells:
        T.tick_checkboxes(cells[0], [x for x in (group, cls) if x], multi=True)


# (code, normalised report label) -> handler.  '*' matches any code.
SPEC_HANDLERS = {
    ("EFT", "numberoftestports"): _h_eft_test_ports,
    ("EFT", "testvoltage"): _h_eft_test_voltage,
    ("SURGE", "testport"): _h_surge_test_port,
    ("SURGE", "testvoltage"): _h_surge_test_voltage,
    ("ESD", "indirectcontactdischargehcpvcp"): _h_esd_indirect,
    ("PFMF", "coilorientation"): _h_pfmf_coil,
    ("PFMF", "testlevel"): _h_pfmf_test_level,
    ("VOLTAGEDIPS", "testlevel"): _h_vdips_level,
    ("*", "classification"): _h_classification,
}


def spec_handler(code, label):
    n = norm_label(label)
    return SPEC_HANDLERS.get((code, n)) or SPEC_HANDLERS.get(("*", n))


# --------------------------------------------------------------------------
# TEST SPECIFICATION table
# --------------------------------------------------------------------------

def fill_spec_table(table, code, form, ctx=None):
    """Fill one test's TEST SPECIFICATION table from its datasheet form.

    Returns (filled, unresolved) counts so the builder can log coverage.
    """
    ctx = ctx if ctx is not None else {}
    ctx.setdefault("code", code)
    index = schema_key_index(code)
    filled = unresolved = 0

    for row in table.rows:
        label = T.row_label(row)
        cells = T.distinct_cells(row)
        if len(cells) < 2:
            continue
        value_cells = cells[1:]

        handler = spec_handler(code, label)
        if handler is not None:
            handler(value_cells, form, ctx)
            filled += 1
            continue
        if not label:
            continue

        key = resolve_key(code, label, index)
        if key is None:
            unresolved += 1
            continue

        if len(value_cells) >= 2:
            v1, v2 = band_values(form, key)
            # two cells with one value each == a band split; two cells printing
            # the same option list == a checkbox pair (Tabletop | Floor standing)
            if T.has_checkboxes(value_cells[0]) and T.has_checkboxes(value_cells[1]):
                val = v1 or v2
                for c in value_cells:
                    T.tick_checkboxes(c, val)
            else:
                for c, v in zip(value_cells, (v1, v2)):
                    _write(c, v)
        else:
            _write(value_cells[0], _val(form, key))
        filled += 1
    return filled, unresolved


def _write(cell, value):
    """Write a value into a spec cell, ticking rather than overwriting when the
    cell holds checkboxes (overwriting would delete the controls). An empty value
    leaves the cell untouched, so the template's printed constants survive."""
    value = str(value or "").strip()
    if not value:
        return
    if not T.has_checkboxes(cell):
        T.set_cell_text(cell, value)
        return
    # a multi-select row stores its selections as one comma-separated value
    # (e.g. Coupling Phases "0°, 90°, 180°, 270°") - tick each of them
    parts = [p.strip() for p in re.split(r"\s*[,;]\s*", value) if p.strip()]
    if len(parts) > 1:
        ticked = T.tick_checkboxes(cell, parts, multi=True)
        if ticked:
            return
    T.tick_or_custom(cell, value)


# --------------------------------------------------------------------------
# TEST OBSERVATION
# --------------------------------------------------------------------------

def _obs_rows_from_matrix(matrix):
    """[[label, *cells], ...] from an EFT/SURGE {'cols', 'rows'} matrix."""
    if not matrix:
        return []
    return [[r.get("label", "")] + list(r.get("cells") or [])
            for r in matrix.get("rows") or []]


def observation_tables(code, form):
    """Ordered [(hint, rows)] for a test's observation grids.

    ``hint`` is a substring that identifies which report table the rows belong
    to (the report labels its grids "Power Line:", "AC Power Line:", etc.), and
    ``rows`` are row-lists ready for ``fill_table_rows``. Returns [] for a test
    whose grid is a single fixed table.
    """
    from datasheet_gen import generic_service as gs

    if code == "EFT":
        return [("power", _obs_rows_from_matrix(gs._eft_obs(form, "power"))),
                ("signal", _obs_rows_from_matrix(gs._eft_obs(form, "signal")))]
    if code == "SURGE":
        return [("ac", _obs_rows_from_matrix(gs._surge_obs(form, "ac"))),
                ("dc", _obs_rows_from_matrix(gs._surge_obs(form, "dc"))),
                ("signal", _obs_rows_from_matrix(gs._surge_obs(form, "signal")))]
    if code == "VOLTAGEDIPS":
        out = []
        for kind, hint in (("dips", "dips"), ("intr", "interrupt")):
            rows = []
            for grp in gs._vdips_groups(form, kind):
                for r in grp.get("rows") or []:
                    rows.append([r.get("pct", ""), r.get("dur", ""), r.get("obs", "")])
            out.append((hint, rows))
        return out
    if code == "ESD":
        return [("indirect", _esd_rows(form, "ind", 8, named=False)),
                ("direct", _esd_rows(form, "dir", 3, named=True)),
                ("air", _esd_rows(form, "air", 3, named=True))]
    if code == "RS_RI":
        return [("", _rs_rows(form))]
    if code == "PFMF":
        return [("", _pfmf_rows(form))]
    if code == "CRF":
        return [("power", _crf_rows(form, "power")),
                ("signal", _crf_rows(form, "signal"))]
    return []


_ESD_INDIRECT_POINTS = ["HCP (0°)", "HCP (90°)", "HCP (180°)", "HCP (270°)",
                        "VCP (0°)", "VCP (90°)", "VCP (180°)", "VCP (270°)"]


def _esd_rows(form, prefix, count, named):
    """ESD observation rows: S.No, test point, then the 6 test-level cells."""
    rows = []
    for i in range(1, count + 1):
        if named:
            point = _val(form, "%s_r%d_name" % (prefix, i))
        else:
            point = _ESD_INDIRECT_POINTS[i - 1] if i - 1 < len(_ESD_INDIRECT_POINTS) else ""
        cells = [_val(form, "%s_r%d_c%d" % (prefix, i, c)) for c in range(1, 7)]
        if not point and not any(cells):
            continue
        rows.append([str(i), point] + cells)
    return rows


def _rs_rows(form):
    """RS observation: frequency band, test level, dwell, then 8 angle cells."""
    bands = [("f_80_to_1000", "80 to 1000"), ("f_1000_to_6000", "1000 to 6000"),
             ("f_ism", "ISM Band(1)")]
    rows = []
    for base, label in bands:
        level = _val(form, base + "_col_1")
        dwell = _val(form, base + "_col_2")
        cells = [_val(form, "%s_col_%d" % (base, c)) for c in range(3, 11)]
        if not level and not dwell and not any(cells):
            continue
        rows.append([label, level, dwell] + cells)
    return rows


def _pfmf_rows(form):
    """PFMF observation: field strength, power frequency, then 7 orientations."""
    rows = []
    for base, freq in (("pf_50", "50 Hz"), ("pf_60", "60 Hz")):
        strength = _val(form, base + "_col_1")
        cells = [_val(form, "%s_col_%d" % (base, c)) for c in range(3, 10)]
        if not strength and not any(cells):
            continue
        rows.append([strength, _val(form, base + "_col_2") or freq] + cells)
    return rows


def _crf_rows(form, side):
    """CRF observation rows for one port, from the schema's row-loop table."""
    key = "test_observation_rows"
    cols = [form.get("%s__c%d[]" % (key, i)) or [] for i in range(5)]
    cols = [c if isinstance(c, list) else [c] for c in cols]
    n = max((len(c) for c in cols), default=0)
    rows = []
    for i in range(n):
        row = [str(cols[j][i]).strip() if i < len(cols[j]) else "" for j in range(5)]
        if not any(row):
            continue
        port = row[1].lower()
        is_signal = "signal" in port
        if (side == "signal") != is_signal:
            continue
        rows.append(row)
    return rows


def observation_legend(code, form):
    """[(code, description)] for the A/B/C/D legend under an observation grid."""
    prefixes = {"EFT": "eft_obs_legend", "SURGE": "surge_obs_legend",
                "PFMF": "pfmf_obs_legend"}
    base = prefixes.get(code, "obs_legend")
    codes = form.get(base + "_code[]") or []
    descs = form.get(base + "_desc[]") or []
    codes = codes if isinstance(codes, list) else [codes]
    descs = descs if isinstance(descs, list) else [descs]
    out, seen = [], set()
    for i, c in enumerate(codes):
        c = str(c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append((c, str(descs[i]).strip() if i < len(descs) else ""))
    return out


# --------------------------------------------------------------------------
# generic repeating tables
# --------------------------------------------------------------------------

def table_rows(form, key, ncols):
    """Rows of a datasheet repeating table (``key__c0[]`` .. ``key__cN[]``)."""
    cols = [form.get("%s__c%d[]" % (key, i)) or [] for i in range(ncols)]
    cols = [c if isinstance(c, list) else [c] for c in cols]
    n = max((len(c) for c in cols), default=0)
    rows = []
    for i in range(n):
        row = [str(cols[j][i]).strip() if i < len(cols[j]) else "" for j in range(ncols)]
        if any(row):
            rows.append(row)
    return rows


def equipment_rows(code, form):
    """TEST EQUIPMENT USED rows: name, make, model, serial, calibration due."""
    if code == "CE":
        return _ce_arrays(form, "eq_", ["name", "make", "model", "serial", "cal_due"])
    return table_rows(form, "test_equipment_used_rows", 5)


def software_rows(code, form, test_name):
    """SOFTWARE USED rows. The report adds a leading 'Test Name' column that the
    datasheet does not have, so the test's display name is prepended."""
    if code == "CE":
        rows = _ce_arrays(form, "sw_", ["name", "version"])
    else:
        rows = table_rows(form, "software_used_rows", 2)
    return [[test_name] + r[:2] for r in rows]


def _ce_arrays(form, prefix, names):
    """Rows from the bespoke CE form's parallel ``prefix+name[]`` arrays."""
    cols = [form.get(prefix + n + "[]") or [] for n in names]
    cols = [c if isinstance(c, list) else [c] for c in cols]
    n = max((len(c) for c in cols), default=0)
    rows = []
    for i in range(n):
        row = [str(cols[j][i]).strip() if i < len(cols[j]) else "" for j in range(len(names))]
        if any(row):
            rows.append(row)
    return rows


# --------------------------------------------------------------------------
# RESULT
# --------------------------------------------------------------------------

def result_text(code, form, class_type=""):
    """The emission tests' one-line RESULT sentence, with class and verdict."""
    verdict = (_val(form, "overall_result") or _val(form, "result")
               or _val(form, "result_pass_fail") or "").upper()
    cls = _val(form, "result_class") or re.sub(r"^class\s*", "", class_type, flags=re.I)
    sentences = {
        "CE": "Conducted Emissions from the EUT as per Class %s limit: %s",
        "RE": "Radiated Emissions from the EUT as per Class %s limit: %s",
        "HARMONIC": "Harmonic Current Emissions from the EUT as per Class %s limit: %s",
    }
    if code == "VOLTAGEFLICKER":
        return "Voltage fluctuations and flicker emissions from the EUT as per: %s" % (verdict or "")
    tmpl = sentences.get(code)
    if not tmpl:
        return ""
    return tmpl % (cls or "", verdict or "")


def result_criteria(form):
    """(required, met) performance criteria for the immunity tests' RESULT table."""
    return (_val(form, "required_performance_criteria"),
            _val(form, "met_performance_criteria"))


def vdips_result_rows(form):
    """Voltage Dips RESULT matrix: required then met, per level column."""
    def _arr(key):
        v = form.get(key) or []
        return [str(x).strip() for x in (v if isinstance(v, list) else [v])]
    return _arr("vdips_req_criteria[]"), _arr("vdips_met_criteria[]")


# --------------------------------------------------------------------------
# TEST SETUP PICTURES / MEASUREMENT DATA images
# --------------------------------------------------------------------------

def caption_key(caption):
    """Normalised caption text with its 'Photo 3:' / 'Figure 4:' prefix removed.

    The report and the datasheets caption the same picture identically once the
    auto-numbered prefix is dropped ("RS test setup_Vertical_80 MHz - 1GHz"),
    which is what lets images be matched by meaning rather than by position.
    """
    s = re.sub(r"^\s*(photo|figure|table)\s*\d*\s*[:.]?\s*", "", caption or "",
               flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def image_captions(code):
    """{normalised caption: form key} for a test's captioned images."""
    out = {}
    if code == "CE":
        # the bespoke CE form has fixed slots rather than schema image items
        out[caption_key("CE test setup_Power Port")] = "photo_setup"
        return out
    from datasheet_gen.registry import load_schema
    schema = load_schema(code)
    for sec in schema.get("sections", []):
        for it in sec.get("items", []):
            items = []
            if it.get("type") == "fields":
                items = [f for f in it.get("fields", []) if f.get("input") == "image"]
            elif it.get("type") == "image" or (it.get("type") == "field"
                                               and it.get("input") == "image"):
                items = [it]
            for f in items:
                k = caption_key(f.get("label") or f["key"])
                if k:
                    out.setdefault(k, f["key"])
    return out


# Image keys that must never be poured into a picture slot: a signature belongs
# on the cover's sign-off block, not among the test photographs.
NON_FIGURE_KEYS = ("signature",)

# Ordering groups: setup photographs first (they map to the report's Photo
# captions), then measurement plots (Figure captions), then functional-check
# captures, then anything else. Within a group, by trailing number.
_KEY_GROUPS = (
    (r"^photo_setup$", 0),
    (r"^img_photo_\d+$", 1),
    (r"^(ce|re)_extra_photo_\d+$", 2),
    (r"^plot_[a-z]+(_avg)?_\d+$", 3),
    (r"^plot_extra_\d+_\d+$", 4),
    (r"^img_figure_\d+$", 5),
    (r"^img_fc_\d+$", 6),
    (r"^img_functional_check$", 7),
)


# What a slot is for. A "Photo N:" caption must never be filled with a
# measurement plot, and a "Figure N:" caption must never be filled with a test
# setup photograph - so the positional fallback is restricted by kind.
KIND_PHOTO = "photo"
KIND_PLOT = "plot"
KIND_CHECK = "check"

_KEY_KINDS = (
    (r"^photo_setup$", KIND_PHOTO),
    (r"^img_photo_\d+$", KIND_PHOTO),
    (r"^(ce|re)_extra_photo_\d+$", KIND_PHOTO),
    (r"^plot_", KIND_PLOT),
    (r"^img_figure_\d+$", KIND_PLOT),
    (r"^img_fc_\d+$", KIND_CHECK),
    (r"^img_functional_check$", KIND_CHECK),
)


def image_kind(key):
    """'photo' | 'plot' | 'check' | '' for an image form key."""
    for pattern, kind in _KEY_KINDS:
        if re.match(pattern, key or ""):
            return kind
    return ""


def ordered_image_keys(code, images, exclude=(), kinds=None):
    """Every usable image key of a test, in the order it should be consumed.

    Used both as the positional fallback when a caption's text does not match,
    and to find the images a datasheet captured beyond the report's printed
    slots (which the builder then appends). Signatures are always excluded.

    ``kinds`` restricts the result to certain slot kinds - pass (KIND_PLOT,) for
    a Figure caption and (KIND_PHOTO,) for a Photo caption, so a setup photo is
    never dropped into a measurement-plot slot or vice versa.
    """
    def _rank(k):
        for pattern, group in _KEY_GROUPS:
            if re.match(pattern, k):
                m = re.findall(r"\d+", k)
                return (group, int(m[-1]) if m else 0, k)
        return (99, 0, k)

    keys = [k for k in images
            if k not in exclude and k not in NON_FIGURE_KEYS]
    if kinds:
        keys = [k for k in keys if image_kind(k) in kinds]
    return sorted(keys, key=_rank)


def ordered_photo_keys(code, images):
    """Backwards-compatible alias for ``ordered_image_keys``."""
    return ordered_image_keys(code, images)
