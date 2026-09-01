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
import json
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
    "iec_emc_requests": "MASTER EMC test request, one row per TCO/job: product, requester, status, assignment, key dates. is_synthetic=1 marks SEEDED rows. MOST are named 'DEMO ...', but the flag is the test and the name is not - 'FULLFILL Test Bench FB-900' is seeded too, so a product without the DEMO prefix is NOT thereby real. Leave them out of totals, rankings and list-everything answers, and never quote one as a real job. BUT A NAME THE USER TYPES WILL NOT CARRY THE PREFIX: match product_name with LIKE on the words they gave, and if the only rows matching are synthetic, ANSWER ABOUT THOSE ROWS and say they are seeded demo data. Never report a product as absent when a DEMO row for it exists - that reads as a missing record rather than as a filter.",
    "iec_emc_request_tests": "one row per EMC test per request (test_code CE/RE/EFT/ESD/SURGE...); is_selected=1 = in scope; per-test workflow status + engineer",
    "iec_emc_request_service_types": "service types requested (per request)",
    "iec_emc_request_serial_numbers": "EUT serial numbers (per request)",
    "iec_emc_request_additional_models": "extra model numbers covered by the same request (model variance)",
    "iec_emc_request_test_ce_signal_lines": "signal lines to be tested for Conducted Emission (per requested CE test)",
    "iec_emc_request_wireless": "wireless interfaces declared on the request; the parent's has_wireless_interface says whether any were",
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
    # The identity sentence is here because leaving it out produced an answer
    # that was right and unusable: asked which datasheets an engineer recorded,
    # the reply listed three rows all labelled "DEMO-EMC-302" with results D, B
    # and B - correct data, and no way to tell which test got which. A job has
    # many datasheets, so tco_id alone does not name one.
    "datasheet": "HEADER of every filled datasheet, one row per scheduled test: test code, status, result, conditions, who tested it. START HERE for datasheet questions. A ROW IS IDENTIFIED BY tco_id AND test_code TOGETHER - one job has one datasheet per test, so tco_id alone names a job, not a datasheet. Always print test_code when you list or compare datasheets, or the rows cannot be told apart.",
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
    "datasheet_draft_history": "append-only record of EVERY save an engineer made, with changed_fields naming the boxes that changed and form_json holding the whole form as it stood. This is how to answer 'what did it say before', 'who changed this field' and 'when was this value entered'. Finer grained than datasheet_revision, which only captures submissions.",
    "datasheet_records": "the RAW saved form behind each datasheet (draft or submitted). Prefer the `datasheet` tables above - this one stores the form as JSON.",
    "datasheet_fixed_values": "admin-editable fixed values (uncertainty, SOP refs, limits) per datasheet type",
    "basic_standard_map": "admin mapping: product standard -> basic standard used by datasheets",
    # -- reason taxonomy ---------------------------------------------------
    # A lookup table, not lab data, but the ONLY place the vocabulary lives.
    # Two families that must never be mixed: family='test_failure' is an
    # engineering fact about the UNIT (joins datasheet.failure_reason_code);
    # family='review_rejection' is a quality finding about the RECORD (joins
    # datasheet_status_history.reason_code). A cohort query that matched an
    # emission failure against a missing-signature finding would be noise.
    "emc_reason_code": "the lab's REASON CODE vocabulary. family='test_failure' -> why a UNIT failed the standard (join datasheet.failure_reason_code); family='review_rejection' -> why a RECORD was sent back in peer review (join datasheet_status_history.reason_code). Never mix the two families in one grouping. This table is also what tells you a code the user named is not real.",
    # -- report wizard -----------------------------------------------------
    "report_draft": "part-written test reports in the report wizard, one row per request; page_reached says how far the author got. The FINISHED report is planner_entries.report_file_path, not here.",
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
# .claude/hooks/catalog_guard.py reads both of the above when it compares the
# committed catalog against a live database. Without them it would report every
# deliberate omission here as a missing table.

# Kept even at zero rows. For these, "the table is there and it is empty" is a
# real answer - EFT simply has not been run yet, no datasheet has been rejected
# yet. Dropping them would leave the model with no way to say that.
#
# `iec_emc_request` is here for a second reason as well as that one. The rule
# below drops any table that is empty AT BUILD TIME, and these child tables
# empty and refill as jobs come and go: iec_emc_request_serial_numbers held a
# row when the catalog was last generated and holds none now, so a plain
# regeneration would have SILENTLY REMOVED a table the model can currently
# query. A catalog that shrinks whenever a list happens to be empty is not a
# description of the schema, it is a snapshot of one afternoon's data.
KEEP_EMPTY_PREFIXES = ("datasheet", "iec_emc_request")

# Same rule, by exact name, for tables with no useful shared prefix.
KEEP_EMPTY = frozenset(("report_draft",))

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
        # report_draft sits here rather than in requests because the question it
        # answers - "which reports are still being written" - is only meaningful
        # next to planner_entries.report_file_path, which is where a FINISHED
        # report lands. Split across two workers, neither can tell half-written
        # from done.
        "tables": ["planner_entries", "users", "iec_emc_requests", "datasheet",
                   "report_draft"],
    },
    "datasheets": {
        "title": "filled datasheets and measured results",
        "blurb": ("What was actually MEASURED and recorded: results, ambient "
                  "conditions, per-test parameters, every observation cell, the "
                  "equipment and software used, and the review history."),
        # iec_emc_requests WAS deliberately excluded here, on the grounds that
        # `datasheet` already denormalises tco_id, job_number, product_name and
        # eut_class, and that table is the single biggest block of catalog text.
        # Sound reasoning until scope enforcement arrived, and now wrong: the
        # scope guard REJECTS any query touching `datasheet` that does not reach
        # iec_emc_requests.is_synthetic, and this worker touches `datasheet` on
        # essentially every question. Without the table in its slice the guard
        # demands a join to something the allowlist forbids - the worker cannot
        # comply, and every datasheets query fails with no way out. A guard the
        # worker is unable to satisfy is not a guard, it is an outage.
        # emc_reason_code is 16 rows of vocabulary and the join target for both
        # datasheet.failure_reason_code and datasheet_status_history.reason_code.
        # Left out of the catalog it was worse than absent: insights.py queries
        # it on its own connection and works, so the numbers a worker was shown
        # referenced codes whose meaning it could not look up - and any worker
        # writing that join had it REJECTED by sql_guard for an unlisted table.
        "tables": ["datasheet", "datasheet_*", "basic_standard_map",
                   "emc_reason_code", "planner_entries", "users",
                   "iec_emc_requests"],
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
        #
        # `datasheet` and iec_emc_requests come with it, and only because of
        # scope: datasheet_equipment is 78% synthetic-derived, so the guard
        # requires it to reach is_synthetic, and the only route there is
        # datasheet_equipment -> datasheet -> iec_emc_requests. Two extra tables
        # to keep one bridge usable. equipment / maintenance / equipment_history
        # are lab-wide infrastructure with no request of their own and need no
        # filter, so a pure inventory question pays nothing for this.
        "tables": ["equipment", "equipment_history", "maintenance", "users",
                   "datasheet_equipment", "datasheet", "iec_emc_requests"],
    },
}

# --------------------------------------------------------------------------
# Which worker OWNS a table, as opposed to merely being able to see it
# --------------------------------------------------------------------------
# DOMAINS above lists join partners as well as subjects: `datasheet` is in three
# slices so requests and schedule can answer "asked for versus delivered", and
# `users` is in all four because every table joins to it. That breadth is
# deliberate and says nothing about which worker a QUESTION belongs to.
#
# Getting this wrong is not theoretical - it is the first thing I measured. A
# router that weighted each term by how many slices contain its table concluded
# that "datasheet", the most diagnostic word in the whole schema, was noise
# (three slices), and stopped routing "how many CE datasheets does X have" to
# the datasheets worker. Ownership is the signal; visibility is not.
#
# Longest prefix wins, so datasheet_equipment belongs to datasheets even though
# inventory can also see it.
TABLE_OWNER_PREFIXES = (
    ("requests", ("iec_emc_request",)),
    ("datasheets", ("datasheet", "basic_standard_map", "emc_reason_code")),
    ("inventory", ("equipment", "maintenance")),
    ("schedule", ("planner_entries", "report_draft")),
)

# Genuinely shared, with no owner. A term that appears ONLY here - username,
# email, role - cannot route anything, and pretending it can would send every
# question mentioning a person to whichever domain happened to sort first.
UNOWNED_TABLES = ("users",)


def table_owner(name):
    """The one domain a table belongs to, or None when it is shared spine."""
    if name in UNOWNED_TABLES:
        return None
    best, best_len = None, -1
    for domain, prefixes in TABLE_OWNER_PREFIXES:
        for prefix in prefixes:
            if name.startswith(prefix) and len(prefix) > best_len:
                best, best_len = domain, len(prefix)
    return best


# Identifier fragments that carry no routing signal wherever they appear:
# plumbing, timestamps, and the two schema-wide prefixes. Everything else earns
# its place by being discriminative, which is measured rather than judged.
_TERM_STOP = frozenset((
    "iec", "emc", "req", "row", "col", "key", "label", "sort", "order", "num",
    "created", "updated", "modified", "legacy", "custom", "value", "values",
    "text", "json", "data", "info", "name", "names", "code", "codes", "type",
    "types", "count", "total", "min", "max", "new", "old", "other", "others",
    "sections", "and", "the", "for", "with", "per", "one", "two", "all", "any",
    # English function words. The list above covered the ones that turn up in
    # COLUMN names; these turn up once a VALUE is a sentence. Harvesting
    # iec_emc_requests.product_environment_other, which holds 'Tests are
    # performed in accordance with IEC 61000-4-2.', made "are" a term owned by
    # requests - enough to tie "which tests are scheduled for next week" at
    # 2.0-2.0 against schedule and make the router decline a question it had
    # answered correctly. A word this common cannot identify anything.
    "are", "is", "was", "were", "be", "been", "being", "am", "in", "of", "to",
    "as", "at", "on", "by", "from", "into", "onto", "upon", "that", "this",
    "these", "those", "not", "yes", "no", "it", "its", "if", "or", "but",
    "which", "what", "when", "where", "who", "whom", "how", "why", "there",
    "here", "then", "than", "so", "such", "have", "has", "had", "do", "does",
    "did", "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "a", "an", "some", "each", "every", "both", "few", "more", "most",
    "only", "same", "very", "also", "about", "after", "before", "during",
    "between", "through", "under", "over", "above", "below", "again", "once",
))
_TERM_SPLIT = re.compile(r"[^A-Za-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Below this a term is spread over too many domains to mean anything.
TERM_MIN_WEIGHT = 0.34


def _term_words(identifier):
    """cableName -> {cable}; ac_voltage_range -> {voltage, range}."""
    spaced = _CAMEL.sub(" ", str(identifier or ""))
    return {w for w in _TERM_SPLIT.split(spaced.lower())
            if len(w) > 2 and w not in _TERM_STOP}


# Values so generic that no domain owns them. A column whose whole value set is
# drawn from here contributes nothing to routing, whatever it is called.
_GENERIC_VALUES = frozenset((
    "", "yes", "no", "y", "n", "true", "false", "0", "1", "na", "n/a", "nil",
    "none", "null", "other", "others", "custom", "default", "(default)",
    "unknown", "not applicable", "not submitted", "pending", "normal"))


def build_domain_terms(tables):
    """{term: (owning domain, ...)} from the schema's own vocabulary.

    Table names, column names, JSON keys and enum values are the words this
    lab's data actually uses. Hand-listing routing vocabulary meant the router
    knew none of them: `iec_emc_request_accessories` is a table, and "what
    accessories were declared" matched nothing at all.
    """
    owners, sources = {}, {}
    for t in tables:
        owner = table_owner(t["name"])
        if not owner:
            continue
        # (word -> the column it came from) so a match can name its evidence,
        # not just its domain. The table name itself contributes no column.
        found = {}
        for w in _term_words(t["name"]):
            found.setdefault(w, None)
        for c, _typ, _k in t["cols"]:
            if _HIDDEN_COLUMN.search(c):
                continue
            for w in _term_words(c):
                found.setdefault(w, c)
        for column, profile in (t.get("json") or {}).items():
            for k in profile["keys"]:
                for w in _term_words(k):
                    found.setdefault(w, "%s->$.%s" % (column, k))
        for c, vals in list(t["enums"].items()) + [
                (c2, (v2,)) for c2, (v2, _n) in (t.get("constants") or {}).items()]:
            # A flag's VALUES are not vocabulary. 'Yes' and 'No' identify no
            # domain - every one of them has flags - and letting them in put
            # 'yes' under both inventory and requests the moment the equipment
            # register's Yes/No columns were harvested. The values still reach
            # ENUM_VALUES so the model can filter on them; they just do not get
            # to vote on routing. Keeping the two apart is the point: what a
            # column can hold is evidence, what a word identifies is a guess.
            if all(str(v).strip().lower() in _GENERIC_VALUES for v in vals):
                continue
            for v in vals:
                for w in _term_words(v):
                    found.setdefault(w, c)
        for w, col in found.items():
            owners.setdefault(w, set()).add(owner)
            sources.setdefault(w, []).append((t["name"], col))
    return ({w: tuple(sorted(d)) for w, d in sorted(owners.items())},
            {w: tuple(s[:TERM_MAX_SOURCES]) for w, s in sorted(sources.items())})


# How many table/column pairs to remember per term. The hint block names a few
# concrete places to look; a term that occurs in twenty columns is not a hint.
TERM_MAX_SOURCES = 4


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

# Joins made on a TEXT VALUE instead of an id, where the value is not unique on
# either side. {child: (child column, parent, parent column)}.
#
# datasheet_equipment records what the engineer TYPED, so there is no id to join
# on, and RELATIONSHIPS has always warned the name is not unique. What it could
# not say is what that costs, and the cost is not the join failing - it is the
# join SUCCEEDING with too many rows. Measured on this database: "BNC Cable"
# matches 2 inventory rows AND appears twice on a datasheet, so one real usage
# becomes 8 joined rows. Asked which instruments were used while out of
# calibration, the assistant answered "two tests" against a true 8.
#
# Measured rather than described, because the numbers are the whole warning and a
# hand-written note would go stale the moment someone fixes a duplicate name.
#
# Serial number is NOT the better key here and was checked before recommending
# anything: matching on serial_no covers 12 of 26 rows at 2.67x fan-out against
# name's 24 of 26 at 1.17x. The name is the best key available; the fix is to
# count distinctly, not to join differently.
TEXT_JOINS = {
    "datasheet_equipment": ("equipment_name", "equipment", "name"),
}

_ENUMISH = re.compile(
    r"status|type|code|role|mode|class|result|state|level|category|active|"
    r"verdict|decision|^value$|grid_key|col_key|priority|family", re.I)

# How many distinct values still count as a vocabulary worth listing.
#
# Was 15, which silently swallowed the one column where the list matters most:
# emc_reason_code.code has exactly SIXTEEN codes, so the taxonomy table went
# into the prompt with no taxonomy in it. Measured against this database, 20
# admits that column and nothing else - the next enum-ish column up has 12
# values, so this is not a slope.
MAX_ENUM_VALUES = 20

# A classification is REUSED - many rows share one value. An identifier is not,
# and by cardinality alone the two are indistinguishable in a small table:
# content_hash has 16 distinct values over 16 rows and looks every bit as
# categorical as a status column until you notice nothing repeats. Requiring
# each class to average this many rows is what lets the harvest below stop
# depending on the column NAME.
#
# Name-gating was losing real classifications: equipment.calibration_required
# (Yes 74 / No 15), ic_required and maintenance_required are Yes/No columns on
# the equipment register that _ENUMISH does not match, so the model was never
# told those values exist and could not filter on them at all. _ENUMISH is kept
# below, but only to RESCUE a genuine status column in a table too small to
# satisfy the repetition test - never to gate one out.
MIN_CLASS_REPEAT = 3

# A column holding one value over 9 rows is a trap worth warning about; over 1
# row it is just a table nobody has filled in yet, and the warning is noise
# that costs prompt space. Measured: this threshold keeps the 21 real ones -
# including iec_emc_requests.requester_status, 'At Review' on every row, which
# is the column an eval question was answered from instead of `status` - and
# drops 11 that say nothing.
MIN_CONSTANT_ROWS = 8

# Position, not category. `row_no` 1..6 repeats happily and means nothing.
_ORDINAL_NAME = re.compile(
    r"(_no|_order|_index|_seq|_num|_number|_count|_qty|_sort)$"
    r"|^(no|seq|sort_order|revision_no)$", re.I)

# Columns someone would plausibly filter on, used only to decide which
# single-valued columns are worth a warning. A constant `status` is a trap; a
# constant `col_label` is just an empty field.
_FLAGGISH = re.compile(r"^(is_|has_|can_|should_)|_required$|_flag$", re.I)


# People and identity fields are never a classification, however few distinct
# values a small table gives them. This is not tidiness: harvesting
# equipment.test_name ('CE', 'CE,RE', 'EFT,Surge,PFMF,VoltageDips') fed test-code
# words into the inventory domain and cost a routing case - "which tests are
# scheduled for next week" stopped resolving to the schedule worker, because ESD
# and SURGE had become inventory words too. Names also have no business in a
# prompt when the question is "what values can this column hold".
#
# Measurements are NOT listed here on purpose. Blocklisting 'freq' would also
# kill equipment.calibration_frequency, whose values are 'Annual' / 'Bi-Annual'
# and which is a real classification. They are excluded by testing whether the
# VALUES are numeric instead - the same lesson as powerSignal in the JSON scan.
_NOT_A_CLASS = re.compile(
    r"name$|^name|email|phone|contact|_by$|^by_|witness|manufacturer|"
    r"designation|department|division|^site$|site$|group$|address|"
    r"version$|^make$|^model|serial|comment|remark|^note|descri", re.I)


# "Not filled in" spelled the several ways this schema spells it. These are not
# values in their own right, so they should not tip a column one way or the
# other when deciding what KIND of column it is.
_SENTINEL_VALUES = {"", "na", "n/a", "none", "null", "-", "--", "?", "tbd", "nil"}


def _values_are_measurements(vals):
    """Numbers and ranges kept in a text column: data, not categories."""
    seen = [str(v).strip() for v in vals if str(v).strip()]
    if not seen:
        return False
    real = [v for v in seen if v.lower() not in _SENTINEL_VALUES]
    if set(real) <= {"0", "1"}:
        # A boolean is not a measurement. datasheet_modification.mod_state is
        # varchar holding '0'/'1' and the numeric test threw it away.
        #
        # A boolean WITH a sentinel beside it is still a boolean, and testing
        # the raw value list missed that: datasheet.eut_modification_state
        # holds '0' (9 rows), 'NA' (31) and '1' (1), so two of its three values
        # are digits, the ratio test called the column a measurement, and the
        # model was told nothing about a plainly categorical field. Asked which
        # units were modified it then has to guess the encoding, and a guess of
        # 'Yes' returns no rows and reads as a real absence.
        return False
    numeric = sum(
        1 for v in real
        if v.replace(".", "").replace("-", "").replace(" ", "").replace(",", "").isdigit())
    return numeric > len(real) / 2


def _value_shape_ok(vals, ctype):
    """False for dates kept in text and for digests - never categories."""
    seen = [str(v).strip() for v in vals if str(v).strip()]
    if not seen:
        return True
    # Both orders: 2026-08-17 and 17/08/2026. Only the first was caught, so
    # datasheet_modification.fitted_date came through as a two-class column.
    dateish = 0
    for v in seen:
        parts = re.split(r"[-/.]", v)
        if len(parts) == 3 and all(p.isdigit() for p in parts) and len(v) >= 8:
            dateish += 1
    if dateish > len(seen) / 2:
        return False
    if "char" in (ctype or "") and len(seen[0]) >= 32:
        hexish = sum(1 for v in seen if len(v) >= 16
                     and all(ch in "0123456789abcdefABCDEF" for ch in v))
        if hexish > len(seen) / 2:
            return False
    return True

# Credential-ish columns, and personal contact details: omitted from the
# catalog entirely. Keep in step with sql_guard's DENIED_COLUMN_PATTERNS
# and DENIED_PII_PATTERNS. sql_guard blocks
# them at validation time too - this just keeps them out of the model's sight.
_HIDDEN_COLUMN = re.compile(
    r"password|\bpwd\b|secret|api_key|(?:reset|auth|session|csrf|access|refresh)_token"
    r"|email|phone|\bmobile\b", re.I)

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

# A hidden *_json column is one of TWO different things and they need
# different notes. Grid columns (obs_*_json, *_rows_json, *_measurements_json)
# hold a measurement matrix that IS also normalised into datasheet_measurement.
# Blob columns hold a whole saved form and are normalised nowhere. Keying the
# note on "ends with _json" conflated them, and told the report wizard's draft
# store to go look for its rows in datasheet_measurement. Same split as
# probes._NOT_A_GRID, for the same reason.
_BLOB_COLUMNS = frozenset(("form_json", "images_json", "values_json"))

_BLOB_NOTE = ("form_json / images_json hold the whole saved form as one raw "
              "blob and are not selectable here. Nothing inside them is "
              "reachable from SQL: answer from the scalar columns on this "
              "table, or say the value is not recorded in queryable form.")

# Facts a person in this building knows and no amount of column-reading reveals.
# Hand-written, because they are judgements about MEANING; the numbers around
# them stay measured. Rendered on the table they belong to, which is where a
# model writing SQL against that table will actually be looking.
_FAILED_NOTE = (
    "WHAT COUNTS AS FAILED: `result` holds either PASS/FAIL or an EMC "
    "performance criterion letter - A and B are acceptable, C and D are not. So "
    "a unit that did not pass is `result` IN ('FAIL','C','D'), and the reliable "
    "test is `failure_reason_code IS NOT NULL`, which is set on exactly the "
    "campaigns that failed. WHERE result='FAIL' alone is the mistake to avoid: "
    "it silently misses every unit that failed on criterion D and answered "
    "\"1 unit failed\" for a lab where three had. NULL result means no outcome "
    "was recorded, which is not a pass.")

_REJECTED_NOTE = (
    "TWO DIFFERENT REJECTIONS, and this table is neither. A REQUEST can be "
    "refused by an admin - rejected_at / rejected_by / rejection_reason on this "
    "table - and a filled DATASHEET can be sent back by a peer reviewer, which "
    "is recorded nowhere near here: it is datasheet_status_history WHERE "
    "to_status='Rejected', carrying reason_code and actor_name. Asked who sends "
    "back the most work in peer review, counting "
    "this table's columns returned zero and produced the answer \"there are zero "
    "rejections logged in peer review\" when there were six.")

_SCHEDULE_NOTE = (
    "A STATUS IS NOT A DATE. in_progress / scheduled / datasheet_uploaded are "
    "WORKFLOW states: an entry stays in_progress until a person advances it, "
    "however long ago its dates passed. So a question about what is happening "
    "NOW, TODAY or CURRENTLY must test start_date / end_date against CURDATE() "
    "as well, and an entry whose end_date is behind us is OVERDUE, not active. "
    "Asked what was being tested right now, a status-only filter returned 23 "
    "in_progress rows whose scheduled end dates had EVERY ONE already passed - "
    "the most recent by six days, the oldest by four months - and presented "
    "stalled work as current activity. If the two disagree, say so: report the "
    "count and that their scheduled window has closed.")

_DONE_NOTE = (
    "FINISHED AND PASSED ARE DIFFERENT COLUMNS. `status` says how far the "
    "PAPERWORK got - Approved (33 rows) or Draft (14) - and `result` says what "
    "HAPPENED to the unit. A test that is complete is status='Approved'; a test "
    "that passed is result IN ('PASS','A','B'). Asked which tests were completed "
    "and which were not, an answer grouped them by result instead and reported "
    "compliance, which is a different question: a Draft sheet can already hold a "
    "PASS, and an Approved one can hold a D. And there is no third word for "
    "either - `compliant` is not a column, not a value, and appears on no screen "
    "in this app, so inventing it hands the reader a term with nowhere to look "
    "it up.")

SEMANTIC_NOTES = {
    "datasheet": (_FAILED_NOTE, _DONE_NOTE),
    "datasheet_revision": (_FAILED_NOTE,),
    "iec_emc_requests": (_REJECTED_NOTE,),
    "planner_entries": (_SCHEDULE_NOTE,),
}

_OBS_NOTE = ("the *_json columns hold this test's grids with their own labels "
             "and block structure. You do NOT have to parse them: every cell is "
             "also a row in datasheet_measurement (numbers, with value_num for "
             "comparisons) or datasheet_observation (criterion letters). Prefer "
             "those - they are plain SQL. read_grid(datasheet_id) is still there "
             "when you need a grid laid out exactly as the form shows it.")

_GLOSSARY = """How lab vocabulary maps to this schema (read this before writing SQL):
- "job" / "TCO" / "project" / "request"  -> iec_emc_requests (tco_id, job_number)

TCO_ID AND JOB_NUMBER ARE DIFFERENT COLUMNS AND USERS SAY BOTH. The application
labels a field "Job Number" and shows TFS-EMC-2026-002, so that is what a user
types; tco_id is the other one and looks like IEC-EMC-004. They are NOT
interchangeable and one is not a prefix of the other:

      tco_id      IEC-EMC-004        (also DEMO-EMC-3xx for seeded demo rows)
      job_number  TFS-EMC-2026-002   (also DEMO-JOB-3xx)

Decide which column the identifier belongs to by its SHAPE before you filter on
it. A job number used in a tco_id filter matches nothing, returns zero, and looks
exactly like a real absence - asked whether the final report was uploaded for job
TFS-EMC-2026-002, the assistant filtered datasheet.tco_id by it, got 0 and
answered "Yes, it has been uploaded". A job number can be resolved to its tco_id
with resolve_entity(kind='job'), and both are worth showing in an answer.

- "final report" / "the report" / "report uploaded" -> planner_entries.report_file_path
  (with report_uploaded_at / report_uploaded_by / report_comments). This is the
  Word document produced AFTER the datasheets are approved and is a DIFFERENT
  thing from a datasheet: the application has an "Upload Final Report" button for
  it. A report question is answered from planner_entries, never by counting
  `datasheet` rows - a job with no datasheets recorded is not a job with a report.
- "datasheet uploaded" (planner_entries.status) -> a FILE was attached, which is
  NOT the same as the form having been filled in. Two of the eight entries in that
  status have a file and no `datasheet` row at all, so "the datasheet is done"
  and "the datasheet is recorded and queryable" are different claims.
- "EUT" / "product" / "unit under test"  -> iec_emc_requests.product_name / model_number
- "test" in the sense of WHAT WAS ASKED   -> iec_emc_request_tests (+ iec_emc_request_test_* detail)
- "test" in the sense of WHAT WAS RUN     -> planner_entries (schedule) and `datasheet` (result)
- "datasheet" / "the sheet" / "results"   -> `datasheet` + datasheet_<code> + datasheet_observation
- "observation" / "criterion A|B|C|D"     -> datasheet_observation.value
- "reading" / "measurement" / "limit"     -> datasheet_measurement.value_num (filter revision_no)
- "margin" / "how close to the limit"     -> datasheet_measurement, col_key like '%_margin'
- "previous version" / "before it was rejected" -> datasheet_revision + datasheet_rev_<code>
- "who changed" / "when was it entered" / "edit history" -> datasheet_draft_history
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
replace spaces with underscores, and map the four names above.

SOME TEXT COLUMNS HOLD JSON. The type says `text` and the name gives no hint,
but the value is one object per row - a cable is {cableName, length,
powerSignal, shielded, purpose}. Any table whose entry below has an "X is
JSON, not text" line works this way, and the keys are listed there.

Read a key as a normal column and everything else follows - WHERE, GROUP BY,
ORDER BY, COUNT all work on the extracted value:

    SELECT JSON_UNQUOTE(JSON_EXTRACT(cable_value,'$.cableName')) AS cable_name,
           JSON_UNQUOTE(JSON_EXTRACT(cable_value,'$.powerSignal')) AS kind
    FROM iec_emc_request_cables WHERE request_id = 4

Three rules. Never SELECT the raw column - it returns the whole object and the
user gets punctuation instead of an answer. Never invent a key that is not
listed; JSON_EXTRACT on a missing key returns NULL, which reads exactly like a
recorded blank. And JSON_TABLE is not available to you - extract the keys you
need one at a time."""

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
  and result denormalised, so most questions ABOUT A DATASHEET need NO join at
  all. But a question about the SCHEDULE is not one of those: 10 of the 23
  in_progress planner entries have no datasheet row yet, so LEFT JOINing to
  `datasheet` to read job_number or product_name off it prints NULL on every one
  of them. Asked what was being tested right now, that is exactly what happened -
  ten NULLs under Job Number, while iec_emc_requests held the value for all of
  them. For a planner listing take those fields from iec_emc_requests via
  p.test_request_id = r.id.
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


# --------------------------------------------------------------------------
# Two facts that read like ordinary columns and are not
# --------------------------------------------------------------------------
# Both were measured after an answer went wrong on them, and both are stated as
# NUMBERS taken from the live database rather than as prose someone typed once -
# a claim about the data that cannot re-check itself is how the catalog went
# stale before.

def _revision_pointer(cur, name):
    """Is <table>.revision_no a NEXT-TO-EDIT pointer? (checked, higher, gap).

    Asked what changed in the revision that passed, a worker joined
    datasheet_draft_history on dh.revision_no = d.revision_no. datasheet.
    revision_no was 3; the frozen revisions are 1 and 2, and revision 3 does not
    exist yet. It found nothing and answered "no recorded changes" against 22
    draft-history rows and up to 95 changed fields.
    """
    if name != "datasheet":
        return None
    try:
        cur.execute(
            "SELECT COUNT(*), SUM(d.revision_no > f.maxrev), "
            "       MIN(d.revision_no - f.maxrev), MAX(d.revision_no - f.maxrev) "
            "FROM `datasheet` d JOIN (SELECT datasheet_id, MAX(revision_no) maxrev "
            "  FROM datasheet_revision GROUP BY datasheet_id) f ON f.datasheet_id = d.id")
        checked, higher, lo, hi = cur.fetchone()
        if not checked or not higher:
            return None
        return int(checked), int(higher), int(lo or 0), int(hi or 0)
    except Exception:  # noqa: BLE001 - a note is never worth failing over
        return None


def _reason_in_comment(cur, name):
    """Does the reason live in `comment` rather than `reason_code`? (n, total).

    Same answer reported "approval reason code is not recorded (null)". True -
    and the reason was sitting in `comment`: "Equipment listed are not the ones
    which were in the Request object", then "Now its correct".
    """
    if name != "datasheet_status_history":
        return None
    try:
        cur.execute(
            "SELECT SUM(reason_code IS NULL AND comment IS NOT NULL AND comment <> ''), "
            "       SUM(reason_code IS NOT NULL), COUNT(*) "
            "FROM datasheet_status_history "
            "WHERE to_status IN ('Approved','Rejected')")
        only_comment, coded, total = cur.fetchone()
        if not total or not only_comment:
            return None
        return int(only_comment), int(coded or 0), int(total)
    except Exception:  # noqa: BLE001
        return None


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
        if rows == 0 and not name.startswith(KEEP_EMPTY_PREFIXES) \
                and name not in KEEP_EMPTY:
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

        # Every column holding a definite finite set, decided from the DATA.
        # See MIN_CLASS_REPEAT for why this is no longer gated on the name.
        enums = {}
        constants = {}
        for c, t, k in cols:
            if _HIDDEN_COLUMN.search(c) or _LARGE_COLUMN.match(c):
                continue
            if c in fks:
                continue                      # a reference, not a class
            if c == "id" or c.endswith("_id"):
                continue
            if _NOT_A_CLASS.search(c):
                continue                      # a person or an identity field
            textish = t.startswith(("varchar", "char", "enum", "set", "tinytext"))
            intish = t.startswith(("tinyint", "smallint", "int", "bit", "bool"))
            if not (textish or intish):
                continue
            # A NUMERIC key is a surrogate id and never a class. A TEXT primary
            # key is the opposite: in a lookup table it IS the vocabulary.
            # emc_reason_code is keyed on `code varchar(40) PRI` and holds the
            # sixteen-code taxonomy this whole feature turns on - excluding
            # every PRI threw the taxonomy out of the catalog. Unique and text
            # keys can never satisfy the repetition test (distinct always equals
            # non_null), so they reach the harvest only through the _ENUMISH
            # rescue below, which is the right gate for them.
            if k in ("PRI", "UNI") and intish:
                continue
            if intish and _ORDINAL_NAME.search(c):
                continue
            try:
                cur.execute(
                    "SELECT COUNT(`%s`), COUNT(DISTINCT `%s`)%s FROM `%s`"
                    % (c, c, ", MAX(CHAR_LENGTH(`%s`))" % c if textish else ", 0",
                       name))
                non_null, distinct, longest = cur.fetchone()
            except Exception:  # noqa: BLE001 - one odd column is not worth failing
                continue
            if not non_null or not distinct or distinct > MAX_ENUM_VALUES:
                continue
            if textish and (longest or 0) > 80:
                continue                      # prose, whatever its cardinality
            if intish and distinct > 12:
                continue

            # The repetition test, or a rescue for a named status column in a
            # table with barely any rows in it yet.
            repeats = distinct * MIN_CLASS_REPEAT <= non_null
            if not repeats and not _ENUMISH.search(c):
                continue

            cur.execute("SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IS NOT NULL "
                        "ORDER BY `%s` LIMIT %d"
                        % (c, name, c, c, MAX_ENUM_VALUES + 1))
            vals = [str(r[0]) for r in cur.fetchall()]
            if not vals or len(vals) > MAX_ENUM_VALUES:
                continue
            if not _value_shape_ok(vals, t):
                continue
            if textish and _values_are_measurements(vals):
                continue                      # freq ranges, temperatures, dips
            if any(len(str(v).split()) >= 5 or str(v).rstrip().endswith(".")
                   for v in vals):
                # A class label is a label, not a sentence. A column mixing the
                # two - environment_value holds '', 'Basic Electromagnetic',
                # 'Other' and a full sentence about IEC 61000-4-2 - is a
                # free-text field, so the whole column goes rather than leaving
                # a value list that looks complete and is not.
                continue
            if len(vals) == 1:
                # Classification-shaped and empty of information. Worth saying
                # so only for a column someone would filter on - and worth
                # saying LOUDLY, because it reads like an outcome and is not:
                # datasheet_revision.status is 'Draft' on every row including
                # the approved ones.
                if ((_ENUMISH.search(c) or _FLAGGISH.search(c))
                        and non_null >= MIN_CONSTANT_ROWS):
                    constants[c] = (vals[0], non_null)
                continue
            enums[c] = sorted(vals)

        # How badly does this table's text join multiply? See TEXT_JOINS.
        fanout = None
        spec = TEXT_JOINS.get(name)
        if spec:
            ccol, ptable, pcol = spec
            try:
                cur.execute("SELECT COUNT(*) FROM `%s`" % name)
                src = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*), COUNT(DISTINCT c.id) FROM `%s` c JOIN `%s` p "
                    "ON p.`%s` = c.`%s`" % (name, ptable, pcol, ccol))
                joined, matched = cur.fetchone()
                cur.execute(
                    "SELECT c.`%s`, COUNT(*) FROM `%s` c JOIN `%s` p ON p.`%s` = c.`%s` "
                    "GROUP BY c.`%s` HAVING COUNT(*) > COUNT(DISTINCT c.id) "
                    "ORDER BY COUNT(*) DESC LIMIT 3"
                    % (ccol, name, ptable, pcol, ccol, ccol))
                worst = [(r[0], r[1]) for r in cur.fetchall()]
                if src and joined and matched and joined > matched:
                    fanout = (matched, src, joined, ptable, pcol, worst)
            except Exception:  # noqa: BLE001 - a note is never worth failing over
                pass

        json_cols = {}
        for c, t, _k in cols:
            if _HIDDEN_COLUMN.search(c) or _LARGE_COLUMN.match(c):
                continue
            if not (t.startswith("varchar") or t.startswith("text")
                    or t.startswith("json") or t.startswith("char")):
                continue
            profile = _json_profile(cur, name, c)
            if profile:
                json_cols[c] = profile

        pointer = _revision_pointer(cur, name)
        reason_gap = _reason_in_comment(cur, name)

        tables.append({"name": name, "rows": rows, "cols": cols,
                       "fks": fks, "enums": enums, "constants": constants,
                       "coverage": coverage,
                       "json": json_cols, "fanout": fanout,
                       "pointer": pointer, "reason_gap": reason_gap,
                       "domains": _domains_for(name)})
    return tables


# --------------------------------------------------------------------------
# JSON hidden inside a text column
# --------------------------------------------------------------------------
# The lab's request forms store repeating structures as one JSON object per
# row - a cable is {cableName, length, powerSignal, shielded, purpose}. The
# column is declared `text`, so NOTHING about the schema says it is JSON:
# information_schema reports varchar/text, and the name gives no hint either
# (`cable_value`, `custom_spec`, `value_text`). The catalog therefore described
# 16 structured columns as flat strings.
#
# What that costs is not a syntax error, it is a plausible wrong answer. Asked
# for cable information the model selects `cable_value` and gets a JSON blob it
# must read in prose; asked how many cables are shielded it cannot filter at
# all, because `WHERE shielded = 'Shielded'` names a column that does not
# exist. Neither failure looks like a failure.
#
# So the shape is MEASURED from the rows, not guessed from the type or the
# name, and re-measured on every build. Detection is deliberately by content:
# sample the column, and if half of what is there parses as a JSON object or
# array, it is a JSON column whatever the DDL claims.
JSON_SAMPLE_ROWS = 40
# Above this many keys the object is a whole saved form, not a record: naming
# 129 keys would cost more prompt than the table is worth and the useful values
# are normalised elsewhere anyway. Those keep the blob note instead.
JSON_MAX_KEYS = 12
# Per key, the same "is this a vocabulary" test the column enums get.
JSON_MAX_KEY_VALUES = 6
JSON_MAX_VALUE_LEN = 40


def _json_profile(cur, table, column):
    """{key: {"values": [...], "nested": bool}} for a text column holding JSON,
    or None when the column is not JSON / is too wide to describe.

    Also returns None for an EMPTY column: with no rows there is nothing to
    measure, and inventing a key list would be exactly the kind of unverified
    schema claim this module exists to avoid.
    """
    try:
        # ORDER BY so the sample - and therefore the key value lists this emits
        # into a COMMITTED file - is reproducible. Without it MySQL is free to
        # return any 40 rows, so two builds of an unchanged database could
        # produce different catalogs and a diff nobody could explain.
        cur.execute("SELECT `%s` FROM `%s` WHERE `%s` IS NOT NULL AND `%s` <> '' "
                    "ORDER BY `%s` LIMIT %d"
                    % (column, table, column, column, column, JSON_SAMPLE_ROWS))
        raw = [r[0] for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 - a note is never worth failing the build
        return None
    if not raw:
        return None

    objects, looked_like_json = [], 0
    for value in raw:
        text = value if isinstance(value, str) else str(value)
        if text.lstrip()[:1] not in ("{", "["):
            continue
        looked_like_json += 1
        try:
            objects.append(json.loads(text))
        except (ValueError, TypeError):
            pass
    # Half, not all: one hand-edited row must not hide the other thirty-nine.
    if not objects or looked_like_json * 2 < len(raw):
        return None

    keys, is_list = {}, False
    for obj in objects:
        items = obj if isinstance(obj, list) else [obj]
        is_list = is_list or isinstance(obj, list)
        for item in items:
            if not isinstance(item, dict):
                continue
            for k, v in item.items():
                bucket = keys.setdefault(k, {"values": set(), "nested": False})
                if isinstance(v, (dict, list)):
                    bucket["nested"] = True
                    continue
                text = str(v).strip()
                if text and len(text) <= JSON_MAX_VALUE_LEN:
                    bucket["values"].add(text)
    if not keys or len(keys) > JSON_MAX_KEYS:
        return None

    out = {}
    for k, bucket in keys.items():
        values = sorted(bucket["values"])
        out[k] = {"values": tuple(values) if len(values) <= JSON_MAX_KEY_VALUES else (),
                  "example": values[0] if values else "",
                  "nested": bucket["nested"]}
    return {"is_list": is_list, "keys": out}


def render_json_block(t):
    """The lines describing this table's JSON columns, keys first."""
    lines = []
    for column, profile in sorted((t.get("json") or {}).items()):
        lines.append("  %s is JSON, not text%s. Keys:"
                     % (column, " (a LIST of these objects)" if profile["is_list"] else ""))
        for key in sorted(profile["keys"]):
            info = profile["keys"][key]
            if info["nested"]:
                lines.append("    %s - a nested object/array, read it whole" % key)
            elif info["values"]:
                lines.append("    %s - values: %s"
                             % (key, ", ".join("'%s'" % v for v in info["values"])))
            elif info["example"]:
                lines.append("    %s - e.g. '%s'" % (key, info["example"]))
            else:
                lines.append("    %s" % key)
    return lines


def visible_columns(t):
    """Column names the model is allowed to see (and therefore to select)."""
    return [c for c, _typ, _k in t["cols"]
            if not _HIDDEN_COLUMN.search(c) and not _LARGE_COLUMN.match(c)]


def render_table_text(t):
    # {ROWS} is filled in at render time from catalog_stats. Baking the count
    # here is what made the prompt announce "datasheet_harmonic (EMPTY - no rows
    # yet)" about a table that had a row in it.
    head = "### %s {ROWS}- %s" % (
        t["name"], PURPOSES.get(t["name"], "supporting table"))
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
    for note in SEMANTIC_NOTES.get(t["name"], ()):
        lines.append("  %s" % note)
    hidden = [c for c, _t, _k in t["cols"] if _LARGE_COLUMN.match(c)]
    if hidden:
        grids = [h for h in hidden
                 if h.endswith("_json") and h not in _BLOB_COLUMNS]
        if grids:
            lines.append("  note: %s" % _OBS_NOTE)
        elif t["name"] == "datasheet_records":
            lines.append("  note: %s" % _RECORDS_NOTE)
        elif any(h in _BLOB_COLUMNS for h in hidden):
            lines.append("  note: %s" % _BLOB_NOTE)
        else:
            # Everything else large: block diagrams, signatures, the
            # equipment_history old/new value dumps. These got NO note at all,
            # so the model saw a column list with holes in it and no way to
            # know a hole was deliberate rather than a gap in the data.
            lines.append("  note: these columns exist but are too large to "
                         "return and are not selectable here: %s."
                         % ", ".join(sorted(hidden)))
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
    if t.get("fanout"):
        matched, src, joined, ptable, pcol, worst = t["fanout"]
        examples = ", ".join("'%s' -> %d rows" % (v, n) for v, n in worst)
        lines.append(
            "  THIS JOIN MULTIPLIES ROWS: joining to `%s` ON %s matches %d of %d "
            "rows here but returns %d, because the text is not unique on either "
            "side (%s). COUNT(*) over that join is therefore WRONG - it counts "
            "duplicates. Use COUNT(DISTINCT %s.id) for usages, or "
            "COUNT(DISTINCT %s.datasheet_id) for datasheets, and never SUM over "
            "it. Matching on serial_no instead is worse, not better: it covers "
            "fewer rows and multiplies more."
            % (ptable, pcol, matched, src, joined, examples, t["name"], t["name"]))
    if t.get("pointer"):
        checked, higher, lo, hi = t["pointer"]
        lines.append(
            "  revision_no HERE IS A POINTER, NOT A REVISION THAT EXISTS. Measured "
            "on this database: %d of %d datasheets carry a revision_no HIGHER than "
            "their highest frozen revision (by %s). It means 'the next revision to "
            "be edited'. So `WHERE revision_no = <the parent's revision_no>` against "
            "datasheet_revision or datasheet_draft_history matches NOTHING and looks "
            "exactly like 'no changes were recorded'. To read what actually happened "
            "use MAX(revision_no) from datasheet_revision, or compare consecutive "
            "revisions - or better, call analyse_history(review_history), which does "
            "this correctly."
            % (higher, checked, "%d" % lo if lo == hi else "%d-%d" % (lo, hi)))
    if t.get("reason_gap"):
        only_comment, coded, total = t["reason_gap"]
        lines.append(
            "  THE REASON IS OFTEN IN `comment`, NOT `reason_code`. Measured on "
            "this database: of %d decided rows, %d carry a written comment with NO "
            "reason_code, and %d carry a code. So reporting 'no reason was recorded' "
            "on the strength of a NULL reason_code is wrong whenever a comment "
            "exists - read BOTH, and quote the comment when the code is absent."
            % (total, only_comment, coded))
    # Value lists, constants and JSON keys are NOT baked in. They are measured
    # per render by catalog_stats, because they change when somebody uses the
    # app rather than when somebody migrates the schema.
    return "\n".join(lines)


def read_reason_families(conn):
    """{code: (family, label)} from emc_reason_code.

    Sixteen rows, and the only thing that says which AXIS a reason belongs to.
    verify needs it to notice a review_rejection code being presented as why a
    unit failed a standard, and verify has no database - so it travels in the
    committed catalog. Stable rather than measured: a taxonomy changes when
    somebody adds a code, which is a migration, not a Tuesday.
    """
    out = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, family, label FROM emc_reason_code")
            for code, family, label in cur.fetchall():
                out[str(code)] = (str(family or ""), str(label or ""))
    except Exception:  # noqa: BLE001 - a fresh database may not have it yet
        pass
    return out


def build_module_text(tables, families=None):
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
# ENUM_VALUES: served fresh by __getattr__ from catalog_stats()['enums'].

# Columns that hold ONE value on every row. Classification-shaped and empty of
# information: filtering on them matches everything, and reading one as an
# outcome is always wrong. datasheet_revision.status is 'Draft' on all 12 rows
# including the approved ones - the outcome lives in
# datasheet_status_history.to_status. Kept structured as well as in the prompt
# so a probe can answer "can this column tell me that?" without a query.
# "table.column" -> (the only value, rows carrying it).
# CONSTANT_COLUMNS: served fresh by __getattr__ from catalog_stats()['constants'].

# The measurement / observation grids, per table. These columns are hidden from
# COLUMNS on purpose - a model cannot write useful SQL against JSON and
# selecting them blows the size budget - but the data is the substance of a
# datasheet, so probes.read_grid() reaches them through this map instead.
GRID_COLUMNS = %(grids)r

# Structure inside text columns that hold JSON, measured from the rows rather
# than read off the DDL (which says `text` and gives no hint). Keyed
# "table.column" -> {"is_list": bool, "keys": {key: {values, example, nested}}}.
# probes.find_field reads this so "where is the cable length recorded" can
# answer with a JSON path instead of missing the column entirely.
# JSON_KEYS: served fresh by __getattr__ from catalog_stats()['json_keys'].

# What each table held WHEN THIS FILE WAS GENERATED. Not a live figure - the
# whole catalog is a photograph, and this is the part that dates fastest.
# Every table heading in the prompt quotes it, so a stale count is a lie told
# to the model: it once said `datasheet (24 rows)` against an empty table and
# the model believed it. .claude/hooks/catalog_guard.py compares this against
# the live database at session start so drift is announced, not discovered.
# ROW_COUNTS: served fresh by __getattr__ from catalog_stats()['row_counts'].

# WHICH columns hold a classification, and which hold JSON. The judgement, not
# the measurement: deciding that equipment.calibration_required is a class and
# content_hash is not is schema-shaped work (see MIN_CLASS_REPEAT in
# build_catalog) and belongs in a reviewed file. WHAT those columns currently
# contain is measured at run time by catalog_stats, because it changes whenever
# somebody uses the app. Keeping the two apart is what stops routing shifting
# under the model's feet every time a new status value appears.
# Which AXIS each reason code belongs to: test_failure is why the UNIT failed
# a standard, review_rejection is why the RECORD was sent back in peer review.
# {code: (family, label)}. verify reads this to catch an answer that presents
# one as the other, which is the specific error the taxonomy exists to prevent.
REASON_FAMILIES = %(families)r

CLASS_COLUMNS = %(class_columns)r
JSON_COLUMNS = %(json_columns)r


# The schema's own vocabulary, for routing a question to the worker that can
# actually see the tables it is about. {term: (owning domain, ...)} - a term
# owned by one domain is a strong signal, one owned by three is noise.
# intent.single_domain() scores against this; see TABLE_OWNER_PREFIXES in
# build_catalog for why ownership and not slice membership.
DOMAIN_TERMS = %(domain_terms)r

# Where each term actually came from: ((table, column or JSON path), ...). Lets
# a router explain itself - "cables matched iec_emc_request_cables.cable_value"
# - instead of only naming a worker.
TERM_SOURCES = %(term_sources)r

# What a term is worth: full marks when one domain owns it, half when two.
TERM_MIN_WEIGHT = %(term_min_weight)r


def term_weight(term):
    """0.0 when the term says nothing about which worker owns the question."""
    owners = DOMAIN_TERMS.get(term)
    if not owners:
        return 0.0
    weight = 1.0 / len(owners)
    return weight if weight >= TERM_MIN_WEIGHT else 0.0


def json_paths_for(table, column=None):
    """[(column, key), ...] this table records inside JSON. For find_field."""
    out = []
    for ref, profile in (_stats().get('json_keys') or {}).items():
        tbl, col = ref.split(".", 1)
        if tbl != table or (column and col != column):
            continue
        out.extend((col, k) for k in sorted(profile.get("keys") or {}))
    return out


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


# ---------------------------------------------------------------------------
# The measured half, fetched per render rather than baked in above.
# ---------------------------------------------------------------------------

def _stats():
    """Current row counts, value lists, constants and JSON keys.

    Imported lazily: catalog_stats imports this module back, and a prompt has
    to render even when the database is unreachable.
    """
    try:
        from . import catalog_stats
        return catalog_stats.current()
    except Exception:  # noqa: BLE001
        return {"row_counts": {}, "enums": {}, "constants": {}, "json_keys": {}}


# The volatile names are deliberately NOT module attributes. Python calls
# __getattr__ only for names it cannot find, so defining them would freeze them.
# Everything that already said schema_catalog.ENUM_VALUES keeps working and
# starts getting the current answer - except `from schema_catalog import
# ENUM_VALUES`, which binds a snapshot at import and must be changed to
# attribute access.
_VOLATILE = {"ROW_COUNTS": "row_counts", "ENUM_VALUES": "enums",
             "CONSTANT_COLUMNS": "constants", "JSON_KEYS": "json_keys"}


def __getattr__(name):
    if name in _VOLATILE:
        return _stats().get(_VOLATILE[name]) or {}
    raise AttributeError("module %%r has no attribute %%r" %% (__name__, name))


def _rows_phrase(name, stats):
    counts = stats.get("row_counts") or {}
    if name not in counts:
        return ""           # unmeasured: say nothing rather than guess a count
    n = counts[name]
    if not n:
        return "(EMPTY - no rows yet) "
    return "(%%d row%%s) " %% (n, "" if n == 1 else "s")


def _json_block(ref, profile):
    col = ref.split(".", 1)[1]
    out = ["  %%s is JSON, not text.%%s Keys:"
           %% (col, " A LIST of objects." if profile.get("is_list") else "")]
    for key, meta in sorted((profile.get("keys") or {}).items()):
        vals = meta.get("values") or ()
        line = "    %%s" %% key
        if vals:
            line += " - values: %%s" %% ", ".join("'%%s'" %% v for v in vals)
        elif meta.get("example") not in (None, ""):
            line += " - e.g. '%%s'" %% meta["example"]
        out.append(line)
    return out


def _volatile_lines(name, stats):
    out = []
    prefix = name + "."
    for ref, profile in sorted((stats.get("json_keys") or {}).items()):
        if ref.startswith(prefix) and ref.count(".") == prefix.count("."):
            out.extend(_json_block(ref, profile))
    for ref, vals in sorted((stats.get("enums") or {}).items()):
        if ref.startswith(prefix):
            out.append("  %%s values: %%s"
                       %% (ref[len(prefix):], ", ".join("'%%s'" %% v for v in vals)))
    for ref, pair in sorted((stats.get("constants") or {}).items()):
        if not ref.startswith(prefix):
            continue
        val, n = pair
        out.append(
            "  %%s IS '%%s' ON ALL %%d ROWS - it never varies, so it cannot tell "
            "anything apart. Filtering on it matches everything and reading it "
            "as an outcome is wrong; find the column that does vary."
            %% (ref[len(prefix):], val, n))
    empty = sorted(c[len(prefix):] for c in (stats.get("empty_columns") or {})
                   if c.startswith(prefix))
    if empty:
        out.append(
            "  NEVER RECORDED - these columns are NULL on all %%d rows, so a "
            "filter or a COUNT on them returns nothing and that is not an "
            "answer about the lab: %%s. Whatever you were looking for is kept "
            "somewhere else; find that column instead of concluding there is "
            "none." %% (list((stats.get("row_counts") or {}).values()) and
                        (stats.get("row_counts") or {}).get(name, 0),
                        ", ".join(empty)))
    return out


def _table_text(name, stats=None):
    """One table's section: the committed skeleton plus today's measurements."""
    skeleton = _TABLE_TEXT.get(name)
    if skeleton is None:
        return None
    stats = _stats() if stats is None else stats
    body = skeleton.replace("{ROWS}", _rows_phrase(name, stats))
    extra = _volatile_lines(name, stats)
    # chr(10), not a backslash escape: this line lives inside the generator's
    # template string, where one level of escaping has already been spent.
    return body + (chr(10) + chr(10).join(extra) if extra else '')


def catalog_prompt_text(domain=None):
    """The catalog section for one worker, or the whole schema when domain is None."""
    names = DOMAIN_TABLES.get(domain) if domain else tuple(_TABLE_TEXT)
    if not names:
        names = tuple(_TABLE_TEXT)
    st = _stats()
    body = (chr(10) + chr(10)).join(_table_text(n, st) for n in names if n in _TABLE_TEXT)
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
                + (chr(10) + chr(10)).join(_table_text(n) for n in core if n in _TABLE_TEXT))
    if rest:
        lines = []
        for n in rest:
            head = (_table_text(n) or "").split(chr(10), 1)[0]
            lines.append(head[4:] if head.startswith("### ") else n)
        out += ("\\n\\n## Also yours - call describe_table(name) for its columns\\n"
                + "\\n".join("  " + ln for ln in lines))
    return out


def table_detail(name):
    """Full catalog entry for one table: columns, notes, enum values."""
    return _table_text(name)


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
       "json_keys": {"%s.%s" % (t["name"], c): p
                     for t in tables for c, p in (t.get("json") or {}).items()},
       "families": dict(families or {}),
       "row_counts": {t["name"]: int(t["rows"] or 0) for t in tables},
       "class_columns": tuple(sorted(
           ["%s.%s" % (t["name"], c) for t in tables for c in t["enums"]]
           + ["%s.%s" % (t["name"], c) for t in tables
              for c in (t.get("constants") or {})])),
       "json_columns": tuple(sorted(
           "%s.%s" % (t["name"], c) for t in tables for c in (t.get("json") or {}))),
       "constants": {"%s.%s" % (t["name"], c): v
                     for t in tables
                     for c, v in (t.get("constants") or {}).items()},
       "domain_terms": build_domain_terms(tables)[0],
       "term_sources": build_domain_terms(tables)[1],
       "term_min_weight": TERM_MIN_WEIGHT,
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
        families = read_reason_families(conn)
    finally:
        conn.close()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_catalog.py")
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_module_text(tables, families))

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
