# -*- coding: utf-8 -*-
"""Deterministic tests for the planning / scope layer. No API key, no database.

WHY A SECOND TEST FILE
----------------------
nlp_search/evals.py is an integration harness: it calls the real model against
the real database and takes forty minutes. That is the right way to measure
whether ANSWERS are correct, and the wrong way to find out that a regex stopped
matching.

Everything here is pure: given a question, does the scope come out REAL; given
SQL, does the guard reject it; given a plan, does the guard warn. These are the
parts that must not silently stop working, and they can all be checked in under
a second without spending a token.

    python test_nlp_planning.py          # standalone
    pytest test_nlp_planning.py          # or under pytest
"""
import sys

from nlp_search import plan_guard, query_planner, scope, sql_guard, table_retriever
from nlp_search import verify
from nlp_search.schema_catalog import ALLOWED_TABLES

_FAILURES = []


def check(label, got, want):
    if got != want:
        _FAILURES.append("%s: got %r, want %r" % (label, got, want))


def check_true(label, got):
    if not got:
        _FAILURES.append("%s: expected truthy, got %r" % (label, got))


def check_false(label, got):
    if got:
        _FAILURES.append("%s: expected falsy, got %r" % (label, got))


# --------------------------------------------------------------------------
# scope detection
# --------------------------------------------------------------------------

def test_scope_detection():
    cases = [
        ("How many EMC test requests are in the system?", scope.REAL),
        ("How many tests are assigned?", scope.REAL),
        ("Which products failed?", scope.REAL),
        # explicit demo
        ("How many demo requests are there?", scope.DEMO),
        ("Show me the synthetic data", scope.DEMO),
        ("What is DEMO-EMC-311 about?", scope.DEMO),
        ("How many tests in the test dataset?", scope.DEMO),
        # both
        ("How many requests including demo data?", scope.ALL),
        # "real" pins REAL even when demo is also named
        ("How many real jobs, not the demo ones?", scope.REAL),
        ("Exclude the demo rows - how many requests?", scope.REAL),
        # "a demo of X" is not a request for demo DATA
        ("Give me a demo of the report generator", scope.REAL),
    ]
    for question, want in cases:
        check("scope(%r)" % question[:42], scope.detect(question), want)


def test_scope_predicate():
    check("REAL predicate", scope.sql_predicate(scope.REAL, "r"), "r.is_synthetic = 0")
    check("DEMO predicate", scope.sql_predicate(scope.DEMO, "r"), "r.is_synthetic = 1")
    check("ALL predicate", scope.sql_predicate(scope.ALL, "r"), "")
    check_true("planner_entries is scoped",
               "planner_entries" in scope.SCOPED_TABLES)
    check_true("datasheet is scoped", "datasheet" in scope.SCOPED_TABLES)
    # Shared lab infrastructure has no request of its own; requiring a join
    # from it would require a join that does not exist.
    for t in ("equipment", "maintenance", "users", "equipment_history"):
        check_false("%s must NOT be scoped" % t, t in scope.SCOPED_TABLES)


# --------------------------------------------------------------------------
# the SQL guard's scope enforcement
# --------------------------------------------------------------------------

def _ok(sql, sc):
    ok, _why, _clean = sql_guard.validate_sql(
        sql, ALLOWED_TABLES, denied_star_tables=("users",), scope=sc)
    return bool(ok)


def test_guard_scope():
    cases = [
        # (sql, scope, allowed?)
        ("SELECT COUNT(*) FROM iec_emc_requests", "REAL", False),
        ("SELECT COUNT(*) FROM iec_emc_requests WHERE is_synthetic = 0", "REAL", True),
        ("SELECT COUNT(*) FROM iec_emc_requests r WHERE r.is_synthetic = 0", "REAL", True),
        ("SELECT COUNT(*) FROM planner_entries", "REAL", False),
        ("SELECT COUNT(*) FROM planner_entries p JOIN iec_emc_requests r "
         "ON r.id = p.test_request_id WHERE r.is_synthetic = 0", "REAL", True),
        ("SELECT COUNT(*) FROM datasheet", "REAL", False),
        ("SELECT COUNT(*) FROM datasheet_status_history", "REAL", False),
        # lab-wide tables need no filter
        ("SELECT COUNT(*) FROM equipment WHERE calibration_due_date < CURDATE()",
         "REAL", True),
        ("SELECT COUNT(*) FROM maintenance", "REAL", True),
        ("SELECT username FROM users WHERE role = 'lab_engineer'", "REAL", True),
        # ALL and unscoped impose nothing
        ("SELECT COUNT(*) FROM planner_entries", "ALL", True),
        ("SELECT COUNT(*) FROM iec_emc_requests", None, True),
        # DEMO still requires the flag - it is a filter, not an absence of one
        ("SELECT COUNT(*) FROM iec_emc_requests", "DEMO", False),
        ("SELECT COUNT(*) FROM iec_emc_requests WHERE is_synthetic = 1", "DEMO", True),
        # a CTE carrying the filter satisfies it
        ("WITH real_r AS (SELECT id FROM iec_emc_requests WHERE is_synthetic = 0) "
         "SELECT COUNT(*) FROM planner_entries p JOIN real_r ON real_r.id = "
         "p.test_request_id", "REAL", True),
        # THE workaround that must not work: two synthetic jobs carry a
        # real-looking IEC-EMC- id, so the prefix proves nothing.
        ("SELECT COUNT(*) FROM iec_emc_requests WHERE tco_id NOT LIKE 'DEMO%'",
         "REAL", False),
    ]
    for sql, sc, want in cases:
        check("guard[%s] %s" % (sc, sql[:52]), _ok(sql, sc), want)


def test_every_domain_can_reach_the_scope_flag():
    """A worker that owns a scoped table must be able to join to the flag.

    This invariant is not obvious and breaking it is silent. is_synthetic lives
    only on iec_emc_requests, and the guard rejects any query touching a scoped
    table without it - so a worker holding, say, `datasheet` but NOT
    iec_emc_requests can write no valid query at all. That happened: the
    datasheets worker had iec_emc_requests deliberately excluded to save prompt
    tokens, and after the scope policy went in it answered "that table isn't
    accessible in this environment" instead of the coupling method.

    Anyone re-slicing the domains in build_catalog.py will hit this test rather
    than that answer.
    """
    from nlp_search import schema_catalog as cat
    for domain in cat.DOMAIN_TABLES:
        allowed = set(cat.tables_for(domain))
        if allowed & scope.SCOPED_TABLES:
            check_true("%s can reach %s" % (domain, scope.FLAG_TABLE),
                       scope.FLAG_TABLE in allowed)


def test_guard_still_blocks_the_old_things():
    """The scope check is additive - none of the original controls may lapse."""
    blocked = [
        "DROP TABLE datasheet",
        "SELECT 1; DELETE FROM datasheet",
        "INSERT INTO users (username) VALUES ('x')",
        "SELECT * FROM users",
        "SELECT SLEEP(5)",
        "SELECT * FROM information_schema.tables",
        "SELECT password FROM users",
        "SELECT COUNT(*) FROM some_table_that_does_not_exist",
        "SELECT COUNT(*) FROM equipment -- comment",
        "SELECT COUNT(*) FROM equipment FOR UPDATE",
    ]
    for sql in blocked:
        check("guard blocks %s" % sql[:46], _ok(sql, "ALL"), False)
    # and a legitimate read still passes
    check("guard allows a plain read",
          _ok("SELECT name FROM equipment LIMIT 5", "ALL"), True)


# --------------------------------------------------------------------------
# table retrieval
# --------------------------------------------------------------------------

def _names(question, domain=None):
    return [t["name"] for t in table_retriever.retrieve(question, domain=domain)]


def test_table_retrieval():
    got = _names("How many tests are assigned for Vantage Water Purifier?")
    check_true("assigned -> iec_emc_request_tests", "iec_emc_request_tests" in got)
    check_true("assigned -> planner_entries", "planner_entries" in got)
    check_true("retrieval stays small (<=6)", len(got) <= 6)

    got = _names("Which instruments are out of calibration?", domain="inventory")
    check("calibration ranks equipment first", got[0], "equipment")

    got = _names("What coupling method was used on the CE datasheets?",
                 domain="datasheets")
    check("CE coupling ranks datasheet_ce first", got[0], "datasheet_ce")

    # A named test code must favour ITS table over the ten sibling tables that
    # score identically on the word "datasheet".
    got = _names("Who reviewed the ESD datasheet?", domain="datasheets")
    check_true("ESD surfaces datasheet_esd", "datasheet_esd" in got)

    # Schema questions route to the catalog, and retrieval must not drag the
    # whole request subtree in on the word "requests".
    got = _names("How many EMC test requests are in the system?", domain="requests")
    check_true("requests ranks the master table", "iec_emc_requests" in got[:2])

    check("gibberish retrieves nothing", table_retriever.retrieve("zzz qqq"), [])


# --------------------------------------------------------------------------
# query planning
# --------------------------------------------------------------------------

def test_operation_and_subject():
    cases = [
        ("How many tests are assigned?", "COUNT", "TEST"),
        ("Which datasheets are in draft?", "LIST", "DATASHEET"),
        ("Where is the coupling method stored?", "DESCRIBE", "SCHEMA"),
        ("Why did the CE test fail?", "EXPLAIN", None),
        ("Break down the datasheets by test code", "AGGREGATE", "DATASHEET"),
        ("Compare IEC-EMC-004 and IEC-EMC-005", "COMPARE", None),
    ]
    for question, op, subject in cases:
        check("operation(%r)" % question[:38],
              query_planner.detect_operation(question), op)
        if subject:
            check("subject(%r)" % question[:38],
                  query_planner.detect_subject(question), subject)


def test_entity_candidates():
    got = dict((t, k) for k, t in
               query_planner.candidate_entities("How many tests are assigned "
                                                "for Smart2Pure?"))
    check_true("Smart2Pure proposed", "Smart2Pure" in got)
    got = [t for _k, t in query_planner.candidate_entities(
        "What happened on IEC-EMC-004?")]
    check_true("TCO proposed", "IEC-EMC-004" in got)
    # A sentence-initial question word is not a product name.
    got = [t for _k, t in query_planner.candidate_entities("How many tests?")]
    check("no phantom entity", got, [])
    # Two-word proper nouns are offered as BOTH person and product - guessing
    # person only lost the scope signal on "Lifecycle Probe".
    kinds = [k for k, t in query_planner.candidate_entities(
        "How many tests for Lifecycle Probe?") if t == "Lifecycle Probe"]
    check_true("two-word name tried as a product", "product" in kinds)


def test_plan_without_a_model():
    """plan() must work with the model fallback disabled."""
    import os
    os.environ["NLP_NO_PLAN_MODEL"] = "1"
    try:
        plan = query_planner.plan("How many tests are assigned?", domain="requests")
        check("plan operation", plan["operation"], "COUNT")
        check("plan subject", plan["subject"], "TEST")
        check("plan scope", plan["scope"], "REAL")
        check_true("plan named tables", bool(plan["source_tables"]))
        check("empty question plans to None", query_planner.plan(""), None)

        # Regression: a DATA question must never come out as DESCRIBE, even
        # when the model fallback says so. "What coupling method was used on
        # the CE datasheets?" asks for a VALUE; classified as DESCRIBE, the
        # plan told the worker to answer from the catalog and not query, and
        # the answer became "I did not run any query for that".
        plan = query_planner.plan(
            "What coupling method was used on the CE datasheets?",
            kind="data", domain="datasheets")
        check_false("a data question is never DESCRIBE",
                    plan["operation"] == "DESCRIBE")
        check_false("a data question is never subject SCHEMA",
                    plan["subject"] == "SCHEMA")

        # ...and a genuine schema question still is.
        plan = query_planner.plan("Where is the coupling method stored?",
                                  kind="schema")
        check("schema question is DESCRIBE", plan["operation"], "DESCRIBE")
        check("schema question is subject SCHEMA", plan["subject"], "SCHEMA")
    finally:
        os.environ.pop("NLP_NO_PLAN_MODEL", None)


# --------------------------------------------------------------------------
# plan guard
# --------------------------------------------------------------------------

def test_plan_guard():
    check("None plan is OK", plan_guard.validate(None)["verdict"], plan_guard.OK)

    good = {"operation": "COUNT", "subject": "REQUEST", "scope": "REAL",
            "source_tables": ["iec_emc_requests"], "candidate_metrics": [],
            "entity": None}
    check("complete plan passes", plan_guard.validate(good)["verdict"], plan_guard.OK)

    # An ambiguous term warns but does not block - the worker can still answer
    # by naming which reading it used.
    ambiguous = dict(good, state="ASSIGNED",
                     candidate_metrics=["test_assigned_on_request",
                                        "test_assigned_in_schedule"])
    v = plan_guard.validate(ambiguous)
    check("ambiguous term warns", v["verdict"], plan_guard.WARN)
    check_true("ambiguous term is answerable", v["ok"])
    check_true("ambiguous term offers a clarification", bool(v["clarify"]))

    # An unresolved entity blocks: filtering on it would report zero, and a
    # zero from an unmatched filter means something different from "none".
    missing = dict(good, entity={"value": "Hyperion Mass Analyser",
                                 "resolved": False, "type": "PRODUCT"})
    v = plan_guard.validate(missing)
    check("unresolved entity blocks", v["verdict"], plan_guard.BLOCK)
    check_false("blocked plan is not ok", v["ok"])

    # A name that exists only in the excluded corpus blocks too, with its own
    # finding - it is not absent, it is out of scope.
    excluded = dict(good, entity={"value": "Lifecycle Probe", "resolved": False,
                                  "type": "PRODUCT", "excluded_by_scope": True})
    v = plan_guard.validate(excluded)
    check("scope-excluded entity blocks", v["verdict"], plan_guard.BLOCK)
    check_true("scope exclusion is explained",
               any("only in the corpus" in e for e in v["errors"]))

    # Missing scope is a real error - it is the one field with no safe default
    # at this point, because the default was supposed to be applied upstream.
    v = plan_guard.validate(dict(good, scope=None))
    check("missing scope blocks", v["verdict"], plan_guard.BLOCK)


# --------------------------------------------------------------------------
# the semantic verification helpers
# --------------------------------------------------------------------------

class _FakeLedger(object):
    """Just enough Ledger for the pure checks."""

    def __init__(self, plan=None, entries=()):
        self.plan = plan
        self.entries = list(entries)

    def tables_queried(self):
        import re
        names = set()
        for e in self.entries:
            if e.get("error"):
                continue
            for m in re.finditer(r"\b(?:FROM|JOIN)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
                                 e.get("sql") or "", re.I):
                names.add(m.group(1).lower())
        return names

    def sql_text(self):
        return "\n".join((e.get("sql") or "") for e in self.entries
                         if not e.get("error")).lower()


def test_scope_excluded_entity_prompt():
    """A demo-only entity must be told apart from one that does not exist.

    Regression for the worst answer this system produced during development.
    Asked "how many test assigned for product vantage water purifier" - a
    product that exists ONLY as DEMO-EMC-311/316 - it printed:

        In-scope tests assigned on request: 0. Scheduled: 0. Jobs: 0.

    above three LAB-WIDE queries that had returned 40, 16 and 6, under a badge
    reading "Verified against the data". Four separate defects lined up: the
    lower-case name was never extracted, so no entity was found; with no
    entity the lab-wide measures ran; the guard's "do not call lab_metric"
    line was gated on `resolved`, which is False here; and the answer's zeros
    were invisible to the checker because 0 is filtered out as prose.
    """
    excluded = {"operation": "COUNT", "subject": "TEST", "scope": "REAL",
                "candidate_metrics": ["test_assigned_on_request"],
                "source_tables": ["iec_emc_request_tests"],
                "entity": {"type": "PRODUCT", "value": "vantage water purifier",
                           "resolved": False, "excluded_by_scope": True,
                           "identifiers": []}}
    block = query_planner.prompt_block(excluded)
    check_true("excluded entity blocks lab_metric",
               "DO NOT call lab_metric" in block)
    check_true("excluded entity is named as such",
               "EXCLUDED CORPUS" in block)
    check_true("excluded entity forbids a zero answer",
               "'0'" in block or "none" in block.lower())
    check_false("excluded entity is not called simply unresolved",
                "NOT RESOLVED" in block)

    # A genuine miss keeps the old wording - it needs the opposite answer.
    missing = dict(excluded, entity={"type": "PRODUCT", "value": "Hyperion",
                                     "resolved": False, "identifiers": []})
    block = query_planner.prompt_block(missing)
    check_true("an unresolved entity is still marked unresolved",
               "NOT RESOLVED" in block)
    check_false("an unresolved entity is not called scope-excluded",
                "EXCLUDED CORPUS" in block)


def test_lowercase_entities_are_found():
    """People type in lower case, and capitalisation was the only signal."""
    got = query_planner.candidate_entities(
        "how many test assigned for product vantage water purifier")
    texts = [t.lower() for _k, t in got]
    check_true("lower-case product name extracted",
               any("vantage water purifier" in t for t in texts))

    got = query_planner.candidate_entities("what tests are assigned to krishna")
    check_true("lower-case person extracted",
               any(k == "person" and t.lower() == "krishna" for k, t in got))

    got = query_planner.candidate_entities("how many tests are assigned for smart2pure")
    kinds = [k for k, t in got if t.lower() == "smart2pure"]
    check_true("a bare 'for X' is tried as a product", "product" in kinds)
    check_true("a bare 'for X' is tried as a person", "person" in kinds)

    # ...without inventing entities out of ordinary phrasing.
    for q in ("how many tests are assigned for each engineer",
              "how many tests are assigned?",
              "how many datasheets are in draft?"):
        check("no phantom entity in %r" % q[:34],
              query_planner.candidate_entities(q), [])


def test_plan_mismatch():
    plan = {"operation": "COUNT", "subject": "TEST", "scope": "REAL",
            "source_tables": ["iec_emc_request_tests", "iec_emc_requests"],
            "candidate_metrics": ["test_assigned_on_request"],
            "entity": {"value": "Smart2Pure", "resolved": True,
                       "identifiers": ["IEC-EMC-004"]}}

    # A COUNT with no query at all: the observed failure was answering "4
    # tests" off the entity lookup's four candidate JOBS.
    check_true("count with no SQL is flagged",
               bool(verify._plan_mismatch(_FakeLedger(plan))))

    # Right tables, right column, right entity - nothing to say.
    good = _FakeLedger(plan, [{"sql": "SELECT COUNT(*) FROM iec_emc_request_tests t "
                                      "JOIN iec_emc_requests r ON r.id = t.request_id "
                                      "WHERE t.assigned_engineer_id IS NOT NULL "
                                      "AND r.tco_id = 'IEC-EMC-004'",
                               "worker": "requests"}])
    check("a conforming query is not flagged", verify._plan_mismatch(good), None)

    # Counting TESTS off the request table: 4 jobs reported as 4 tests. Real
    # number, wrong grain, and value-matching cannot see it.
    grain = _FakeLedger(plan, [{"sql": "SELECT COUNT(*) FROM iec_emc_requests r "
                                       "WHERE r.tco_id = 'IEC-EMC-004'",
                                "worker": "requests"}])
    check_true("wrong grain is flagged", bool(verify._plan_mismatch(grain)))

    # A lab-wide answer to an entity question.
    labwide = _FakeLedger(plan, [{"sql": "SELECT COUNT(*) FROM iec_emc_request_tests t "
                                         "WHERE t.assigned_engineer_id IS NOT NULL",
                                  "worker": "requests"}])
    check_true("entity ignored is flagged", bool(verify._plan_mismatch(labwide)))


def test_scope_breach():
    plan = {"scope": "REAL"}
    clean = _FakeLedger(plan, [{"sql": "SELECT 1", "columns": ["tco_id", "is_synthetic"],
                                "rows": [["IEC-EMC-004", 0]]}])
    check("clean rows pass", verify._scope_breach(clean), None)

    dirty = _FakeLedger(plan, [{"sql": "SELECT 1", "columns": ["tco_id", "is_synthetic"],
                                "rows": [["IEC-EMC-004", 0], ["DEMO-EMC-301", 1]]}])
    check_true("synthetic rows are flagged", bool(verify._scope_breach(dirty)))

    # DEMO scope is allowed to return synthetic rows - that is what was asked.
    check("demo scope permits synthetic rows",
          verify._scope_breach(_FakeLedger({"scope": "DEMO"}, dirty.entries)), None)


def test_scope_prose_stripping():
    """The filter must not be narrated at the user, and stripping it must not
    eat the answer - an earlier version returned an empty string."""
    cases = [
        ("10 EMC test requests in the system. Filtering on r.is_synthetic = 0 "
         "to exclude synthetic data.", "10 EMC test requests in the system."),
        ("There are 10 requests. (Note: filtered on is_synthetic = 0 to "
         "exclude demo rows.)", "There are 10 requests."),
        ("Filtering on r.is_synthetic = 0, there are 10 requests.",
         "there are 10 requests."),
        # must survive untouched
        ("The coupling method is stored in datasheet_ce.coupling_method.",
         "The coupling method is stored in datasheet_ce.coupling_method."),
        ("There are 27 active products.", "There are 27 active products."),
        ("There are 10 real requests and 12 demo requests.",
         "There are 10 real requests and 12 demo requests."),
    ]
    for raw, want in cases:
        check("strip(%s)" % raw[:40], verify.strip_machinery(raw), want)


def test_final_answer_guard():
    """The language stage may not introduce a figure."""
    from nlp_search import final_answer
    ok, _ = final_answer._guard("There are 10 requests.", "There are 10 EMC test requests.")
    check_true("faithful rewrite allowed", ok)
    ok, why = final_answer._guard("There are 10 requests.",
                                  "There are 10 requests and 12 datasheets.")
    check_false("invented figure rejected", ok)
    check_true("rejection names the figure", "12" in (why or ""))
    ok, _ = final_answer._guard("There are 10 requests.", "")
    check_false("empty rewrite rejected", ok)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report, do not abort the run
            _FAILURES.append("%s raised %s: %s" % (fn.__name__, type(exc).__name__, exc))
    if _FAILURES:
        print("FAILED (%d)" % len(_FAILURES))
        for f in _FAILURES:
            print("  -", f)
        return 1
    print("OK - %d test groups passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
