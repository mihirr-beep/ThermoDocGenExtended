"""Schema-driven context building + prefill for the generic datasheet engine."""
from .service import _join, _fmt_supply  # reuse CE helpers


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
        ctx[f["key"]] = _s(form_data.get(f["key"]))
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
    job = name = model = serial = standard = vf = ""
    if request_obj is not None:
        job = _s(getattr(request_obj, "job_number", "") or getattr(request_obj, "tco_id", ""))
        name = _s(getattr(request_obj, "product_name", ""))
        model = (_join(getattr(request_obj, "additional_models", []), "model_number")
                 or _s(getattr(request_obj, "model_number", "")))
        serial = (_join(getattr(request_obj, "serial_numbers", []), "serial_number")
                  or _s(getattr(request_obj, "serial_number", "")))
        standard = _join(getattr(request_obj, "product_standards", []), "standard_value")
        vf = _fmt_supply(getattr(request_obj, "supply_vf_values", []))
    eng = _s(getattr(assignment, "test_person_name", "")) if assignment else ""

    pre = {}
    for f in iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
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
        elif "voltage" in k and "frequency" in k:
            pre[f["key"]] = vf
        elif "tested_by" in k or k == "tested_by":
            pre[f["key"]] = eng
        elif k == "deviation":
            pre[f["key"]] = "NA"
    return pre
