# -*- coding: utf-8 -*-
"""Pure extraction of values out of a posted/saved datasheet form.

WHY THIS LIVES HERE
-------------------
These helpers know how a datasheet FORM is shaped - the flat ``key__c0[]``
arrays, the per-cell observation grids that are posted but never declared in the
JSON schemas (``ind_r3_c5``, ``surge_obs_ac_2__c7``), the CE form's parallel
``eq_name[]`` arrays. That is datasheet knowledge, not report knowledge, so it
belongs in this package.

It used to live in ``report_gen/mapping.py``. Leaving it there would have forced
``datasheet_gen.projection`` to import ``report_gen``, while ``report_gen``
already imports ``datasheet_gen`` in six places - a circular dependency. Moving
it keeps the arrow pointing one way:

    report_gen  ->  datasheet_gen        (never the reverse)

Nothing here touches Word, the database, or Flask: given a form dict it returns
plain Python. That makes both the report builder and the projection testable
without either.
"""
import re


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
    return [(g["hint"], g["rows"]) for g in observation_grids(code, form)]


def observation_grids(code, form):
    """The observation grids with the metadata ``observation_tables`` drops.

    Each entry is ``{hint, cols, rows, label_cols, groups}``:

    * ``cols``       - column headings, where the form posts them (EFT and
                       SURGE build theirs from the selected test levels, so the
                       headings ARE data: "+/-0.5 kV", "CM L->PE 0deg").
                       "" for a grid whose headings are fixed in the template.
    * ``label_cols`` - how many leading columns identify the row rather than
                       carry a measurement. ESD spends two (S.No + test point),
                       RS three (band + level + dwell), EFT one.
    * ``groups``     - per-row group name, or None. Only VOLTAGEDIPS has one:
                       its rows are posted per phase-angle combination, and the
                       report flattens them, but the combination is the whole
                       point of the row so the projection keeps it.

    Kept beside ``observation_tables`` rather than inside the projection so the
    report and the projection cannot drift apart about what a grid contains.
    """
    from datasheet_gen import generic_service as gs

    def matrix(hint, m):
        return {"hint": hint, "cols": list((m or {}).get("cols") or []),
                "rows": _obs_rows_from_matrix(m), "label_cols": 1, "groups": None}

    if code == "EFT":
        return [matrix("power", gs._eft_obs(form, "power")),
                matrix("signal", gs._eft_obs(form, "signal"))]
    if code == "SURGE":
        return [matrix(k, gs._surge_obs(form, k)) for k in ("ac", "dc", "signal")]
    if code == "VOLTAGEDIPS":
        out = []
        for kind, hint in (("dips", "dips"), ("intr", "interrupt")):
            rows, groups = [], []
            for grp in gs._vdips_groups(form, kind):
                for r in grp.get("rows") or []:
                    rows.append([r.get("pct", ""), r.get("dur", ""), r.get("obs", "")])
                    groups.append(grp.get("combo", ""))
            out.append({"hint": hint, "rows": rows, "label_cols": 2,
                        "cols": ["Test Level (% Ut)", "Duration (cycles / periods)",
                                 "Observation"], "groups": groups})
        return out
    if code == "ESD":
        cols = ["S.No", "Test Point"] + [""] * 6
        return [{"hint": h, "rows": r, "cols": cols, "label_cols": 2, "groups": None}
                for h, r in (("indirect", _esd_rows(form, "ind", 8, named=False)),
                             ("direct", _esd_rows(form, "dir", 3, named=True)),
                             ("air", _esd_rows(form, "air", 3, named=True)))]
    if code == "RS_RI":
        return [{"hint": "", "rows": _rs_rows(form), "label_cols": 3, "groups": None,
                 "cols": ["Frequency band (MHz)", "Test level (V/m)", "Dwell time"]
                         + [""] * 8}]
    if code == "PFMF":
        return [{"hint": "", "rows": _pfmf_rows(form), "label_cols": 2, "groups": None,
                 "cols": ["Field strength (A/m)", "Power frequency"] + [""] * 7}]
    if code == "CRF":
        cols = ["Frequency range (MHz)", "Port Name", "Test Level (Vrms)",
                "Coupling method", "Observation"]
        return [{"hint": s, "rows": _crf_rows(form, s), "cols": cols,
                 "label_cols": 4, "groups": None} for s in ("power", "signal")]
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


# public alias - `_val` is used everywhere internally, but callers outside this
# module should not reach for an underscore name
value = _val
