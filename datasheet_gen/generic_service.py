"""Schema-driven context building + prefill for the generic datasheet engine."""
from .service import _join, _fmt_supply, _ra, _eut_config, as_checkbox_line  # reuse CE helpers

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


def build_context(schema, form_data):
    """Map the posted form into the docxtpl context for this schema."""
    ctx = {}
    # scalar fields (incl. those inside 'fields' groups); images set by the generator
    for f in iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
        val = _s(form_data.get(f["key"]))
        # Fields declared with a "checkbox" option list render as human-ticked
        # checkboxes (their template placeholder is {{r key }}).
        if f.get("checkbox"):
            from .layout import human_checkbox
            val = human_checkbox(val, f["checkbox"])
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
                ctx[f["key"] + "_caption"] = _s(form_data.get(f["key"] + "_caption"))
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
    return ctx


def collect_prefill(schema, request_obj, assignment):
    """Best-effort auto-fill from the request, mapped onto this schema's field keys.

    Mirrors the values CE auto-fills (job/TCO, EUT name/model/serial, product
    standard, supply voltage/frequency, tested-by, deviation). Model/serial fall
    back to the request's own scalar columns when the multi-valued child rows are
    empty (e.g. a single-model request), so prefill works regardless of how the
    request was captured.
    """
    job = name = model = serial = standard = vf = monitoring = test_mode = cfg = ""
    if request_obj is not None:
        job = _ra(request_obj, "job_number", "tco_id")
        name = _ra(request_obj, "product_name")
        # Primary Product-Identity columns first, multi-valued child rows as fallback.
        model = _ra(request_obj, "model_number") or _join(getattr(request_obj, "additional_models", []), "model_number")
        serial = _ra(request_obj, "serial_number") or _join(getattr(request_obj, "serial_numbers", []), "serial_number")
        standard = _join(getattr(request_obj, "product_standards", []), "standard_value")
        vf = _fmt_supply(getattr(request_obj, "supply_vf_values", []))
        monitoring = _ra(request_obj, "monitoring_parameters")
        test_mode = _ra(request_obj, "test_configuration", "operation_modes")
        cfg = _eut_config(request_obj)  # 'Tabletop' / 'Floor standing' / ''
    eng = _s(getattr(assignment, "test_person_name", "")) if assignment else ""
    detail = _test_detail(request_obj, schema.get("code"))

    pre = {}
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
            if (schema.get("code") or "").upper() == "RE":
                from .service import basic_standard_for
                pre[f["key"]] = basic_standard_for(standard) or default   # derived from Product Standard
            else:
                pre[f["key"]] = "Sysmex"      # manager: baseline basic standard
        elif "monitoring_parameters" in k:
            pre[f["key"]] = monitoring
        elif "voltage" in k and "frequency" in k:
            pre[f["key"]] = vf
        elif k == "test_mode":
            pre[f["key"]] = test_mode
        elif "modification_state" in k:
            pre[f["key"]] = "0 - Initial state"   # manager: modification defaults to 0
        elif k.startswith("eut_configuration"):
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
        elif k == "deviation":
            pre[f["key"]] = "NA"
    return pre


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
            Equipment.test_name.isnot(None),
            Equipment.test_name.ilike(f"%{code}%"),
        ).order_by(Equipment.sl_no.asc(), Equipment.name.asc()).all()
    except Exception:
        return []
    rows = []
    for eq in candidates:
        tokens = [t.strip().upper() for t in (eq.test_name or "").split(",")]
        if code not in tokens:
            continue
        cd = getattr(eq, "calibration_due_date", None)
        rows.append({
            "c0": _s(eq.name), "c1": _s(eq.make), "c2": _s(eq.model_no),
            "c3": _s(eq.serial_no), "c4": cd.isoformat() if cd else "",
        })
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
            if it.get("type") == "table" and "equipment" in it["key"].lower():
                rows = _equipment_rows_for(code)
                if rows:
                    out[it["key"]] = rows
    if code == "RE":
        from . import re_logic
        ps = _join(getattr(request_obj, "product_standards", []), "standard_value") if request_obj else ""
        cls = _ra(request_obj, "class_type")
        qp, pa = re_logic.limit_rows(ps, cls)
        if qp:
            out["re_limits_qp_rows"] = qp
        if pa:
            out["re_limits_pa_rows"] = pa
        out.setdefault("software_used_rows", [{"c0": "TDK Emission Lab", "c1": "14.43"}])
        # EUT Modification Record defaults to a single 'initial state' row (like CE)
        out.setdefault("eut_modification_rec_rows", [{"c0": "0", "c1": "Initial state", "c2": "", "c3": ""}])
    return out
