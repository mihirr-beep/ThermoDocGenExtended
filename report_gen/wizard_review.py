# -*- coding: utf-8 -*-
"""Read-only assembly for the wizard's review pages (2, 3 and 4).

WHY EVERY NUMBER HERE COMES FROM THE BUILDER'S OWN CALL
------------------------------------------------------
``build_report`` starts with ``S.collect(request_obj, planner_entries)``. So does
``preview_source`` below, with the planner entries ordered exactly as
``app.generate_test_report`` orders them. A review page that assembled its own
version of "what the report will say" is the two-lists defect that put 49 of 92
per-test subsections out of agreement with the template - and here it would be
worse, because the admin signs off on what this page shows them.

So: 1.1's Results come from ``builder._verdict``, its spec cell from
``builder._test_method_spec``, the kept/deleted rows from
``registry.TEST_METHOD_ROWS``, approval from ``projection.derive_status``, and
the splice decision from ``splice.region_start``. Nothing is recomputed.

THE THREE THINGS collect() DOES NOT KNOW, AND WHY THEY ARE HERE
--------------------------------------------------------------
1. An unfilled 1.1 cell is not blank in the document - the shipped template
   prints its own example there ("<Class A/B>150kHz to 30MHz"), or
   ``cleanup_instructions`` turns a whole-bracket example into "NA". Showing that
   cell as empty would hide the same silent-NA defect this wizard exists to
   remove, one section higher up. ``_template_fallbacks`` reads the real values
   out of the template and ``_after_cleanup`` predicts what survives, using the
   builder's own predicates.
2. 1.4 falls back the same way: ``fill_summary`` writes only ``if unc.get(code)``,
   so a kept row with no ``datasheet_fixed_values.measurement_uncertainty`` ships
   the template's printed figure.
3. ``datasheet.revision_no`` is the *next, live* revision - submitting N freezes
   N and moves the row to N+1. The revision peer review actually approved is
   ``MAX(datasheet_revision.revision_no)``, so showing ``revision_no`` would
   over-report by one on every approved test.

Nothing in this module writes anything.
"""
import logging
import os
import re

from . import builder as B
from . import mapping as M
from . import registry as REG
from . import service as S
from . import draft
from . import wizard_fields as WF

log = logging.getLogger(__name__)


# ==========================================================================
# the one source both these pages and build_report() read
# ==========================================================================

def preview_source(request_id):
    """(request_obj, planner_entries, data) - exactly what build_report() sees.

    The entry ORDER matters and is copied from app.generate_test_report:
    resolve_tests() falls back to ``active[-1]`` when no entry sits at
    'datasheet_uploaded', so a review page that ordered its entries differently
    could preview a datasheet the build would not splice.
    """
    from models import PlannerEntry
    from app import _resolve_request

    req = _resolve_request(request_id)
    if req is None:
        return None, [], None
    entries = PlannerEntry.query.filter_by(test_request_id=int(request_id)).order_by(
        PlannerEntry.start_date.asc(), PlannerEntry.start_time.asc(),
        PlannerEntry.id.asc()).all()
    return req, entries, S.collect(req, entries)


def _request_values(request_obj):
    """The six store="request" fields, read off the ORM object.

    getattr rather than another SELECT: preview_source already loaded the row,
    and this database is remote - the cost that matters is round trips, not rows.
    """
    return {f[0]: getattr(request_obj, f[0], None) for f in WF.by_store("request")}


def _merged_form(request_obj, loaded_draft):
    """Draft values with the request's own columns layered on top.

    Same precedence as wizard_routes.eut_page, so the outstanding list on the
    final page cannot disagree with the one on page 1.
    """
    merged = dict((loaded_draft or {}).get("form") or {})
    row = _request_values(request_obj)
    for key, v in row.items():
        if v not in (None, ""):
            merged[key] = v
    return merged, row


# ==========================================================================
# what the blank template already prints, and what survives cleanup
# ==========================================================================

_TEMPLATE_FALLBACK = None


def _template_fallbacks():
    """{"method": {code: {spec, result}}, "uncertainty": {code: value}}.

    Read POSITIONALLY out of the pristine template: its 1.1 data rows are in
    TEST_METHOD_ROWS order and its 1.4 rows in UNCERTAINTY_ROWS order (verified).
    Matching by label instead would mean keeping a second copy of
    fill_summary's row matcher, which is exactly the drift this wizard avoids.
    A row whose label has moved is skipped rather than guessed at.

    Cached: it is one file read that never changes while the process lives.
    """
    global _TEMPLATE_FALLBACK
    if _TEMPLATE_FALLBACK is not None:
        return _TEMPLATE_FALLBACK
    out = {"method": {}, "uncertainty": {}}
    try:
        from docx import Document
        from . import docx_tools as T
        outline = B.Outline(Document(REG.TEMPLATE_PATH))

        tabs = outline.tables_in("TEST REPORT SUMMARY", "TEST METHOD")
        if tabs:
            for (label, code), row in zip(REG.TEST_METHOD_ROWS, tabs[0].rows[1:]):
                if M.norm_label(T.row_label(row))[:12] != M.norm_label(label)[:12]:
                    continue                       # template drift - do not guess
                cells = T.distinct_cells(row)
                if len(cells) >= 4:
                    out["method"][code] = {
                        "spec": T.full_text(cells[1]).strip(),
                        "result": T.full_text(cells[3]).strip()}

        tabs = outline.tables_in("TEST REPORT SUMMARY", "MEASUREMENT UNCERTAINITY")
        if tabs:
            for (label, code), row in zip(REG.UNCERTAINTY_ROWS, tabs[0].rows[1:]):
                if M.norm_label(T.row_label(row))[:12] != M.norm_label(label)[:12]:
                    continue
                cells = T.distinct_cells(row)
                if len(cells) >= 2:
                    out["uncertainty"][code] = T.full_text(cells[-1]).strip()
    except Exception as exc:  # noqa: BLE001 - a preview must not need the template
        log.info("wizard review: template fallbacks unavailable: %s", exc)
    _TEMPLATE_FALLBACK = out
    return out


def _after_cleanup(text):
    """What an untouched template cell actually prints, once cleanup has run.

    Uses the builder's own predicates - _is_instruction, _INLINE_NOISE,
    NOT_APPLICABLE - so this cannot drift from the pass that really does it
    (builder.cleanup_instructions, the cell loop).
    """
    txt = (text or "").strip()
    if not txt:
        return ""
    if B._is_instruction(txt):
        return B.NOT_APPLICABLE
    cleaned = txt
    for pattern, repl in B._INLINE_NOISE:
        cleaned = pattern.sub(repl, cleaned)
    if cleaned == txt:
        return txt
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip() or B.NOT_APPLICABLE


def _sourced(value, fallback):
    """(text, source) for one 1.1 cell.

    source is "datasheet" when the build writes it, "template" when the build
    leaves the template's own example standing, and "" when the cell is genuinely
    empty in the document.
    """
    if value:
        return value, "datasheet"
    shipped = _after_cleanup(fallback)
    if shipped:
        return shipped, "template"
    return "", ""


# ==========================================================================
# page 2: section 1
# ==========================================================================

def method_rows(data):
    """1.1 TEST METHOD - exactly the rows fill_summary() will keep, in its order."""
    meta, tests = data["meta"], data["tests"]
    by_code = {t["code"]: t for t in tests}
    tpl = _template_fallbacks()["method"]
    rows = []
    for label, code in REG.TEST_METHOD_ROWS:
        t = by_code.get(code)
        if t is None:
            continue                        # fill_summary removes this row entirely
        fb = tpl.get(code) or {}
        try:
            spec = B._test_method_spec(code, t["form"], meta)
        except Exception as exc:  # noqa: BLE001 - one odd datasheet, not the page
            log.info("wizard review: 1.1 spec failed for %s: %s", code, exc)
            spec = ""
        try:
            verdict = B._verdict(t["form"])
        except Exception as exc:  # noqa: BLE001
            log.info("wizard review: 1.1 verdict failed for %s: %s", code, exc)
            verdict = ""
        spec_text, spec_src = _sourced(spec, fb.get("spec"))
        res_text, res_src = _sourced(verdict, fb.get("result"))
        rows.append({"code": code, "test": label,
                     "spec": spec_text, "spec_source": spec_src,
                     "port": REG.TEST_METHOD_PORT.get(code, ""),
                     "result": res_text, "result_source": res_src})
    return rows


def omitted_method_rows(data):
    """The 1.1 rows fill_summary() will delete, so the admin sees the shrink."""
    codes = {t["code"] for t in data["tests"]}
    return [{"code": code, "test": label}
            for label, code in REG.TEST_METHOD_ROWS if code not in codes]


def standards(data):
    """1.2 APPLICABLE STANDARDS as (product_standards, basic_standards).

    _fill_standards() computes and writes in one function, so there is no helper
    to call for the list alone; this repeats its split-and-dedup loop. The clean
    fix is to extract standards_lists() from _fill_standards and have both call
    it - out of scope here, this phase adds no edits to builder.py.
    """
    meta, tests = data["meta"], data["tests"]
    products = list(meta.get("product_standards") or [])
    basics, seen = [], set()
    for t in tests:
        for part in re.split(r"\s*[&;]\s*", M._val(t["form"], "basic_standard")):
            part = part.strip()
            if part and part.lower() not in seen:
                seen.add(part.lower())
                basics.append(part)
    return products, basics


def uncertainty_rows(data):
    """1.4 MEASUREMENT UNCERTAINITY - kept rows only, with their printed value."""
    codes = {t["code"] for t in data["tests"]}
    unc, tpl = data["uncertainty"], _template_fallbacks()["uncertainty"]
    rows = []
    for label, code in REG.UNCERTAINTY_ROWS:
        if code not in codes:
            continue                        # fill_summary removes this row
        value, source = _sourced(unc.get(code, ""), tpl.get(code))
        rows.append({"code": code, "test": label, "value": value,
                     "source": source})
    return rows


def summary_preview(request_id):
    """Everything section 1 will contain, as the document will contain it."""
    req, entries, data = preview_source(request_id)
    if req is None:
        return {"ok": False, "reason": "Request not found"}
    products, basics = standards(data)
    rows = method_rows(data)
    unc = uncertainty_rows(data)
    notes = []
    if not rows:
        notes.append("No test has data, so 1.1 TEST METHOD will have no data rows "
                     "at all - the build raises \"no completed tests\" first.")
    if any(r["spec_source"] == "template" or r["result_source"] == "template"
           for r in rows):
        notes.append("Cells marked \"from the template\" are not sourced from any "
                     "datasheet. The document prints the blank form's own example "
                     "there, or NA - the same defect the EUT page exists to remove, "
                     "one section higher. Fix it on the datasheet, not here.")
    if not products and not basics:
        notes.append("1.2 APPLICABLE STANDARDS will be left exactly as the template "
                     "ships it: _fill_standards returns without writing when both "
                     "lists are empty.")
    if not unc:
        notes.append("1.4 MEASUREMENT UNCERTAINITY will be a heading and a sentence "
                     "with no table rows: it covers the four emission tests only, "
                     "and this request has none of them.")
    return {
        "ok": True,
        "tco_id": S._s(getattr(req, "tco_id", "")),
        "product": S._s(getattr(req, "product_name", "")),
        "method": rows,
        "omitted": omitted_method_rows(data),
        "standards": {
            "products": products, "basics": basics,
            # the same arithmetic _fill_standards uses to size the table
            "rows": max(-(-len(products) // 2), -(-len(basics) // 2), 1),
        },
        "uncertainty": unc,
        "codes": data["codes"],
        "skipped": data["skipped"],
        "notes": notes,
        "entries": len(entries),
    }


# ==========================================================================
# page 3: section 3 (the criteria note) and sections 4..N (one per test)
# ==========================================================================

def _lifecycle(tests):
    """{planner_entry_id: datasheet projection row} in ONE query.

    One round trip for every test rather than one per test, and best-effort: the
    `datasheet` table is a queryable mirror of form_json, not the truth, so a
    review page must still render when it is missing or stale.
    """
    ids = [getattr(t["entry"], "id", None) for t in tests
           if t.get("entry") is not None]
    ids = [i for i in ids if i]
    if not ids:
        return {}
    from sqlalchemy import text
    from models import db
    sql = ("SELECT d.id, d.planner_entry_id, d.status, d.revision_no, d.result, "
           "d.decided_at, d.peer_reviewer_name, "
           "(SELECT MAX(v.revision_no) FROM datasheet_revision v "
           " WHERE v.datasheet_id = d.id) AS approved_revision "
           "FROM `datasheet` d WHERE d.planner_entry_id IN (%s)"
           % ", ".join(":p%d" % i for i in range(len(ids))))
    try:
        rows = db.session.execute(
            text(sql), {"p%d" % i: v for i, v in enumerate(ids)}).mappings()
        return {r["planner_entry_id"]: dict(r) for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.info("wizard review: datasheet projection unavailable: %s", exc)
        return {}


def _splice_check(path, code):
    """(will_splice, note). Cheap pre-check of the one failure extract_region raises.

    build_report splices when datasheet_path is set and silently falls back to
    fill_test_section on ANY exception, so without this the page could promise
    "your approved pages will be used" and be wrong.
    """
    from . import splice as SPL
    if not path:
        return False, "no generated datasheet on disk"
    try:
        from docx import Document
        if SPL.region_start(Document(path), code) is not None:
            return True, ""
        return False, ("no %r heading in the file - the report falls back to its "
                       "own blank template section"
                       % SPL.TEST_HEADING.get(code, code))
    except Exception as exc:  # noqa: BLE001
        return False, "the file cannot be read (%s)" % exc


def criteria_note(data):
    """Section 3 - what tick_decision_rules() will and will not mark.

    3.1 PERFORMANCE CRITERIA is static prose and no code touches it; 3.2 DECISION
    RULE is ticked from meta["decision_rules"], and tick_decision_rules() returns
    immediately on an empty list. Saying so is this page's only real job for
    section 3 - an untricked 3.2 is otherwise invisible until someone reads the
    finished PDF.
    """
    selected = list(data["meta"].get("decision_rules") or [])
    labels = B._DECISION_RULE_LABELS
    ticked = [labels.get(r, r) for r in selected]
    ticked_norm = {M.norm_label(x) for x in ticked}
    return {
        "ticked": ticked,
        "unticked": [v for v in labels.values()
                     if M.norm_label(v) not in ticked_norm],
        "none": not ticked,
        "criteria_letters": "A, B and C",
        # The template has no paragraph for criterion D, yet _verdict prints
        # whatever the datasheet recorded - request 15's EFT reports D.
        "no_d_paragraph": True,
    }


def tests_preview(request_id):
    """Per test: datasheet, revision, result, approval, splice-or-fallback."""
    req, _entries, data = preview_source(request_id)
    if req is None:
        return {"ok": False, "reason": "Request not found"}
    from datasheet_gen import projection as P

    lifecycle = _lifecycle(data["tests"])
    out = []
    for t in data["tests"]:
        entry, rec = t.get("entry"), (t.get("record") or {})
        life = lifecycle.get(getattr(entry, "id", None), {})
        path = t.get("datasheet_path")
        will_splice, note = _splice_check(path, t["code"])
        try:
            status = P.derive_status(entry, rec)
        except Exception as exc:  # noqa: BLE001
            log.info("wizard review: derive_status failed for %s: %s", t["code"], exc)
            status = "Unknown"
        required = M._val(t["form"], "required_performance_criteria")
        met = M._val(t["form"], "met_performance_criteria")
        try:
            verdict = B._verdict(t["form"])
        except Exception:  # noqa: BLE001
            verdict = ""
        out.append({
            "code": t["code"],
            "section": t["section"],
            "name": t["name"],
            "form_no": t.get("form_no"),
            "datasheet": os.path.basename(path) if path else None,
            "datasheet_path": path,
            # the frozen revision peer review saw, not datasheet.revision_no
            "revision": life.get("approved_revision"),
            "live_revision": life.get("revision_no"),
            "result": verdict,
            "recorded_result": S._s(life.get("result") or rec.get("result") or ""),
            "approved": status == "Approved",
            "review_status": status,
            "entry_status": getattr(entry, "status", None),
            "reviewer": S._s(life.get("peer_reviewer_name") or ""),
            "decided_at": life.get("decided_at"),
            "has_data": t["has_data"],
            "images": len(t.get("images") or {}),
            "will_splice": will_splice,
            "source": ("the approved datasheet's own pages are spliced in"
                       if will_splice
                       else "filled into the report's blank template section"),
            "note": note,
            "required_criterion": required,
            "met_criterion": met,
            # Nothing in the builder compares these two, so a test that met a
            # weaker criterion than it required still prints its letter in 1.1
            # with nothing marking it as a miss.
            "criterion_mismatch": bool(required and met
                                       and required.strip().upper() != met.strip().upper()),
        })
    return {"ok": True,
            "tco_id": S._s(getattr(req, "tco_id", "")),
            "product": S._s(getattr(req, "product_name", "")),
            "tests": out,
            "skipped": data["skipped"],
            "criteria": criteria_note(data)}


# ==========================================================================
# page 4: can the existing endpoint actually succeed?
# ==========================================================================

def latest_report(request_id, entries=None):
    """The most recent generated report .docx for this request, or None.

    Two sources because either can be the newer one: the planner entries carry
    report_file_path from the last generate, and the directory holds every run.
    """
    from flask import current_app
    candidates = []
    for e in entries or []:
        p = getattr(e, "report_file_path", None)
        if p and os.path.exists(p):
            candidates.append(p)
    try:
        folder = os.path.join(
            current_app.config.get("UPLOAD_FOLDER", "uploads"), "reports",
            str(int(request_id)))
        if os.path.isdir(folder):
            candidates.extend(os.path.join(folder, n) for n in os.listdir(folder)
                              if n.lower().endswith(".docx"))
    except Exception as exc:  # noqa: BLE001 - a missing folder is not an error
        log.info("wizard review: report folder unreadable for %s: %s",
                 request_id, exc)
    if not candidates:
        return None
    return max(set(candidates), key=lambda p: os.path.getmtime(p))


def readiness(request_id):
    """{"ready", "blockers", "outstanding"} + what the final page needs to say.

    blockers are the generate endpoint's OWN preconditions, evaluated without
    calling it - no planner entries (404), no submitted datasheet record (400),
    no resolvable tests (build_report raises ValueError -> 400). outstanding is
    wizard_fields.outstanding(): field gaps, which do NOT block a build, they
    just come out blank or NA. Conflating the two would either refuse to generate
    a report the lab is entitled to, or promise a complete one that is not.
    """
    from . import render as R

    req, entries, data = preview_source(request_id)
    if req is None:
        return {"ready": False, "blockers": ["Request not found"],
                "outstanding": [], "ok": False, "reason": "Request not found"}

    from datasheet_gen import records as DR
    active = [e for e in entries
              if str(getattr(e, "status", "") or "").strip().lower() != S.CANCELLED]
    with_data = []
    for e in active:
        try:
            if DR.get_record_for_assignment(e.id):
                with_data.append(e)
        except Exception as exc:  # noqa: BLE001
            log.info("wizard review: record lookup failed for entry %s: %s",
                     e.id, exc)

    blockers = []
    if not entries:
        blockers.append("This request has no planner entries, so there is nothing "
                        "to report on (the endpoint returns 404).")
    if not with_data:
        blockers.append("No submitted datasheet data was found. Complete and "
                        "approve the datasheets first (the endpoint returns 400).")
    if not data["tests"]:
        blockers.append("No test resolved to a section, so build_report raises "
                        "\"no completed tests\" (the endpoint returns 400).")

    d = draft.load(request_id)
    merged, row = _merged_form(req, d)
    left = WF.outstanding(merged, row)

    warnings = []
    unapproved = []
    from datasheet_gen import projection as P
    for t in data["tests"]:
        try:
            if P.derive_status(t.get("entry"), t.get("record") or {}) != "Approved":
                unapproved.append(t["code"])
        except Exception:  # noqa: BLE001
            unapproved.append(t["code"])
    if unapproved:
        warnings.append("Not peer-approved yet: %s. The report will still be built "
                        "from whatever those datasheets currently hold."
                        % ", ".join(unapproved))
    no_data = [t["code"] for t in data["tests"] if not t["has_data"]]
    if no_data:
        warnings.append("No saved datasheet data for %s - those sections ship as "
                        "the blank form." % ", ".join(no_data))
    no_file = [t["code"] for t in data["tests"] if not t.get("datasheet_path")]
    if no_file:
        warnings.append("No generated datasheet on disk for %s, so the report "
                        "fills its own template section instead of splicing the "
                        "approved pages." % ", ".join(no_file))
    if data["skipped"]:
        warnings.append("Dropped from the report: %s."
                        % "; ".join("%s (%s)" % (s["code"], s["reason"])
                                    for s in data["skipped"]))
    if not (data["meta"].get("decision_rules") or []):
        warnings.append("The request selected no decision rule, so 3.2 DECISION "
                        "RULE ships with nothing ticked.")

    already = [e for e in entries if getattr(e, "report_file_path", None)]
    existing = latest_report(request_id, entries)

    return {
        "ok": True,
        "ready": not blockers,
        "blockers": blockers,
        "outstanding": left,
        "warnings": warnings,
        "tco_id": S._s(getattr(req, "tco_id", "")),
        "product": S._s(getattr(req, "product_name", "")),
        "filled": WF.filled_count(merged, row),
        "total": len(WF.FIELDS),
        "images": {k: os.path.basename(v)
                   for k, v in (d.get("images") or {}).items() if v},
        "tests": len(data["tests"]),
        "entries": len(entries),
        "with_data": len(with_data),
        # The endpoint is repeatable and has no status gate of its own, and
        # request 15 already holds three reports from repeat calls. Say so rather
        # than letting Generate look like a first run.
        "already_generated": bool(already),
        "existing_report": os.path.basename(existing) if existing else None,
        "pdf_backend": R.backend(),
        "pdf_available": R.available(),
        "pdf_note": None if R.available() else R.unavailable_note(),
        "page_reached": d.get("page", 1),
    }
