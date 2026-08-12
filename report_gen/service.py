# -*- coding: utf-8 -*-
"""Gather everything the IEC-FRM-516 report needs, from the request + datasheets.

Two data sources are combined:

* **the request** (``EMCRequest`` + its 13 child tables) supplies the front
  matter - sections 1 and 2 - because that information came from the customer;
* **the datasheets** (``datasheet_records.form_json`` / ``images_json``, one row
  per planner entry) supply sections 4..14, because that is what the lab
  engineer actually measured and what peer review approved.

The two are joined through ``planner_entries``: a request's planner entry names
a test (``test_name``, e.g. "VoltageFlicker"), and the datasheet record for that
entry holds the filled form. See ``resolve_tests``.

Nothing in here touches Word - it returns plain dicts so the builder (and the
tests) can work without a document.
"""
import json
import os
import re
from datetime import date, datetime

# request-side test_code (iec_emc_request_tests) -> datasheet registry code.
# Inverse of datasheet_gen.generic_service._DETAIL_ATTR, plus the bespoke CE.
REQUEST_CODE_TO_REPORT = {
    "CE": "CE",
    "RE": "RE",
    "ESD": "ESD",
    "HARMONIC": "HARMONIC",
    "FLICKER": "VOLTAGEFLICKER",
    "RS": "RS_RI",
    "EFT": "EFT",
    "SURGE": "SURGE",
    "CRF": "CRF",
    "POWER_FREQ": "PFMF",
    "VOLTAGE_DIPS": "VOLTAGEDIPS",
    # RS_INTERIM has no section in IEC-FRM-516 and is intentionally absent.
}

CANCELLED = "cancelled"
APPROVED = "datasheet_uploaded"


# ==========================================================================
# small helpers
# ==========================================================================

def _s(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        for x in v:
            if x not in (None, ""):
                return str(x).strip()
        return ""
    return str(v).strip()


def _json_rows(rows, attr):
    """Parse a child table's JSON-in-TEXT column into dicts, in sort order."""
    out = []
    for r in sorted(rows or [], key=lambda x: getattr(x, "sort_order", 0) or 0):
        raw = getattr(r, attr, None)
        if not raw:
            continue
        try:
            val = json.loads(raw)
        except (TypeError, ValueError):
            out.append({"_raw": _s(raw)})
            continue
        if isinstance(val, dict):
            out.append(val)
        elif isinstance(val, list):
            out.extend(v for v in val if isinstance(v, dict))
    return out


def _plain_rows(rows, attr):
    """Values of a plain child table column, in sort order, blanks dropped."""
    vals = []
    for r in sorted(rows or [], key=lambda x: getattr(x, "sort_order", 0) or 0):
        v = _s(getattr(r, attr, None))
        if v:
            vals.append(v)
    return vals


def fmt_date(value, fmt="%d %b %Y"):
    """Format a date/datetime/ISO-string for the document; '' when absent."""
    if not value:
        return ""
    if isinstance(value, str):
        for pat in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                value = datetime.strptime(value.strip()[:19], pat)
                break
            except ValueError:
                continue
        else:
            return value.strip()
    try:
        return value.strftime(fmt)
    except Exception:
        return _s(value)


def decode_data_url(value):
    """Bytes of a ``data:image/...;base64,...`` value (block diagram/signature).

    Returns None when the value is absent or not decodable. Plain base64 without
    the data-URL prefix is accepted too.
    """
    raw = _s(value)
    if not raw:
        return None
    import base64
    if raw.startswith("data:"):
        _head, _sep, raw = raw.partition(",")
        if not _sep:
            return None
    raw = re.sub(r"\s+", "", raw)
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    return data if data and len(data) > 32 else None


# ==========================================================================
# per-test datasheet payloads
# ==========================================================================

def _record_form_and_images(record):
    """(form_data, images) parsed out of a datasheet_records row."""
    form, images = {}, {}
    if not record:
        return form, images
    if record.get("form_json"):
        try:
            form = json.loads(record["form_json"]) or {}
        except (TypeError, ValueError):
            form = {}
    if record.get("images_json"):
        try:
            images = json.loads(record["images_json"]) or {}
        except (TypeError, ValueError):
            images = {}
    # only keep images that are still on disk - a missing file must not abort
    images = {k: v for k, v in images.items() if v and os.path.exists(v)}
    return form, images


def _datasheet_path(record, entry):
    """The generated datasheet .docx for this test, or None.

    Checked in order of trust: the record's own generated_file_path is written by
    the generator itself, the planner entry's by the send-for-review step. A path
    that no longer exists on disk is treated as absent rather than returned - the
    caller would only fail on it later, and less clearly.
    """
    for value in ((record or {}).get("generated_file_path"),
                  getattr(entry, "datasheet_file_path", None)):
        p = _s(value)
        if p and os.path.exists(p):
            return p
    return None


def resolve_tests(request_obj, planner_entries, include_without_data=True):
    """Ordered per-test payloads for the report.

    Driven by **the tests on the request form** (``EMCRequest.tests`` where
    ``is_selected``), because that is the agreed scope: once every test is done
    the report carries all of them, pass or fail. Each is then matched to its
    planner entry (by normalised ``test_name``) and to that entry's datasheet
    record.

    A test whose every planner entry is cancelled was not performed, so it is
    dropped. A selected test with no data at all is kept when
    ``include_without_data`` so its section stays in the document as the blank
    form rather than silently disappearing - the caller reports it as a gap.

    Returns (tests, skipped) where each test is a dict:
        code, section, name, entry, record, form, images, has_data
    """
    from datasheet_gen.registry import REGISTRY
    from datasheet_gen import records as R
    from .registry import REPORT_CODE_ORDER, SECTION_BY_CODE

    # planner entries grouped by report code
    by_code = {}
    for e in planner_entries or []:
        code = (e.test_name or "").strip().upper()
        if code not in REGISTRY:
            code = REQUEST_CODE_TO_REPORT.get(code, code)
        if code in REGISTRY:
            by_code.setdefault(code, []).append(e)

    selected = set()
    for t in getattr(request_obj, "tests", []) or []:
        if not getattr(t, "is_selected", False):
            continue
        rc = REQUEST_CODE_TO_REPORT.get(str(getattr(t, "test_code", "")).upper())
        if rc:
            selected.add(rc)
    # a scheduled test that is not flagged on the request still belongs in the
    # report - the schedule is the stronger evidence that it was performed
    selected |= set(by_code)

    tests, skipped = [], []
    for code in REPORT_CODE_ORDER:
        if code not in selected:
            continue
        entries = by_code.get(code, [])
        active = [e for e in entries
                  if str(e.status or "").strip().lower() != CANCELLED]
        if entries and not active:
            skipped.append({"code": code, "reason": "all scheduled runs cancelled"})
            continue

        # prefer the approved entry, then the most recently updated one
        entry = None
        for e in active:
            if str(e.status or "").strip().lower() == APPROVED:
                entry = e
                break
        if entry is None and active:
            entry = active[-1]

        record = R.get_record_for_assignment(entry.id) if entry is not None else None
        form, images = _record_form_and_images(record)
        has_data = bool(form)
        if not has_data and not include_without_data:
            skipped.append({"code": code, "reason": "no saved datasheet data"})
            continue
        tests.append({
            "code": code,
            "section": SECTION_BY_CODE.get(code, code),
            "name": REGISTRY.get(code, (None, code))[1],
            "form_no": REGISTRY.get(code, ("",))[0],
            "entry": entry,
            "record": record,
            "form": form,
            "images": images,
            "has_data": has_data,
            # Where this test's own generated .docx is, if one exists. The
            # builder splices that document's per-test pages into the report
            # rather than filling a second copy of the same tables - see
            # report_gen/splice.py. Two places record it and either will do:
            # the record is written when the datasheet is generated, the planner
            # entry when it is sent for review.
            "datasheet_path": _datasheet_path(record, entry),
        })
        if not has_data:
            skipped.append({"code": code,
                            "reason": "no saved datasheet data (section left blank)"})
    return tests, skipped


# ==========================================================================
# request-level (sections 1 and 2)
# ==========================================================================

def _dimensions(req):
    unit = _s(getattr(req, "dimension_unit", "")) or "mm"
    parts = []
    for attr in ("length", "width", "height"):
        v = getattr(req, attr, None)
        if v in (None, ""):
            continue
        parts.append(("%g" % float(v)) if isinstance(v, (int, float)) else _s(v))
    return (" x ".join(parts) + " " + unit) if parts else ""


def _supply(req):
    """'230 V / 50 Hz, 120 V / 60 Hz' from the supply voltage/frequency rows."""
    out = []
    for row in _json_rows(getattr(req, "supply_vf_values", []), "value_text"):
        v, f = _s(row.get("voltage")), _s(row.get("frequency"))
        if v and f:
            out.append("%s V / %s Hz" % (v, f))
        elif v or f:
            out.append(v or f)
    return ", ".join(out)


def _eut_spec(req):
    """The single EUT electrical spec blob (voltage/frequency/current/power)."""
    rows = _json_rows(getattr(req, "eut_specs", []), "spec_value")
    return rows[0] if rows else {}


def _operating_voltage(req, spec):
    """Operating voltage: prefer the AC/DC ranges captured on the EUT spec."""
    bits = []
    ac = _s(spec.get("acVoltageRange"))
    dc = _s(spec.get("dcVoltageRange"))
    if ac:
        bits.append("AC %s V" % ac)
    if dc:
        bits.append("DC %s V" % dc)
    return ", ".join(bits) or _supply(req)


def _operating_frequency(req, spec):
    acf = _s(spec.get("acFreqRange"))
    if acf:
        return "%s Hz" % acf
    return _s(getattr(req, "operating_frequency", ""))


def _measured_current(spec):
    bits = []
    for key, label in (("acInputCurrent", "AC"), ("dcInputCurrent", "DC")):
        v = _s(spec.get(key))
        if v:
            bits.append("%s %s A" % (label, v))
    return ", ".join(bits)


def accessory_rows(req):
    """[[S.No, Name, Make, Model No., Serial No.], ...] for table 1 of 2.9."""
    rows = []
    for i, a in enumerate(_json_rows(getattr(req, "accessories", []), "accessory_value"), 1):
        rows.append([str(i), _s(a.get("equipmentName") or a.get("name")),
                     _s(a.get("make")), _s(a.get("modelNo") or a.get("model")),
                     _s(a.get("serialNo") or a.get("serial"))])
    return rows


def cable_rows(req):
    """[[S.No, Cable Name, Length(m), Power/Signal, Shielded/Unshielded], ...]."""
    rows = []
    for i, c in enumerate(_json_rows(getattr(req, "cables", []), "cable_value"), 1):
        rows.append([str(i), _s(c.get("cableName") or c.get("name")),
                     _s(c.get("length")), _s(c.get("powerSignal")),
                     _s(c.get("shielded"))])
    return rows


def modification_rows(tests):
    """EUT modification record for 2.4, taken from the datasheets.

    Every datasheet captures the same EUT modification table, so the rows are
    merged across tests and de-duplicated on (state, description).
    """
    seen, rows = set(), []
    for t in tests:
        form = t.get("form") or {}
        cols = [form.get("eut_modification_rec_rows__c%d[]" % i) or [] for i in range(4)]
        cols = [c if isinstance(c, list) else [c] for c in cols]
        n = max((len(c) for c in cols), default=0)
        for i in range(n):
            row = [_s(cols[j][i]) if i < len(cols[j]) else "" for j in range(4)]
            if not any(row):
                continue
            key = (row[0].lower(), row[1].lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        # CE (bespoke) uses its own mod_* arrays
        mcols = [form.get("mod_%s[]" % k) or []
                 for k in ("state", "description", "fitted_by", "date")]
        mcols = [c if isinstance(c, list) else [c] for c in mcols]
        n = max((len(c) for c in mcols), default=0)
        for i in range(n):
            row = [_s(mcols[j][i]) if i < len(mcols[j]) else "" for j in range(4)]
            if not any(row):
                continue
            key = (row[0].lower(), row[1].lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows or [["0", "Initial state", "", ""]]


def test_date_span(tests, req):
    """('from', 'to') the tests were performed, for the cover page.

    Uses the test dates the engineers recorded on the datasheets, falling back
    to the request's commencement/completion dates.
    """
    dates = []
    for t in tests:
        for key in ("test_date", "test_date_col_1"):
            raw = _s((t.get("form") or {}).get(key))
            for piece in re.split(r"[/,]| to ", raw):
                piece = piece.strip()
                if not piece:
                    continue
                for pat in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                            "%d %b %Y"):
                    try:
                        dates.append(datetime.strptime(piece, pat).date())
                        break
                    except ValueError:
                        continue
        rec = t.get("record") or {}
        if isinstance(rec.get("test_date"), date):
            dates.append(rec["test_date"])
    if dates:
        return fmt_date(min(dates)), fmt_date(max(dates))
    return (fmt_date(getattr(req, "test_commencement_date", None)),
            fmt_date(getattr(req, "test_completion_date", None)))


def signoff_names(tests):
    """(prepared_by, reviewed_by) for the cover signature block.

    Prepared By = whoever submitted the datasheets; Reviewed By = the assigned
    peer reviewer. Both are joined when the tests were split between people.
    """
    from models import db, User

    def _names(ids):
        out = []
        for uid in sorted({i for i in ids if i}):
            u = db.session.get(User, uid)
            n = _s(getattr(u, "username", "")) if u else ""
            if n and n not in out:
                out.append(n)
        return ", ".join(out)

    prepared = _names([getattr(t.get("entry"), "datasheet_uploaded_by", None)
                       for t in tests])
    reviewed = _names([getattr(t.get("entry"), "peer_reviewer_user_id", None)
                       for t in tests])
    if not prepared:
        prepared = ", ".join(sorted({
            _s(getattr(t.get("entry"), "test_person_name", "")) for t in tests
            if _s(getattr(t.get("entry"), "test_person_name", ""))}))
    return prepared, reviewed


def measurement_uncertainties(codes):
    """{code: '± x.xxx dB'} from the admin-editable datasheet_fixed_values."""
    out = {}
    try:
        from datasheet_gen.fixed_store import get_fixed_values
    except Exception:
        return out
    for code in codes:
        try:
            vals = get_fixed_values(code) or {}
        except Exception:
            continue
        unc = _s(vals.get("measurement_uncertainty"))
        if unc:
            out[code] = unc
    return out


def collect(request_obj, planner_entries, now=None):
    """The full report payload: request-level values + ordered per-test data."""
    tests, skipped = resolve_tests(request_obj, planner_entries)
    spec = _eut_spec(request_obj)
    frm, to = test_date_span(tests, request_obj)
    prepared, reviewed = signoff_names(tests)
    now = now or datetime.now()

    return {
        "request": request_obj,
        "tests": tests,
        "skipped": skipped,
        "codes": [t["code"] for t in tests],
        "uncertainty": measurement_uncertainties([t["code"] for t in tests]),
        "meta": {
            "tco_id": _s(getattr(request_obj, "tco_id", "")),
            "job_number": _s(getattr(request_obj, "job_number", "")),
            "manufacturer": _s(getattr(request_obj, "manufacturer", "")),
            "manufacturer_address": _s(getattr(request_obj, "manufacturer_address", "")),
            "eut_name": _s(getattr(request_obj, "product_name", "")),
            "eut_model": _s(getattr(request_obj, "model_number", "")),
            "eut_serial": (_s(getattr(request_obj, "serial_number", ""))
                           or ", ".join(_plain_rows(
                               getattr(request_obj, "serial_numbers", []), "serial_number"))),
            "test_samples": _s(getattr(request_obj, "test_samples", "")),
            "dimensions": _dimensions(request_obj),
            "weight": (("%g kg" % float(getattr(request_obj, "weight")))
                       if getattr(request_obj, "weight", None) not in (None, "") else ""),
            "categories": _plain_rows(getattr(request_obj, "categories", []), "category_name"),
            "product_type": _s(getattr(request_obj, "product_type", ""))
                            or _s(getattr(request_obj, "type_others", "")),
            "operating_voltage": _operating_voltage(request_obj, spec),
            "operating_frequency": _operating_frequency(request_obj, spec),
            "power_rating": (("%s W" % _s(spec.get("ratedPower")))
                             if _s(spec.get("ratedPower")) else ""),
            "measured_current": _measured_current(spec),
            "description": _s(getattr(request_obj, "product_description", "")),
            "configuration": _s(getattr(request_obj, "test_configuration", "")),
            "modes": (_plain_rows(getattr(request_obj, "functional_modes", []), "mode_value")
                      or [x for x in re.split(r"[\r\n]+",
                          _s(getattr(request_obj, "operation_modes", ""))) if x.strip()]),
            "monitoring": _s(getattr(request_obj, "monitoring_parameters", "")),
            "product_standards": _plain_rows(
                getattr(request_obj, "product_standards", []), "standard_value"),
            "decision_rules": _plain_rows(
                getattr(request_obj, "decision_rules", []), "rule_value"),
            "block_diagram": decode_data_url(getattr(request_obj, "block_diagram", None)),
            "accessories": accessory_rows(request_obj),
            "cables": cable_rows(request_obj),
            "modifications": modification_rows(tests),
            "sample_condition": _s(getattr(request_obj, "sample_condition", "")),
            "sample_received": fmt_date(getattr(request_obj, "sample_received_date", None)),
            "tests_from": frm,
            "tests_to": to,
            "issue_date": fmt_date(now),
            "requester_name": _s(getattr(request_obj, "requester_name", "")),
            "requester_email": _s(getattr(request_obj, "requester_email", "")),
            "requester_contact": _s(getattr(request_obj, "requester_contact", "")),
            "lab_manager_name": _s(getattr(request_obj, "lab_manager_name", "")),
            "prepared_by": prepared,
            "reviewed_by": reviewed,
            "class_type": _s(getattr(request_obj, "class_type", "")),
            "product_group": _s(getattr(request_obj, "product_group", "")),
        },
    }
