# -*- coding: utf-8 -*-
"""Regenerate schema_catalog.py from the live MySQL database.

Run whenever the schema changes:

    python -m nlp_search.build_catalog

It introspects the configured database and writes nlp_search/schema_catalog.py:
the table allowlist, the per-column list used for validation, and a compact
catalog text for the agent prompts.

Three things this does that a naive dump would not:

* **Domain slices.** Each worker agent gets only the tables it owns, so its
  prompt stays small (better SQL) and its allowlist is narrow (smaller blast
  radius). ``DOMAINS`` below is the assignment; ``catalog_prompt_text(domain)``
  renders one slice.

* **Empty tables are kept, not dropped** - but only the ones that are part of
  the schema by design (the datasheet family). "This table exists and has no
  rows" is a true answer; "I have never heard of that table" invites the model
  to invent something else. Genuinely dead tables are excluded outright.

* **Big columns are hidden.** form_json, block_diagram, signatures and the
  like are megabyte blobs that blow the tool's size budget and tell the model
  nothing. They are left out of the catalog so it does not reach for them, and
  a note names the queryable alternative where one exists.
"""
import io
import os
import re
import sys

# One-line purposes, hand-authored. These drive the model's routing more than
# the column lists do.
PURPOSES = {
    # -- people ------------------------------------------------------------
    "users": "application users (requesters, lab engineers, admins); role decides permissions",
    # -- inventory ---------------------------------------------------------
    "equipment": "lab test-equipment inventory (make, model, serial, calibration + maintenance due dates)",
    "maintenance": "maintenance records for lab equipment",
    "equipment_history": "audit trail of changes to equipment records (who changed what, when)",
    # -- the request / job domain -----------------------------------------
    "iec_emc_requests": "MASTER EMC test request, one row per TCO/job: product, requester, status, assignment, key dates",
    "iec_emc_request_tests": "one row per EMC test per request (test_code CE/RE/EFT/ESD/SURGE...); is_selected=1 = in scope; per-test workflow status + engineer",
    "iec_emc_request_service_types": "service types requested (per request)",
    "iec_emc_request_serial_numbers": "EUT serial numbers (per request)",
    "iec_emc_request_categories": "product categories (per request)",
    "iec_emc_request_accessories": "EUT accessories (per request)",
    "iec_emc_request_cables": "EUT cables (per request)",
    "iec_emc_request_eut_specs": "EUT electrical specs: voltage/frequency/phase/power (per request)",
    "iec_emc_request_supply_vf": "supply voltage/frequency combinations to test (per request)",
    "iec_emc_request_product_standards": "product standards declared on the request (e.g. EN 61326-1)",
    "iec_emc_request_product_environments": "intended product environments (per request)",
    "iec_emc_request_decision_rules": "conformity decision rules chosen (per request)",
    "iec_emc_request_functional_modes": "EUT functional/operating modes (feeds the datasheet Test Mode)",
    "iec_emc_request_test_standards": "basic test standards per test per request",
    "iec_emc_request_test_ce": "Conducted Emission parameters REQUESTED (what to test), not results",
    "iec_emc_request_test_re": "Radiated Emission parameters REQUESTED, not results",
    "iec_emc_request_test_esd": "ESD parameters REQUESTED, not results",
    "iec_emc_request_test_harmonic": "Harmonic-current parameters REQUESTED, not results",
    "iec_emc_request_test_flicker": "Voltage flicker parameters REQUESTED, not results",
    "iec_emc_request_test_rs": "Radiated Susceptibility parameters REQUESTED, not results",
    "iec_emc_request_test_eft": "EFT/Burst parameters REQUESTED, not results",
    "iec_emc_request_test_surge": "Surge parameters REQUESTED, not results",
    "iec_emc_request_test_crf": "Conducted RF immunity parameters REQUESTED, not results",
    "iec_emc_request_test_power_freq": "Power-frequency magnetic field parameters REQUESTED, not results",
    "iec_emc_request_test_voltage_dips": "Voltage dips/interruptions parameters REQUESTED, not results",
    # -- scheduling --------------------------------------------------------
    "planner_entries": "the lab SCHEDULE: one row per scheduled test - engineer, dates, workflow status, peer reviewer, generated .docx path",
    # -- datasheets: what was actually measured ----------------------------
    "datasheet": "HEADER of every filled datasheet, one row per scheduled test: test code, status, result, conditions, who tested it. START HERE for datasheet questions.",
    "datasheet_ce": "Conducted Emission datasheet: the values RECORDED for that test",
    "datasheet_re": "Radiated Emission datasheet: the values RECORDED",
    "datasheet_esd": "ESD datasheet: the values RECORDED",
    "datasheet_eft": "EFT/Burst datasheet: the values RECORDED",
    "datasheet_surge": "Surge datasheet: the values RECORDED",
    "datasheet_crf": "Conducted RF immunity datasheet: the values RECORDED",
    "datasheet_rs_ri": "Radiated Susceptibility datasheet: the values RECORDED",
    "datasheet_pfmf": "Power-frequency magnetic field datasheet: the values RECORDED",
    "datasheet_harmonic": "Harmonic current datasheet: the values RECORDED",
    "datasheet_voltageflicker": "Voltage flicker datasheet: the values RECORDED",
    "datasheet_voltagedips": "Voltage dips/interruptions datasheet: the values RECORDED",
    "datasheet_observation": "EVERY observation cell from every test, flattened: one row per measured cell (grid, row label, column label, value). This is how to answer 'what was observed at X'.",
    "datasheet_observation_legend": "what each observation code (A, B, C1...) means on a given datasheet",
    "datasheet_measurement": "EVERY measured NUMBER from every test, flattened: one row per cell, with value as text and value_num as a number you can compare and sort. This is where CE Line/Neutral readings, RE tables, harmonic currents and the flicker grids live. revision_no says which submitted version a reading belongs to - filter it, or you will count a rejected version alongside the current one.",
    "datasheet_equipment": "which equipment was USED on each datasheet (as typed on the form)",
    "datasheet_software": "which software was USED on each datasheet",
    "datasheet_modification": "EUT modifications recorded on a datasheet",
    "datasheet_status_history": "audit trail of datasheet review decisions: who approved/rejected, when, and why",
    "datasheet_revision": "frozen snapshot of a datasheet as submitted for each review round",
    "datasheet_records": "the RAW saved form behind each datasheet (draft or submitted). Prefer the `datasheet` tables above - this one stores the form as JSON.",
    "datasheet_fixed_values": "admin-editable fixed values (uncertainty, SOP refs, limits) per datasheet type",
    "basic_standard_map": "admin mapping: product standard -> basic standard used by datasheets",
}

# Dead or trap tables: never offered to the model, whatever they contain.
EXCLUDE = {
    # The abandoned upload-based pipeline. Last written 2026-04-02 and replaced
    # by iec_emc_requests. `test_datasheets` is the dangerous one: the name is
    # exactly what a model reaches for when asked about datasheets, and it
    # would answer from years-old rows that no longer reflect the lab.
    "test_requests", "test_plans", "test_datasheets",
    "iec_emc_test_requests",            # legacy orphan; collides with iec_emc_request_tests
    "equipment_audit_log",              # superseded by equipment_history
    "iec_emc_request_test_rs_interim",  # transient scratch data
    "nlp_search_audit",                 # this feature's own log; not lab data
}

# Prefixes dropped wholesale. The datasheet_rev_* mirrors are column-for-column
# copies of sixteen live tables, one row per frozen revision. Letting the
# `datasheet_*` wildcard pick them up took the datasheets prompt from 8.8k to
# 34.5k characters - a quadrupling paid on every question, to describe tables
# that answer one question ("what did it say before it was rejected"), and to
# describe them in words nearly identical to the live tables they mirror, which
# is exactly how a model ends up querying the wrong one.
#
# They are still there for a DBA and still named predictably, and the glossary
# points at them. What they are not is 25k characters of every prompt.
EXCLUDE_PREFIXES = ("datasheet_rev_",)

# Kept even at zero rows. For these, "the table is there and it is empty" is a
# real answer - EFT simply has not been run yet, no datasheet has been rejected
# yet. Dropping them would leave the model with no way to say that.
KEEP_EMPTY_PREFIXES = ("datasheet",)

# The tables a domain touches on almost every question. These keep their full
# column list in the prompt; everything else is one index line plus a
# describe_table() call when a question actually needs it. See
# index_prompt_text() in the generated module for why it is a split rather
# than an index for everything.
CORE_TABLES = {
    "requests": ("iec_emc_requests", "iec_emc_request_tests", "datasheet", "users"),
    "schedule": ("planner_entries", "iec_emc_requests", "datasheet", "users"),
    "datasheets": ("datasheet", "datasheet_equipment", "datasheet_observation",
                   "datasheet_measurement",
                   "datasheet_software", "planner_entries", "users"),
    "inventory": ("equipment", "datasheet_equipment", "maintenance",
                  "equipment_history", "users"),
}

# Tables whose SELECT * is refused because they carry credential columns.
DENIED_STAR = ("users",)

# Which worker owns what. A table may appear in more than one slice when both
# workers genuinely need it to join (users is needed everywhere; planner_entries
# is the spine between the schedule and the datasheets).
DOMAINS = {
    "requests": {
        "title": "EMC test requests / jobs",
        "blurb": ("Everything a customer ASKED FOR: the request, the product under "
                  "test, which tests are in scope, declared standards, requester and "
                  "approval state. This is the intent, not the measured outcome."),
        # `datasheet` (the parent row only, not its 20 per-test children) is
        # here so ONE worker can answer "what was asked for vs what actually
        # came back". That join - iec_emc_request_tests LEFT JOIN datasheet -
        # is the single most valuable question in the schema and no worker
        # could write it before: requests had the asks, datasheets had the
        # results, and the orchestrator can only staple two answers together
        # in prose, which is where the multi-table questions failed. Costs
        # 1.8k chars of catalog.
        "tables": ["iec_emc_requests", "iec_emc_request_*", "users",
                   "planner_entries", "datasheet"],
    },
    "schedule": {
        "title": "schedule, people and peer review",
        "blurb": ("Who is doing what and when: scheduled tests, assigned engineers, "
                  "workflow status, peer-review assignment and approval state."),
        # `datasheet` for the same reason the requests domain has it: "which
        # scheduled tests have no datasheet, and who is on them" is a plain
        # schedule question and this worker could not answer it. Asked exactly
        # that, its correct query was rejected for touching `datasheet`, so it
        # substituted "engineers with an active planner entry" and presented
        # the result as though it had answered. That named a fourth engineer
        # who has no unfilled test at all.
        "tables": ["planner_entries", "users", "iec_emc_requests", "datasheet"],
    },
    "datasheets": {
        "title": "filled datasheets and measured results",
        "blurb": ("What was actually MEASURED and recorded: results, ambient "
                  "conditions, per-test parameters, every observation cell, the "
                  "equipment and software used, and the review history."),
        # deliberately NOT iec_emc_requests: `datasheet` already denormalises
        # tco_id, job_number, product_name and eut_class, and that table is the
        # single biggest block of catalog text. A question that genuinely needs
        # request detail is a cross-domain question - the orchestrator's job.
        "tables": ["datasheet", "datasheet_*", "basic_standard_map",
                   "planner_entries", "users"],
    },
    "inventory": {
        "title": "equipment and calibration",
        "blurb": ("The lab's instruments: what exists, its calibration and "
                  "maintenance due dates, and the change history."),
        # datasheet_equipment (267 chars) closes the other missing bridge:
        # "was any instrument used on a test out of calibration" needs the
        # used-on-a-datasheet list AND the calibration dates, and they sat in
        # different workers. Note it joins by NAME, not id, and the name is
        # not unique - see RELATIONSHIPS in semantics.py.
        "tables": ["equipment", "equipment_history", "maintenance", "users",
                   "datasheet_equipment"],
    },
}

# Tables where a MISSING CHILD ROW MEANS "NOBODY RECORDED IT" - a gap - as
# opposed to "this does not apply here".
#
# The distinction cannot be read off the schema, which is why this list is
# written by hand. datasheet_ce has rows for 2 of 12 datasheets and that is
# not a gap: the other 10 are not CE tests. datasheet_equipment has rows for
# 8 of 12 and that IS a gap: every test uses equipment, so four datasheets
# simply had none entered.
#
# It matters because of a real answer. Asked "which instruments do we use
# most across our tests", the assistant ran a correct GROUP BY over
# datasheet_equipment and presented a lab-wide ranking. Every row it counted
# came from ONE job. The SQL was right and the answer was still misleading,
# which is the dangerous shape: nothing about it looks wrong. The model
# cannot see this for itself - absent rows leave no trace in a result set.
#
# The numbers are measured against live data at build time, so they cannot
# go stale the way a hand-written note would. Only the JUDGEMENT is manual.
COVERAGE_EXPECTED = {
    "datasheet_equipment": "datasheet_id",
    "datasheet_software": "datasheet_id",
}

# Below this fraction of the parent, say so in the catalog.
COVERAGE_THRESHOLD = 0.95

_ENUMISH = re.compile(
    r"status|type|code|role|mode|class|result|state|level|category|active|"
    r"verdict|decision|^value$|grid_key|col_key|priority", re.I)

# Credential-ish columns: omitted from the catalog entirely. sql_guard blocks
# them at validation time too - this just keeps them out of the model's sight.
_HIDDEN_COLUMN = re.compile(
    r"password|\bpwd\b|secret|api_key|(?:reset|auth|session|csrf|access|refresh)_token", re.I)

# Large blob columns: real data, but megabytes of it and useless to reason
# over. Hidden so the model does not select them and blow the size budget.
_LARGE_COLUMN = re.compile(
    r"^(?:form_json|images_json|extracted_data|block_diagram|plan_update_history|"
    r"model_variance_document|values_json|old_values|new_values|changes|"
    r"generated_files|.*_signature|.*_rows_json|obs_.*_json|.*_measurements_json)$", re.I)

# Where a hidden column has a queryable replacement, say so - otherwise the
# model just sees a column missing and has no idea where the data went.
_RECORDS_NOTE = ("form_json / images_json hold the raw form and are not selectable "
                 "here. The same values are normalised into `datasheet` and its "
                 "per-test tables - use those.")

_OBS_NOTE = ("the *_json columns hold this test's grids with their own labels "
             "and block structure. You do NOT have to parse them: every cell is "
             "also a row in datasheet_measurement (numbers, with value_num for "
             "comparisons) or datasheet_observation (criterion letters). Prefer "
             "those - they are plain SQL. read_grid(datasheet_id) is still there "
             "when you need a grid laid out exactly as the form shows it.")

_GLOSSARY = """How lab vocabulary maps to this schema (read this before writing SQL):
- "job" / "TCO" / "project" / "request"  -> iec_emc_requests (tco_id, job_number)
- "EUT" / "product" / "unit under test"  -> iec_emc_requests.product_name / model_number
- "test" in the sense of WHAT WAS ASKED   -> iec_emc_request_tests (+ iec_emc_request_test_* detail)
- "test" in the sense of WHAT WAS RUN     -> planner_entries (schedule) and `datasheet` (result)
- "datasheet" / "the sheet" / "results"   -> `datasheet` + datasheet_<code> + datasheet_observation
- "observation" / "criterion A|B|C|D"     -> datasheet_observation.value
- "reading" / "measurement" / "limit"     -> datasheet_measurement.value_num (filter revision_no)
- "margin" / "how close to the limit"     -> datasheet_measurement, col_key like '%_margin'
- "previous version" / "before it was rejected" -> datasheet_revision + datasheet_rev_<code>
- "pass" / "fail" / "outcome"             -> datasheet.result  (per test)
- "conditions" / "ambient" / "humidity"   -> datasheet.ambient_temperature / relative_humidity
- "equipment used on a test"              -> datasheet_equipment  (NOT the `equipment` inventory)
- "equipment we own" / "calibration due"  -> equipment
- "engineer" / "tester" / "who ran it"    -> datasheet.engineer_name, planner_entries.engineer_user_id
- "reviewer" / "peer review"              -> planner_entries.peer_reviewer_user_id, datasheet.status='Peer Review'
- "approved" / "rejected" (a datasheet)   -> datasheet_status_history.to_status
- "rejected" (a REQUEST)                  -> iec_emc_requests.rejected_at / rejected_by / rejection_reason

TEST CODES ARE SPELLED DIFFERENTLY IN EACH TABLE. This is the single easiest
way to get a wrong answer here - a naive join on test_code silently drops four
of the eleven test types (28 rows) and nothing looks broken:

    concept          iec_emc_request_tests   planner_entries   datasheet
    Flicker          FLICKER                 VoltageFlicker    VOLTAGEFLICKER
    Power frequency  POWER_FREQ              PFMF              PFMF
    Voltage dips     VOLTAGE_DIPS            VoltageDips       VOLTAGEDIPS
    RF susceptibility RS                     RS_RI             RS_RI
    (CE, CRF, EFT, ESD, RE, SURGE, HARMONIC match, ignoring case)

RS_INTERIM exists on requests only and has no datasheet counterpart. When
joining requests to schedule or datasheets, normalise both sides - upper-case,
replace spaces with underscores, and map the four names above."""

_JOIN_HINTS = """Core relationships (use these joins):
- iec_emc_requests is the MASTER request, one row per TCO / job_number. Every
  iec_emc_request_* child joins via request_id -> iec_emc_requests.id.
- iec_emc_request_tests lists the tests per request; the per-test detail tables
  (iec_emc_request_test_ce etc.) join via request_test_id -> iec_emc_request_tests.id.
  is_selected=1 means the test is in scope.
- planner_entries is the SCHEDULE: joins to iec_emc_requests via tco_id (a
  string, not an id) or test_request_id, and to users via engineer_user_id /
  peer_reviewer_user_id / datasheet_uploaded_by. status lifecycle:
  scheduled -> in_progress -> 'Peer Review' -> datasheet_uploaded -> report_uploaded;
  cancelled is terminal.
- `datasheet` is one row per FILLED datasheet, joined to the schedule by
  planner_entry_id -> planner_entries.id. Its per-test detail lives in
  datasheet_<testcode> (datasheet_ce, datasheet_esd, ...) joined by
  datasheet_id -> datasheet.id, and so do datasheet_observation,
  datasheet_observation_legend, datasheet_equipment, datasheet_software,
  datasheet_modification, datasheet_status_history and datasheet_revision.
  `datasheet` already carries tco_id, job_number, product_name, engineer_name
  and result denormalised, so most questions need NO join at all.
- REQUESTED vs RECORDED is the distinction people get wrong: what a customer
  asked for is in iec_emc_request_test_*, what the lab measured is in
  datasheet_*. "What level was the ESD test run at" is the datasheet;
  "what level did they ask for" is the request."""


def _short_type(t):
    return re.sub(r"\((?:\d+|\d+,\d+)\)", "", t or "").strip()


def _domains_for(name):
    """Which domain slices a table belongs to."""
    out = []
    for dom, spec in DOMAINS.items():
        for pat in spec["tables"]:
            if (pat.endswith("*") and name.startswith(pat[:-1])) or pat == name:
                out.append(dom)
                break
    return out


def introspect(conn, database):
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    names = sorted(r[0] for r in cur.fetchall())
    tables = []
    for name in names:
        if name in EXCLUDE:
            continue
        cur.execute("SELECT COUNT(*) FROM `%s`" % name)
        rows = cur.fetchone()[0]
        if name.startswith(EXCLUDE_PREFIXES):
            continue
        if rows == 0 and not name.startswith(KEEP_EMPTY_PREFIXES):
            continue
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database, name))
        cols = [(c, _short_type(t), k) for c, t, k in cur.fetchall()]
        cur.execute(
            "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (database, name))
        fks = {c: (rt, rc) for c, rt, rc in cur.fetchall()}

        # How much of the PARENT does this child table actually cover?
        #
        # Asked "which instruments do we use most across our tests", the
        # assistant ran a perfectly correct GROUP BY over
        # datasheet_equipment and presented the result as a lab-wide
        # ranking. But equipment is recorded on only 8 of 12 datasheets and
        # every one of those 8 belongs to a single job, so the "ranking" was
        # one job's kit list. The SQL was right and the answer was still
        # misleading, which is the shape of error that matters here: nothing
        # about it looks wrong.
        #
        # No prompt wording fixes that, because the model cannot see what is
        # missing - absent rows leave no trace in a result set. So the gap is
        # measured here, against live data, and stated in the catalog. It
        # regenerates with the catalog, so it cannot go stale the way a
        # hand-written note would.
        coverage = {}
        want_col = COVERAGE_EXPECTED.get(name)
        if want_col and want_col in fks:
            ptable, _pcol = fks[want_col]
            try:
                cur.execute("SELECT COUNT(*) FROM `%s`" % ptable)
                total = cur.fetchone()[0]
                cur.execute("SELECT COUNT(DISTINCT `%s`) FROM `%s` WHERE `%s` IS NOT NULL"
                            % (want_col, name, want_col))
                have = cur.fetchone()[0]
                # Concentration matters as much as coverage: 8 of 12 sounds
                # tolerable until you see all 8 belong to the same job.
                jobs = None
                try:
                    cur.execute(
                        "SELECT COUNT(DISTINCT COALESCE(p.job_number, p.tco_id)) "
                        "FROM `%s` c JOIN `%s` p ON p.id = c.`%s`"
                        % (name, ptable, want_col))
                    jobs = cur.fetchone()[0]
                except Exception:  # noqa: BLE001 - parent may have no job column
                    pass
                if total and have < total * COVERAGE_THRESHOLD:
                    coverage[want_col] = (have, total, ptable, jobs)
            except Exception:  # noqa: BLE001 - a note is never worth failing over
                pass

        enums = {}
        for c, t, _k in cols:
            if not _ENUMISH.search(c) or _HIDDEN_COLUMN.search(c):
                continue
            if not (t.startswith("varchar") or t.startswith("enum") or t.startswith("char")):
                continue
            cur.execute("SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IS NOT NULL LIMIT 16"
                        % (c, name, c))
            vals = [str(r[0]) for r in cur.fetchall()]
            if 0 < len(vals) <= 15:
                enums[c] = sorted(vals)
        tables.append({"name": name, "rows": rows, "cols": cols,
                       "fks": fks, "enums": enums, "coverage": coverage,
                       "domains": _domains_for(name)})
    return tables


def visible_columns(t):
    """Column names the model is allowed to see (and therefore to select)."""
    return [c for c, _typ, _k in t["cols"]
            if not _HIDDEN_COLUMN.search(c) and not _LARGE_COLUMN.match(c)]


def render_table_text(t):
    head = "### %s (%s) - %s" % (
        t["name"],
        "%d rows" % t["rows"] if t["rows"] else "EMPTY - no rows yet",
        PURPOSES.get(t["name"], "supporting table"))
    lines = [head]
    parts = []
    for c, typ, key in t["cols"]:
        if _HIDDEN_COLUMN.search(c) or _LARGE_COLUMN.match(c):
            continue
        piece = "%s %s" % (c, typ)
        if key == "PRI":
            piece += " PK"
        if c in t["fks"]:
            piece += " ->%s.%s" % t["fks"][c]
        parts.append(piece)
    lines.append("  columns: " + "; ".join(parts))
    hidden = [c for c, _t, _k in t["cols"] if _LARGE_COLUMN.match(c)]
    if hidden:
        if t["name"] == "datasheet_records":
            lines.append("  note: %s" % _RECORDS_NOTE)
        elif any(h.endswith("_json") for h in hidden):
            lines.append("  note: %s" % _OBS_NOTE)
    for col, (have, total, ptable, jobs) in sorted(t.get("coverage", {}).items()):
        note = ("  PARTIAL COVERAGE: only %d of %d %s rows have any row here "
                "(%.0f%%)" % (have, total, ptable, 100.0 * have / total))
        if jobs is not None and jobs <= 2:
            note += (", and everything recorded here belongs to %d job%s"
                     % (jobs, "" if jobs == 1 else "s"))
        note += (". A total, ranking or \"most used\" answer from this table "
                 "describes THAT SUBSET only. SAY SO when you report one - "
                 "otherwise it reads as though it covered the whole lab.")
        lines.append(note)
    for c, vals in sorted(t["enums"].items()):
        lines.append("  %s values: %s" % (c, ", ".join("'%s'" % v for v in vals)))
    return "\n".join(lines)


def build_module_text(tables):
    allowed = tuple(t["name"] for t in tables)
    columns = {t["name"]: tuple(visible_columns(t)) for t in tables}
    texts = {t["name"]: render_table_text(t) for t in tables}
    domain_tables = {}
    for dom in DOMAINS:
        domain_tables[dom] = tuple(t["name"] for t in tables if dom in t["domains"])
    domain_meta = {d: {"title": s["title"], "blurb": s["blurb"]}
                   for d, s in DOMAINS.items()}
    return '''# -*- coding: utf-8 -*-
"""AUTO-GENERATED schema catalog for the NL->SQL agents.

Do not edit by hand - regenerate after schema changes with:

    python -m nlp_search.build_catalog

ALLOWED_TABLES drives the sql_guard allowlist and COLUMNS its column check.
DOMAIN_TABLES slices both per worker; catalog_prompt_text(domain) renders the
matching prompt section.
"""

ALLOWED_TABLES = %(allowed)r

# SELECT * is refused on these (they carry credential columns).
DENIED_STAR_TABLES = %(denied)r

# One line per table, for answering "where is X kept?" without reading a row.
TABLE_PURPOSE = %(purposes)r

# Low-cardinality values per table.column, so "which statuses exist" is a
# lookup rather than a query.
ENUM_VALUES = %(enums)r

# The measurement / observation grids, per table. These columns are hidden from
# COLUMNS on purpose - a model cannot write useful SQL against JSON and
# selecting them blows the size budget - but the data is the substance of a
# datasheet, so probes.read_grid() reaches them through this map instead.
GRID_COLUMNS = %(grids)r

# Every column the model is permitted to see, per table. Anything not listed
# is either a credential or a blob deliberately kept out of reach.
COLUMNS = %(columns)r

# Which tables each worker agent may touch.
DOMAIN_TABLES = %(domain_tables)r

DOMAIN_META = %(domain_meta)r

_GLOSSARY = %(glossary)r

_JOIN_HINTS = %(joins)r

CORE_TABLES = %(core)r

_TABLE_TEXT = %(texts)r


def catalog_prompt_text(domain=None):
    """The catalog section for one worker, or the whole schema when domain is None."""
    names = DOMAIN_TABLES.get(domain) if domain else tuple(_TABLE_TEXT)
    if not names:
        names = tuple(_TABLE_TEXT)
    body = "\\n\\n".join(_TABLE_TEXT[n] for n in names if n in _TABLE_TEXT)
    return _GLOSSARY + "\\n\\n" + _JOIN_HINTS + "\\n\\n" + body


def index_prompt_text(domain=None):
    """The prompt catalog: hub tables in full, the long tail as one line each.

    The catalog was 63-79%% of a worker's prompt and the prompt is resent on
    every turn, which made it the largest single line in the bill. But a PURE
    index - no columns for anything - was measured and is worse. It cut a
    simple question from 21k tokens to 7.2k, and then the inventory worker
    spent its turn describing tables and never issued a query at all, while a
    two-part question answered 15 where the truth is 68. Dropping the columns
    forces an extra turn and gpt-4o-mini does not reliably finish the chain.

    So the split follows usage. The hub tables cost nothing extra because they
    were going to be read anyway; the long tail - eleven per-test datasheet
    tables, thirteen per-test request tables - is fetched by describe_table()
    only from the questions that actually name that test.

    _JOIN_HINTS is deliberately absent: it is hand-written prose saying roughly
    what semantics.RELATIONSHIPS says, except RELATIONSHIPS is re-executed
    against the live database by validate() so it cannot quietly go wrong, and
    it is injected separately. Carrying both spent ~390 tokens a turn stating
    the same joins twice, one copy unverified.
    """
    names = DOMAIN_TABLES.get(domain) if domain else tuple(_TABLE_TEXT)
    if not names:
        names = tuple(_TABLE_TEXT)
    core = [n for n in names if n in CORE_TABLES.get(domain, ())]
    rest = [n for n in names if n not in core]
    out = _GLOSSARY
    if core:
        out += ("\\n\\n## Tables you use constantly - columns given\\n\\n"
                + "\\n\\n".join(_TABLE_TEXT[n] for n in core if n in _TABLE_TEXT))
    if rest:
        lines = []
        for n in rest:
            head = _TABLE_TEXT.get(n, "").split("\\n", 1)[0]
            lines.append(head[4:] if head.startswith("### ") else n)
        out += ("\\n\\n## Also yours - call describe_table(name) for its columns\\n"
                + "\\n".join("  " + ln for ln in lines))
    return out


def table_detail(name):
    """Full catalog entry for one table: columns, notes, enum values."""
    return _TABLE_TEXT.get(name)


def tables_for(domain=None):
    """The allowlist for one worker, or every table when domain is None."""
    return DOMAIN_TABLES.get(domain) or ALLOWED_TABLES


def columns_for(table):
    return COLUMNS.get(table, ())
''' % {"allowed": allowed, "denied": tuple(DENIED_STAR), "columns": columns,
       "domain_tables": domain_tables, "domain_meta": domain_meta,
       "glossary": _GLOSSARY, "joins": _JOIN_HINTS, "texts": texts,
       "core": CORE_TABLES,
       "purposes": {t["name"]: PURPOSES.get(t["name"], "supporting table")
                    for t in tables},
       "enums": {"%s.%s" % (t["name"], c): tuple(v)
                 for t in tables for c, v in t["enums"].items()},
       "grids": {t["name"]: tuple(c for c, _ty, _k in t["cols"]
                                  if c.endswith("_json")
                                  and c not in ("images_json", "form_json",
                                                "values_json"))
                 for t in tables
                 if any(c.endswith("_json")
                        and c not in ("images_json", "form_json", "values_json")
                        for c, _ty, _k in t["cols"])}}


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import pymysql
    import mysql_config  # noqa: F401 - loads .env into os.environ
    cfg = mysql_config.config["default"]
    conn = pymysql.connect(host=cfg.MYSQL_HOST, port=int(cfg.MYSQL_PORT),
                           user=cfg.MYSQL_USER, password=cfg.MYSQL_PASSWORD,
                           database=cfg.MYSQL_DATABASE, charset="utf8mb4")
    try:
        tables = introspect(conn, cfg.MYSQL_DATABASE)
    finally:
        conn.close()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_catalog.py")
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_module_text(tables))

    print("Wrote %s" % out_path)
    print("  database: %s   tables: %d" % (cfg.MYSQL_DATABASE, len(tables)))
    for t in tables:
        print("  %-38s %6s rows   %s"
              % (t["name"], t["rows"] or "EMPTY",
                 ",".join(t["domains"]) or "** NO DOMAIN **"))

    # reload the module we just wrote so the reported slice sizes are the real
    # prompt sizes each worker will carry, not an estimate
    print("\n  prompt slice per worker:")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib
    from . import schema_catalog
    importlib.reload(schema_catalog)
    for dom in DOMAINS:
        text = schema_catalog.catalog_prompt_text(dom)
        print("    %-11s %2d tables   %5d chars"
              % (dom, len(schema_catalog.DOMAIN_TABLES[dom]), len(text)))
    print("    %-11s %2d tables   %5d chars  (no slice / orchestrator probes)"
          % ("ALL", len(schema_catalog.ALLOWED_TABLES),
             len(schema_catalog.catalog_prompt_text())))

    orphans = [t["name"] for t in tables if not t["domains"]]
    if orphans:
        print("\n  WARNING: not reachable by any worker: %s" % ", ".join(orphans))


if __name__ == "__main__":
    main()
