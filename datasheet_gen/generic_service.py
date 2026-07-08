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
        elif (schema.get("code") or "").upper() == "RE" and f["key"] == "eut_configuration":
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
    return ctx


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
    fams = re_logic.families(product_standard)
    c = re_logic._norm_class(cls) or "B"
    is30 = (freq == "30MHz-1GHz")
    is16 = (freq == "1GHz-6GHz")
    out = {"re_limit_cispr_qp": [], "re_limit_fcc_qp": [], "re_limit_fcc_pa": []}
    if is30 and "CISPR" in fams:
        out["re_limit_cispr_qp"] = [{"c0": b, "c1": v} for b, v in re_logic._QP_30M_1G.get(("CISPR", c), [])]
    if is30 and "FCC" in fams:
        out["re_limit_fcc_qp"] = [{"c0": b, "c1": v} for b, v in re_logic._QP_30M_1G.get(("FCC", c), [])]
    if is16:
        out["re_limit_fcc_pa"] = [{"c0": b, "c1": p, "c2": a} for b, p, a in re_logic._PA_1G_6G.get(("FCC", c), [])]
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
    s30 = (freq == "30MHz-1GHz")
    s16 = (freq == "1GHz-6GHz")
    DASH = "-"

    def band(v30, v16):
        return (v30 if s30 else DASH), (v16 if s16 else DASH)

    cols = {}
    # real ticked/unticked checkboxes (RunsXml -> {{r ... }}), same rendering as Classification
    cols["frequency_range_col_1"] = human_checkbox("30MHz-1GHz" if s30 else "", ["30MHz-1GHz"])
    cols["frequency_range_col_2"] = human_checkbox("1GHz-6GHz" if s16 else "", ["1GHz-6GHz"])
    cols["resolution_bandwidth_col_1"], cols["resolution_bandwidth_col_2"] = band("120k", "1M")
    cols["video_bandwidth_col_1"], cols["video_bandwidth_col_2"] = band("1M", "3M")
    cols["step_size_col_1"], cols["step_size_col_2"] = band("40k", "400k")
    cols["turn_table_rotation_step_col_1"], cols["turn_table_rotation_step_col_2"] = band("15°", "22.5°")
    cols["antenna_height_variation_step_for_pre_scan_mea_2"], cols["antenna_height_variation_step_for_pre_scan_mea_3"] = band("1", "1")
    cols["antenna_height_variation_for_final_measurement_2"], cols["antenna_height_variation_for_final_measurement_3"] = band("1-4", "1-2")
    cols["pre_scan_measurement_time_col_1"], cols["pre_scan_measurement_time_col_2"] = band("20", "20")
    cols["final_scan_measurement_time_col_1"], cols["final_scan_measurement_time_col_2"] = band("1", "1")
    cols["attenuation_col_1"], cols["attenuation_col_2"] = band("Auto", "Auto")
    # definitional — shown for both bands
    cols["polarization_col_1"] = cols["polarization_col_2"] = "Horizontal and Vertical"
    cols["detector_col_1"] = "Peak and Quasi-peak"
    cols["detector_col_2"] = "Peak and Average"
    return cols


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
        test_mode = _ra(request_obj, "test_configuration", "operation_modes")
        cfg = _eut_config(request_obj)  # 'Tabletop' / 'Floor standing' / ''
    eng = _s(getattr(assignment, "test_person_name", "")) if assignment else ""
    detail = _test_detail(request_obj, schema.get("code"))

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
            if (schema.get("code") or "").upper() == "RE":
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
        
        # Add starter empty rows for the 7-column measurement tables:
        out.setdefault("re_table1_rows", [{"c0": "", "c1": "", "c2": "", "c3": "", "c4": "", "c5": "", "c6": ""}])
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
