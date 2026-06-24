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


def image_keys(schema):
    keys = []
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it["type"] == "image" or (it["type"] == "field" and it.get("input") == "image"):
                keys.append(it["key"])
    return keys


def build_context(schema, form_data):
    """Map the posted form into the docxtpl context for this schema."""
    ctx = {}
    for sec in schema["sections"]:
        for it in sec["items"]:
            t = it["type"]
            if t in ("field", "textarea"):
                if it.get("input") == "image":
                    continue  # image scalars set by the generator
                ctx[it["key"]] = _s(form_data.get(it["key"]))
            elif t == "table":
                cols = [c["key"] for c in it["columns"]]
                arrs = {c: _list(form_data, f"{it['key']}__{c}[]") for c in cols}
                n = max((len(a) for a in arrs.values()), default=0)
                rows = []
                for i in range(n):
                    row = {c: (_s(arrs[c][i]) if i < len(arrs[c]) else "") for c in cols}
                    if any(row.values()):
                        rows.append(row)
                ctx[it["key"]] = rows
            # 'image' and 'static_table' need no context here
    return ctx


def collect_prefill(schema, request_obj, assignment):
    """Best-effort auto-fill from the request, mapped onto this schema's field keys."""
    job = name = model = serial = standard = vf = ""
    if request_obj is not None:
        job = _s(getattr(request_obj, "job_number", "") or getattr(request_obj, "tco_id", ""))
        name = _s(getattr(request_obj, "product_name", ""))
        model = _join(getattr(request_obj, "additional_models", []), "model_number")
        serial = _join(getattr(request_obj, "serial_numbers", []), "serial_number")
        standard = _join(getattr(request_obj, "product_standards", []), "standard_value")
        vf = _fmt_supply(getattr(request_obj, "supply_vf_values", []))
    eng = _s(getattr(assignment, "test_person_name", "")) if assignment else ""

    pre = {}
    for sec in schema["sections"]:
        for it in sec["items"]:
            if it["type"] not in ("field", "textarea"):
                continue
            k = it["key"].lower()
            if "job_number" in k:
                pre[it["key"]] = job
            elif "eut_name" in k:
                pre[it["key"]] = name
            elif "eut_model" in k:
                pre[it["key"]] = model
            elif "eut_serial" in k:
                pre[it["key"]] = serial
            elif "product_standard" in k:
                pre[it["key"]] = standard
            elif "voltage" in k and "frequency" in k:
                pre[it["key"]] = vf
            elif "tested_by" in k or k == "tested_by":
                pre[it["key"]] = eng
            elif k == "deviation":
                pre[it["key"]] = "NA"
    return pre
