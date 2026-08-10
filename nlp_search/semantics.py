# -*- coding: utf-8 -*-
"""The semantic layer: what fuzzy words mean, decided by a human, not a model.

EDIT THIS FILE FREELY. It is meant to be changed - it is the one place where
the lab's own vocabulary is written down, and nobody but the lab can say what
"overdue" or "pending" ought to mean here. Adding a metric is four lines. The
rules are only:

  * every metric carries REAL SQL that runs against this database, and
  * `python -m nlp_search.semantics` must still pass afterwards.

WHY IT EXISTS
-------------
Asked "how many tests are overdue?", the assistant invented a definition -
in_progress or report_uploaded with an end date in the past - and answered
"16" as a plain fact. The arithmetic was correct and the meaning was fiction,
which is the most dangerous shape a wrong answer can take: nothing about it
looks wrong.

The model cannot be asked to notice this itself. Measured on ambiguous
database questions with the full schema in front of it, GPT-4o recognises the
ambiguity about a quarter of the time. So the list is written by hand.

Three kinds of entry:

  METRICS    a named measure with the SQL that computes it. One meaning, one
             number, reviewable by someone who knows the lab.
  AMBIGUOUS  a word that maps to MORE THAN ONE metric. The assistant reports
             all of them, or asks - it may not pick one silently.
  UNDEFINED  a word with no meaning in this database at all. The assistant
             says so and offers what does exist.

Anything not mentioned here is untouched: "how many datasheets does Krishna
have" has no fuzzy word in it and goes straight down the normal SQL path. This
guards a narrow class of question, it does not gate the system.
"""
import re

# --------------------------------------------------------------------------
# LAB RULES - decided by the lab, not by this code
# --------------------------------------------------------------------------
# Four questions the data cannot answer on its own. Each was previously being
# decided implicitly, differently, on every question, which is how the same
# query returned different numbers on different runs. Change these when the
# lab changes its mind; everything below follows from them.
#
#  1. CALIBRATION AUTHORITY -> the due DATE.
#     calibration_due_date < today is the truth. calibration_status_col
#     disagrees badly with it - the large majority of overdue items are still
#     marked 'In Calibration' - so the column is treated as stale and reported
#     only as a data-quality problem, never as an answer.
#
#     NO COUNTS ARE WRITTEN HERE ON PURPOSE. The first version of this comment
#     said "65 items are past due while marked In Calibration". 65 is how many
#     rows carry that status in total; the overdue-and-mismarked figure was 39,
#     and the model repeated the wrong number to a user because it was in the
#     prompt. A hardcoded count is wrong the day the data moves, and this one
#     was wrong the day it was written. Metrics carry SQL; prose carries rules.
#
#  2. CANCELLED WORK STILL COUNTS as outstanding.
#     A test that was requested and has no datasheet is outstanding even if
#     its planner entry was cancelled. Nothing disappears from a completeness
#     figure. Cancelled is named separately so the reader can discount it.
#
#  3. A DRAFT DATASHEET IS "STARTED, NOT FINISHED" - a third state.
#     Not "filled in" and not "missing". Answers about progress must
#     distinguish none / started / approved, because calling a Draft "filled
#     in" told an engineer their unsubmitted sheet was done.
#
#  4. NO INVENTED DEFINITIONS. A judgement word with no entry below has no
#     meaning here, and the honest answer says so.
LAB_RULES = """Rules this lab has decided, which the data cannot tell you:
- Calibration status comes from calibration_due_date, NOT from
  calibration_status_col. That column is stale and contradicts the dates; if
  it is relevant, report it as a data-quality problem, never as the answer.
- A cancelled test still counts as outstanding work on a job. Say how many are
  cancelled, but do not remove them from the total.
- A Draft datasheet is STARTED BUT NOT FINISHED - a third state between "no
  datasheet" and "approved". Never describe a Draft as filled in or done."""


# --------------------------------------------------------------------------
# Test codes: the same test is spelled three different ways
# --------------------------------------------------------------------------
# The request, the schedule and the datasheet each use their own vocabulary,
# and a naive join on test_code silently drops four of the eleven test types
# (28 rows). Every question of the shape "what was requested vs what was
# actually done" is wrong without this bridge.
#
#   request table   planner_entries   datasheet        canonical
#   FLICKER         VoltageFlicker    VOLTAGEFLICKER   VOLTAGEFLICKER
#   POWER_FREQ      PFMF              PFMF             PFMF
#   VOLTAGE_DIPS    VoltageDips       VOLTAGEDIPS      VOLTAGEDIPS
#   RS              RS_RI             RS_RI            RS_RI
#
# RS_INTERIM appears on requests only and has no datasheet counterpart.
TEST_CODE_CANON = {
    "CE": "CE", "CRF": "CRF", "EFT": "EFT", "ESD": "ESD", "RE": "RE",
    "SURGE": "SURGE", "HARMONIC": "HARMONIC",
    "FLICKER": "VOLTAGEFLICKER", "VOLTAGEFLICKER": "VOLTAGEFLICKER",
    "POWER_FREQ": "PFMF", "PFMF": "PFMF",
    "VOLTAGE_DIPS": "VOLTAGEDIPS", "VOLTAGEDIPS": "VOLTAGEDIPS",
    "RS": "RS_RI", "RS_RI": "RS_RI",
    "RS_INTERIM": "RS_INTERIM",
}


def canon_sql(column):
    """A SQL expression that normalises a test-code column, for use in joins."""
    whens = " ".join(
        "WHEN '%s' THEN '%s'" % (k, v) for k, v in sorted(TEST_CODE_CANON.items()))
    return "CASE UPPER(REPLACE(%s,' ','_')) %s ELSE UPPER(%s) END" % (
        column, whens, column)


_CANON_L = canon_sql("t.test_code")
_CANON_R = canon_sql("d.test_code")


# --------------------------------------------------------------------------
# METRICS - one name, one meaning, one query
# --------------------------------------------------------------------------
# `value_sql` must return a single number. `rows_sql` (optional) returns the
# rows behind it, so the assistant can show the list as well as the count.
# The counts in the comments are what these returned on 2026-08-07 - they will
# drift, and validate() only checks the SQL still runs, not the number.

METRICS = {
    # -- equipment ---------------------------------------------------------
    "calibration_overdue": {
        "label": "past its calibration due date",
        "domain": "equipment",
        "value_sql": "SELECT COUNT(*) FROM equipment WHERE calibration_due_date < CURDATE()",
        "rows_sql": ("SELECT name, make, model_no, serial_no, calibration_due_date "
                     "FROM equipment WHERE calibration_due_date < CURDATE() "
                     "ORDER BY calibration_due_date"),
        "authoritative": True,
        "caveat": ("The lab treats the due date as authoritative. Equipment with "
                   "NO calibration date is not counted here - profile_column on "
                   "calibration_due_date for how many that is. Mention as a DATA "
                   "QUALITY problem, never as an alternative answer, that most of "
                   "these are still marked 'In Calibration' in "
                   "calibration_status_col; query for the exact figure rather than "
                   "quoting one."),
    },
    "calibration_flagged": {
        "label": "flagged as requiring calibration",
        "domain": "equipment",
        "value_sql": "SELECT COUNT(*) FROM equipment WHERE calibration_required = 'Yes'",
        "rows_sql": ("SELECT name, make, model_no, calibration_due_date FROM equipment "
                     "WHERE calibration_required = 'Yes' ORDER BY name"),
    },
    "calibration_marked_out": {
        "label": "marked 'Out of Calibration'",
        "domain": "equipment",
        "value_sql": ("SELECT COUNT(*) FROM equipment "
                      "WHERE calibration_status_col = 'Out of Calibration'"),
        "rows_sql": ("SELECT name, make, model_no, calibration_due_date FROM equipment "
                     "WHERE calibration_status_col = 'Out of Calibration'"),
        "caveat": ("The manually-set flag. NOT authoritative - the lab uses the due "
                   "date. Quote this only to point out that the flag is out of step "
                   "with the dates."),
    },
    "calibration_due_soon": {
        "label": "due for calibration in the next 30 days",
        "domain": "equipment",
        "value_sql": ("SELECT COUNT(*) FROM equipment WHERE calibration_due_date "
                      "BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)"),
    },
    "maintenance_overdue": {
        "label": "past its maintenance due date",
        "domain": "equipment",
        "value_sql": "SELECT COUNT(*) FROM equipment WHERE maintenance_due_date < CURDATE()",
        "rows_sql": ("SELECT name, make, model_no, maintenance_due_date FROM equipment "
                     "WHERE maintenance_due_date < CURDATE() ORDER BY maintenance_due_date"),
        "caveat": "24 items have no maintenance date at all and are not counted.",
    },
    "maintenance_flagged": {
        "label": "flagged as requiring maintenance",
        "domain": "equipment",
        "value_sql": "SELECT COUNT(*) FROM equipment WHERE maintenance_required = 'Yes'",
        "rows_sql": ("SELECT name, make, model_no, maintenance_due_date FROM equipment "
                     "WHERE maintenance_required = 'Yes' ORDER BY name"),
        "caveat": ("This is the standing flag - it means the item is on a "
                   "maintenance schedule at all, not that anything is due now."),
    },

    # -- schedule ----------------------------------------------------------
    "test_in_progress": {
        "label": "scheduled tests currently in progress",
        "domain": "schedule",
        "value_sql": "SELECT COUNT(*) FROM planner_entries WHERE status = 'in_progress'",
        "rows_sql": ("SELECT p.test_name, p.tco_id, p.test_person_name, p.start_date, "
                     "p.end_date FROM planner_entries p WHERE p.status = 'in_progress' "
                     "ORDER BY p.end_date"),
    },
    "test_past_end_date": {
        "label": "scheduled tests whose planned end date has passed and are not finished",
        "domain": "schedule",
        "value_sql": ("SELECT COUNT(*) FROM planner_entries WHERE end_date < CURDATE() "
                      "AND status NOT IN ('report_uploaded','cancelled')"),
        "rows_sql": ("SELECT test_name, tco_id, test_person_name, end_date, status "
                     "FROM planner_entries WHERE end_date < CURDATE() "
                     "AND status NOT IN ('report_uploaded','cancelled') ORDER BY end_date"),
        "caveat": ("'Late' is inferred from the planned end date; the lab does not "
                   "record a due date or an SLA, so this is a reading, not a rule."),
    },
    "test_no_datasheet": {
        "label": "scheduled tests with no datasheet filled in",
        "domain": "schedule",
        "value_sql": ("SELECT COUNT(*) FROM planner_entries p "
                      "LEFT JOIN `datasheet` d ON d.planner_entry_id = p.id "
                      "WHERE d.id IS NULL AND p.status <> 'cancelled'"),
        "rows_sql": ("SELECT p.test_name, p.tco_id, p.test_person_name, p.status "
                     "FROM planner_entries p LEFT JOIN `datasheet` d "
                     "ON d.planner_entry_id = p.id "
                     "WHERE d.id IS NULL AND p.status <> 'cancelled'"),
    },
    "engineer_current_load": {
        "label": "how many tests each engineer has in progress",
        "domain": "schedule",
        "value_sql": ("SELECT COUNT(DISTINCT engineer_user_id) FROM planner_entries "
                      "WHERE status = 'in_progress'"),
        "rows_sql": ("SELECT COALESCE(u.username, p.test_person_name) AS engineer, "
                     "COUNT(*) AS tests_in_progress FROM planner_entries p "
                     "LEFT JOIN users u ON u.id = p.engineer_user_id "
                     "WHERE p.status = 'in_progress' GROUP BY engineer "
                     "ORDER BY tests_in_progress DESC"),
        "caveat": ("Load is counted as tests in progress. The lab records no hours, "
                   "capacity or leave, so this says nothing about who is actually free."),
    },

    # -- datasheets --------------------------------------------------------
    "datasheet_draft": {
        "label": "datasheets still in Draft (not yet approved)",
        "domain": "datasheets",
        "value_sql": "SELECT COUNT(*) FROM `datasheet` WHERE status = 'Draft'",
        "rows_sql": ("SELECT test_code, job_number, engineer_name, result "
                     "FROM `datasheet` WHERE status = 'Draft'"),
    },
    "datasheet_no_result": {
        "label": "datasheets with no result recorded",
        "domain": "datasheets",
        "value_sql": "SELECT COUNT(*) FROM `datasheet` WHERE result IS NULL OR result = ''",
        "rows_sql": ("SELECT test_code, job_number, engineer_name, status "
                     "FROM `datasheet` WHERE result IS NULL OR result = ''"),
    },
    "job_completeness": {
        "label": "per job: tests requested vs datasheets actually recorded",
        "domain": "datasheets",
        # Written by hand because generated SQL got it wrong in three ways at
        # once, confidently: it grouped by job_number and so dropped the four
        # jobs that have none (the most behind of all), it joined test codes
        # raw and lost the four that are spelled differently per table, and it
        # reported 6 recorded for a job with 10. Every number it printed
        # appeared somewhere in the evidence, so the grounding check passed it.
        # This is the shape of question where a wrong answer does damage - the
        # reader cannot eyeball it - so the join is reviewed once and reused.
        "value_sql": ("SELECT COUNT(*) FROM iec_emc_requests r "
                      "WHERE EXISTS (SELECT 1 FROM iec_emc_request_tests t "
                      "WHERE t.request_id = r.id AND t.is_selected = 1)"),
        "rows_are_the_answer": True,
        "rows_sql": (
            "SELECT r.tco_id, COALESCE(NULLIF(r.job_number,''),'(no job number)') "
            "AS job_number, r.product_name, "
            "COUNT(DISTINCT t.id) AS requested, "
            "COUNT(DISTINCT CASE WHEN d.status='Approved' THEN d.id END) AS approved, "
            "COUNT(DISTINCT CASE WHEN d.status='Draft' THEN d.id END) AS started_not_finished, "
            "COUNT(DISTINCT t.id) - COUNT(DISTINCT d.id) AS not_started "
            "FROM iec_emc_requests r "
            "JOIN iec_emc_request_tests t ON t.request_id = r.id AND t.is_selected = 1 "
            "LEFT JOIN `datasheet` d ON d.test_request_id = r.id AND %s = %s "
            "GROUP BY r.tco_id, r.job_number, r.product_name "
            "ORDER BY not_started DESC, r.tco_id" % (_CANON_R, _CANON_L)),
        "caveat": ("Three states, not two: approved / started-not-finished (Draft) / "
                   "not started. Four requests have no job number yet and are listed "
                   "by TCO id - those are the ones furthest behind, not missing rows. "
                   "Cancelled tests are still counted as outstanding per lab rule. "
                   "Test codes are normalised across tables before matching."),
    },
    "test_never_scheduled": {
        "label": "requested but never scheduled in the planner",
        "domain": "requests",
        "value_sql": ("SELECT COUNT(*) FROM iec_emc_request_tests t "
                      "LEFT JOIN planner_entries p ON p.test_request_id = t.request_id "
                      "AND %s = %s WHERE t.is_selected = 1 AND p.id IS NULL"
                      % (canon_sql("t.test_code"), canon_sql("p.test_name"))),
        "rows_are_the_answer": True,
        "rows_sql": ("SELECT r.tco_id, COALESCE(NULLIF(r.job_number,''),'(no job number)') "
                     "AS job_number, t.test_code "
                     "FROM iec_emc_request_tests t "
                     "JOIN iec_emc_requests r ON r.id = t.request_id "
                     "LEFT JOIN planner_entries p ON p.test_request_id = t.request_id "
                     "AND %s = %s "
                     "WHERE t.is_selected = 1 AND p.id IS NULL "
                     "ORDER BY r.tco_id, t.test_code"
                     % (canon_sql("t.test_code"), canon_sql("p.test_name"))),
        "caveat": ("Matched per TEST, not per job, and with test codes normalised - "
                   "the planner stores them in test_name with its own spelling. "
                   "A per-job join gives 45 and an unnormalised one gives 67; "
                   "both are wrong."),
    },
    "test_unfilled": {
        "label": "tests requested on a job but with no datasheet recorded",
        "domain": "datasheets",
        "value_sql": ("SELECT COUNT(*) FROM iec_emc_request_tests t "
                      "LEFT JOIN `datasheet` d ON d.test_request_id = t.request_id "
                      "AND %s = %s WHERE t.is_selected = 1 AND d.id IS NULL" % (_CANON_R, _CANON_L)),
        "rows_sql": ("SELECT r.tco_id, r.job_number, t.test_code, t.workflow_status "
                     "FROM iec_emc_request_tests t "
                     "JOIN iec_emc_requests r ON r.id = t.request_id "
                     "LEFT JOIN `datasheet` d ON d.test_request_id = t.request_id "
                     "AND %s = %s WHERE t.is_selected = 1 AND d.id IS NULL "
                     "ORDER BY r.tco_id, t.test_code" % (_CANON_R, _CANON_L)),
        "caveat": "Test codes are spelled differently per table; this join normalises them.",
    },

    # -- requests ----------------------------------------------------------
    "request_rejected": {
        "label": "requests that were rejected",
        "domain": "requests",
        "value_sql": "SELECT COUNT(*) FROM iec_emc_requests WHERE rejected_at IS NOT NULL",
    },
    "request_no_job_number": {
        "label": "requests with no job number assigned yet",
        "domain": "requests",
        "value_sql": ("SELECT COUNT(*) FROM iec_emc_requests "
                      "WHERE job_number IS NULL OR job_number = ''"),
    },
}


# --------------------------------------------------------------------------
# RELATIONSHIPS - how the domains actually join
# --------------------------------------------------------------------------
# This is the general fix for multi-table questions, and it is worth being
# precise about why it is needed.
#
# Measured on the complex suite: every question needing one or two tables
# passed; two of the three needing a third table failed. The reason is not
# that the model writes bad SQL - it is that the three joins that cross a
# domain boundary here are NOT DECLARED FOREIGN KEYS, so there is nothing in
# the schema to read them off:
#
#   planner_entries.test_request_id -> iec_emc_requests.id   (no FK)
#   datasheet.test_request_id       -> iec_emc_requests.id   (no FK)
#   datasheet_equipment.equipment_name -> equipment.name     (no FK, by TEXT)
#
# The model has to guess, and a guessed join either errors (visible, fine) or
# silently returns the wrong rows (invisible, the dangerous one). The last of
# those is worse than it looks: equipment.name is not unique, so the obvious
# join turns 50 datasheet_equipment rows into 62 and quietly inflates any
# count built on it.
#
# So the paths are written down once, by hand, and validate() runs every one
# of them against the live database. A join path that stops working is then a
# failing check rather than a wrong answer six months later.
#
# `sql` must be a complete runnable statement so validate() can execute it.

RELATIONSHIPS = [
    {
        "path": "a job -> the tests asked for on it",
        "tables": ['iec_emc_request_tests', 'iec_emc_requests'],
        "join": "iec_emc_request_tests t JOIN iec_emc_requests r ON t.request_id = r.id",
        "note": "add t.is_selected = 1; unselected rows are menu items, not work.",
        "sql": ("SELECT COUNT(*) FROM iec_emc_request_tests t "
                "JOIN iec_emc_requests r ON t.request_id = r.id "
                "WHERE t.is_selected = 1"),
    },
    {
        "path": "a job -> its scheduled work (NO FOREIGN KEY - use this column)",
        "tables": ['planner_entries', 'iec_emc_requests'],
        "join": "planner_entries p JOIN iec_emc_requests r ON p.test_request_id = r.id",
        "note": ("p.tco_id = r.tco_id gives the same rows and is the fallback. "
                 "planner status is in_progress / report_uploaded / cancelled."),
        "sql": ("SELECT COUNT(*) FROM planner_entries p "
                "JOIN iec_emc_requests r ON p.test_request_id = r.id"),
    },
    {
        "path": "a job -> its datasheets (NO FOREIGN KEY - use this column)",
        "tables": ['datasheet', 'iec_emc_requests'],
        "join": "`datasheet` d JOIN iec_emc_requests r ON d.test_request_id = r.id",
        "note": "datasheet status is Approved or Draft only. Draft = started, not finished.",
        "sql": ("SELECT COUNT(*) FROM `datasheet` d "
                "JOIN iec_emc_requests r ON d.test_request_id = r.id"),
    },
    {
        "path": "a REQUESTED TEST -> its planner entry (per test, not per job)",
        "tables": ["iec_emc_request_tests", "planner_entries"],
        "join": ("iec_emc_request_tests t LEFT JOIN planner_entries p "
                 "ON p.test_request_id = t.request_id AND %s = %s"
                 % (canon_sql("t.test_code"), canon_sql("p.test_name"))),
        "note": ("TWO traps here, and getting either wrong changes the answer. "
                 "(1) The planner's column is test_name, NOT test_code - same "
                 "vocabulary, different name. (2) Join at the TEST level. "
                 "Joining on test_request_id alone means one scheduled test "
                 "makes every other test on that job look scheduled. Measured "
                 "on 'tests requested but never scheduled': 63 correct, 67 if "
                 "you skip the canon, 45 if you join per-job."),
        "sql": ("SELECT COUNT(*) FROM iec_emc_request_tests t "
                "JOIN planner_entries p ON p.test_request_id = t.request_id "
                "AND %s = %s WHERE t.is_selected = 1"
                % (canon_sql("t.test_code"), canon_sql("p.test_name"))),
    },
    {
        "path": "scheduled work -> the datasheet produced for it",
        "tables": ['datasheet', 'planner_entries'],
        "join": "`datasheet` d JOIN planner_entries p ON d.planner_entry_id = p.id",
        "note": ("This is the only link between WHO WAS SCHEDULED and WHAT WAS "
                 "MEASURED. A planner entry with no datasheet row is work not "
                 "yet recorded - LEFT JOIN and test d.id IS NULL to find it."),
        "sql": ("SELECT COUNT(*) FROM `datasheet` d "
                "JOIN planner_entries p ON d.planner_entry_id = p.id"),
    },
    {
        "path": "a datasheet -> the equipment used on it (TEXT MATCH, NOT AN ID)",
        "tables": ['datasheet_equipment', 'equipment'],
        "join": "datasheet_equipment de JOIN equipment e ON de.equipment_name = e.name",
        "note": ("DANGEROUS. equipment.name is NOT unique - this join turns 50 "
                 "rows into 62 and inflates every count built on it. If you only "
                 "need the equipment named on datasheets, DO NOT JOIN AT ALL: "
                 "datasheet_equipment already carries equipment_name, make, "
                 "model_no, serial_no and calibration_due. Join to equipment only "
                 "for a column it does not have, and then COUNT(DISTINCT de.id)."),
        "sql": ("SELECT COUNT(DISTINCT de.id) FROM datasheet_equipment de "
                "JOIN equipment e ON de.equipment_name = e.name"),
    },
    {
        "path": "requested test -> measured test (CODES DIFFER - normalise both)",
        "tables": ['datasheet', 'iec_emc_request_tests'],
        "join": ("`datasheet` d JOIN iec_emc_request_tests t "
                 "ON t.request_id = d.test_request_id AND %s = %s" % (_CANON_L, _CANON_R)),
        "note": ("A plain t.test_code = d.test_code silently drops four of the "
                 "eleven test types. Always wrap both sides in the CASE above."),
        "sql": ("SELECT COUNT(*) FROM `datasheet` d "
                "JOIN iec_emc_request_tests t ON t.request_id = d.test_request_id "
                "AND %s = %s" % (_CANON_L, _CANON_R)),
    },
    {
        "path": "anything -> a person's NAME",
        "tables": ['users'],
        "join": "JOIN users u ON u.id = <the *_user_id or *_by column>",
        "note": ("Engineers are planner_entries.engineer_user_id, reviewers "
                 "peer_reviewer_user_id, requesters iec_emc_requests.user_id, "
                 "and iec_emc_request_tests.assigned_engineer_id. NEVER show the "
                 "id - always join and give the name."),
        "sql": ("SELECT COUNT(*) FROM planner_entries p "
                "JOIN users u ON u.id = p.engineer_user_id"),
    },
]


def relationship_block(available=None):
    """The join paths, for the prompt. Short enough to carry on every call.

    ``available`` is a worker's allowlist - a path whose tables it cannot
    reach is noise, and worse, an invitation to write SQL that sql_guard will
    reject.
    """
    have = set(available) if available else None
    usable = [r for r in RELATIONSHIPS
              if have is None or set(r.get("tables", ())) <= have]
    if not usable:
        return ""
    lines = ["## How the tables join (verified against this database)",
             "Use these exact paths. Some are not foreign keys, so they cannot be",
             "read off the schema, and a guessed join returns wrong rows without",
             "erroring.", ""]
    for rel in usable:
        lines.append("- %s" % rel["path"])
        lines.append("    %s" % rel["join"])
        if rel.get("note"):
            lines.append("    %s" % rel["note"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# AMBIGUOUS - a word that means more than one of the above
# --------------------------------------------------------------------------
# Report every candidate, or ask. Never pick one silently: the whole failure
# being fixed here is a single number presented as though it were the only
# possible reading.

AMBIGUOUS = {
    "never scheduled": ["test_never_scheduled"],
    "never got scheduled": ["test_never_scheduled"],
    "not scheduled": ["test_never_scheduled"],
    "never been scheduled": ["test_never_scheduled"],
    # One meaning now - the lab decided the due date is authoritative.
    "needs calibration": ["calibration_overdue"],
    "need calibration": ["calibration_overdue"],
    "requires calibration": ["calibration_overdue"],
    "out of calibration": ["calibration_overdue"],
    "past calibration": ["calibration_overdue"],
    "due for calibration": ["calibration_due_soon", "calibration_overdue"],

    "needs maintenance": ["maintenance_overdue", "maintenance_flagged"],
    "need maintenance": ["maintenance_overdue", "maintenance_flagged"],
    "requires maintenance": ["maintenance_overdue", "maintenance_flagged"],
    "require maintenance": ["maintenance_overdue", "maintenance_flagged"],
    "due for maintenance": ["maintenance_overdue"],

    "overdue": ["calibration_overdue", "maintenance_overdue", "test_past_end_date"],
    "late": ["test_past_end_date", "calibration_overdue"],
    "behind": ["test_past_end_date", "test_no_datasheet"],
    "delayed": ["test_past_end_date"],

    "pending": ["test_in_progress", "datasheet_draft", "test_no_datasheet"],
    "outstanding": ["test_in_progress", "test_no_datasheet", "datasheet_draft"],
    "not done": ["test_no_datasheet", "test_unfilled"],
    "incomplete": ["datasheet_draft", "datasheet_no_result", "test_unfilled"],
    "unfilled": ["test_unfilled", "test_no_datasheet"],
    "missing": ["test_unfilled", "datasheet_no_result"],
    "still to do": ["test_unfilled", "test_in_progress"],
    "remaining": ["test_unfilled", "test_in_progress"],

    "are behind": ["job_completeness"],
    "how complete": ["job_completeness"],
    "completeness": ["job_completeness"],
    "requested vs": ["job_completeness"],
    "jobs are behind": ["job_completeness"],

    "busy": ["engineer_current_load"],
    "workload": ["engineer_current_load"],
    "most work": ["engineer_current_load"],
    "in progress": ["test_in_progress"],
    "ongoing": ["test_in_progress"],
    "active": ["test_in_progress"],
}


# --------------------------------------------------------------------------
# UNDEFINED - no meaning in this database, and saying so is the right answer
# --------------------------------------------------------------------------
# Each entry names what DOES exist, so the reply is useful rather than a wall.

UNDEFINED = {
    "backlog": "there is no backlog measure; I can give tests in progress, or "
               "tests with no datasheet yet",
    "at risk": "nothing marks a job as at risk; I can give tests past their "
               "planned end date",
    "utilisation": "the lab records no hours or capacity, so utilisation cannot "
                   "be computed; I can give tests in progress per engineer",
    "utilization": "the lab records no hours or capacity, so utilisation cannot "
                   "be computed; I can give tests in progress per engineer",
    "efficiency": "nothing measures efficiency; there are no durations or targets "
                  "recorded",
    "productivity": "nothing measures productivity; I can count tests or "
                    "datasheets per engineer",
    "free": "availability is not recorded - no leave, capacity or booking data. "
            "I can tell you who has fewest tests in progress",
    "available": "engineer availability is not recorded; for equipment I can give "
                 "calibration status",
    "cost": "no cost or price is stored anywhere in this database",
    "revenue": "no financial data is stored in this database",
    "priority": "requests carry an assignment_priority field but it is empty on "
                "every row",
    "sla": "no SLA, target date or turnaround time is recorded",
    "turnaround": "no SLA or turnaround target is recorded; only planned start "
                  "and end dates",
    "certificate": "the lab does not record certificates; it records datasheets "
                   "and generated reports",
    "customer satisfaction": "not recorded anywhere in this database",
}


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------

def _phrases_in(question, keys):
    """Longest phrases first, so 'needs calibration' wins over 'calibration'.

    Word-by-word and typo-tolerant. Users write "callibration", "equipmnt",
    "pendign" - and an exact phrase match silently skips the whole layer for
    them, which is worse than not having it: the question then takes the raw
    SQL path with none of the guards, and the typo is invisible in the logs.
    """
    q = " %s " % re.sub(r"[^a-z0-9 ]+", " ", (question or "").lower())
    q = re.sub(r"\s+", " ", q)
    qwords = q.split()
    hits = []
    for key in sorted(keys, key=len, reverse=True):
        if any(key in h for h in hits):
            continue
        if (" %s " % key) in q:
            hits.append(key)
            continue
        # every word of the key must appear, exactly or as a near-miss
        kwords = key.split()
        if len(kwords) > len(qwords):
            continue
        if all(any(_close(kw, qw) for qw in qwords) for kw in kwords):
            hits.append(key)
    return hits


def _close(a, b):
    """Same word, allowing a typo. Short words must match exactly - 'due' and
    'dye' are not the same thing."""
    if a == b:
        return True
    if len(a) < 5 or abs(len(a) - len(b)) > 2:
        return False
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def resolve(question):
    """What this question's fuzzy words mean.

    {"ambiguous": [{term, metrics:[{name,label,...}]}],
     "undefined": [{term, note}]}
    Empty dict when the question uses no judgement word at all - most do not,
    and those skip this layer entirely.
    """
    out = {}
    amb, seen_sets = [], []
    for term in _phrases_in(question, AMBIGUOUS):
        names = tuple(AMBIGUOUS[term])
        # "need calibration" and "needs calibration" both hit and mean the same
        # thing; report the reading once, under the longest phrase that matched.
        if names in seen_sets:
            continue
        seen_sets.append(names)
        amb.append({"term": term,
                    "metrics": [dict(METRICS[m], name=m) for m in names
                                if m in METRICS]})
    if amb:
        out["ambiguous"] = amb
    und = [{"term": t, "note": UNDEFINED[t]} for t in _phrases_in(question, UNDEFINED)]
    if und:
        out["undefined"] = und
    return out


_ROWS_LIMIT = 60


def execute(resolved, db_params, ledger=None):
    """Run every candidate metric NOW and record the answers in the ledger.

    Handing the model the SQL and trusting it to run it does not work - it
    reads the definition, decides it knows the answer, and writes a number
    nothing executed. The ledger is then empty and the grounding check throws
    the whole answer away. Observed on "how many reports are pending": the
    right SQL was in the prompt and no query was ever issued.

    So the layer runs its own SQL. The number reaches the model already
    computed and already in the ledger, which means it is grounded before the
    model has said anything, and the model's only remaining job is to phrase
    it. That is the answer-contract idea applied to the cases where a human
    already wrote the query.
    """
    if not resolved.get("ambiguous"):
        return resolved
    conn = None
    try:
        conn = _connect_ro(db_params)
        with conn.cursor() as cur:
            for item in resolved["ambiguous"]:
                for mtc in item["metrics"]:
                    try:
                        cur.execute(mtc["value_sql"])
                        mtc["value"] = cur.fetchall()[0][0]
                        if ledger is not None:
                            ledger.record("semantics", mtc["value_sql"],
                                          columns=[mtc["name"]],
                                          rows=[[mtc["value"]]])
                    except Exception as exc:  # noqa: BLE001
                        mtc["error"] = str(exc)[:120]

                    # For some questions the COUNT is not the answer - "which
                    # jobs are behind" wants the jobs. Running only value_sql
                    # handed the model the number 9 and no list, so it went off
                    # and wrote its own query for the rows, which is the exact
                    # thing the reviewed SQL exists to prevent. Fetch the rows
                    # here too, and they arrive pre-grounded.
                    if not mtc.get("rows_are_the_answer") or not mtc.get("rows_sql"):
                        continue
                    try:
                        sql = mtc["rows_sql"]
                        if " limit " not in sql.lower():
                            sql += " LIMIT %d" % _ROWS_LIMIT
                        cur.execute(sql)
                        cols = [d[0] for d in (cur.description or [])]
                        rows = [list(r) for r in cur.fetchall()]
                        mtc["rows"] = {"columns": cols, "rows": rows}
                        if ledger is not None:
                            ledger.record("semantics", sql, columns=cols, rows=rows)
                    except Exception as exc:  # noqa: BLE001
                        mtc["rows_error"] = str(exc)[:120]
        conn.rollback()
    except Exception:  # noqa: BLE001 - a failed metric must not cost the answer
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return resolved


def _connect_ro(db_params):
    import pymysql
    conn = pymysql.connect(
        host=db_params["host"], port=int(db_params.get("port") or 3306),
        user=db_params["user"], password=db_params["password"],
        database=db_params["database"], charset="utf8mb4",
        connect_timeout=5, read_timeout=10, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("SET SESSION MAX_EXECUTION_TIME=5000")
    return conn


def prompt_block(resolved):
    """The instruction injected for one question, or "" when nothing matched."""
    if not resolved:
        return ""
    lines = ["", "## DEFINED TERMS - reference for part of this question", "",
             "These figures are pre-computed and correct. They cover only the "
             "phrase named against each one. ANSWER THE QUESTION THAT WAS "
             "ASKED - if these do not address it, or address only part of it, "
             "query for the rest as normal and use these where they fit. Do "
             "not substitute a figure below for the answer to a different "
             "question.", ""]
    for item in resolved.get("ambiguous", []):
        computed = [m for m in item["metrics"] if "value" in m]
        if len(item["metrics"]) == 1:
            lines.append("In this lab '%s' means exactly one thing, decided by "
                         "the lab and already computed - quote it rather than "
                         "re-querying:" % item["term"])
        else:
            lines.append("The phrase '%s' has more than one meaning here. If the "
                         "question turns on it, give EVERY reading below rather "
                         "than picking one; the numbers are already run, so quote "
                         "them rather than re-querying:" % item["term"])
        for mtc in item["metrics"]:
            if "value" in mtc:
                lines.append("  - %s = %s" % (mtc["label"], mtc["value"]))
            elif mtc.get("error"):
                lines.append("  - %s = could not be computed (%s)"
                             % (mtc["label"], mtc["error"]))
            else:
                lines.append("  - %s   SQL: %s"
                             % (mtc["label"], " ".join(mtc["value_sql"].split())))
            if mtc.get("caveat"):
                lines.append("      you must also say: %s" % mtc["caveat"])
            if mtc.get("rows"):
                lines.extend(_render_rows(mtc["rows"]))
            elif mtc.get("rows_error"):
                lines.append("      the row list failed: %s" % mtc["rows_error"])
            elif mtc.get("rows_sql"):
                lines.append("      for the list behind it, run: %s"
                             % " ".join(mtc["rows_sql"].split())[:300])
        if len(computed) > 1:
            lines.append("Give ALL of these figures with the label that goes with "
                         "each, so the reader can see which reading is which. "
                         "Never collapse them into one number.")
        lines.append("")
    for item in resolved.get("undefined", []):
        lines.append("'%s' has NO definition in this database. Say so plainly, then "
                     "offer what does exist: %s" % (item["term"], item["note"]))
        lines.append("")
    return "\n".join(lines)


def metric_menu(domain=None):
    """The reviewed measures, as a menu the MODEL picks from.

    The phrase table below is a fast path, not the mechanism. It fires when a
    question happens to contain one of its 38 spellings, and it silently does
    nothing when it does not - "how many requested tests still have no
    datasheet" matches none of them, so the reviewed answer of 68 was never
    offered and the model improvised 80, which is the total requested.

    Enumerating more spellings does not fix that; there is always another way
    to say it. So the menu is handed over and the model decides which measure
    the question is asking for. Choosing between seventeen labelled options is
    what a language model is genuinely good at; writing a correct three-table
    join against a schema whose keys are not declared is what it is bad at.
    The human wrote the SQL, the model picks which one.
    """
    rows = []
    for name, m in sorted(METRICS.items()):
        if domain and m.get("domain") and m["domain"] != domain:
            continue
        rows.append("  %-24s %s" % (name, m["label"]))
    if not rows:
        return ""
    head = [
        "## Reviewed measures - call lab_metric(name) instead of deriving these",
        "Each is SQL a human wrote and checked. If the question asks for one of",
        "them, CALL IT - do not write your own version, and never report a",
        "different number for the same thing.",
    ]
    return "\n".join(head + rows)


def run_metric(name, db_params, ledger=None):
    """Execute one reviewed measure by name. Returns text for the model."""
    m = METRICS.get(name)
    if not m:
        near = [k for k in METRICS if name and name.lower() in k.lower()]
        return ("No measure called %r. Available: %s"
                % (name, ", ".join(sorted(near or METRICS))))
    item = {"term": name, "metrics": [dict(m, name=name)]}
    resolved = execute({"ambiguous": [item], "undefined": []}, db_params, ledger=ledger)
    mtc = resolved["ambiguous"][0]["metrics"][0]
    out = ["%s = %s" % (m["label"], mtc.get("value", "could not be computed"))]
    if m.get("caveat"):
        out.append("You must also say: %s" % m["caveat"])
    if mtc.get("rows"):
        out.extend(_render_rows(mtc["rows"]))
    elif m.get("rows_sql"):
        out.append("Row list available - ask for it if the question wants "
                   "which/what rather than how many.")
    return "\n".join(out)


def _render_rows(payload):
    """The pre-fetched rows, as an indented table the model can just format.

    Handing over the SQL and asking for it to be run does not work; handing
    over the rows does. These are already in the ledger, so anything quoted
    from here is grounded before the model speaks.
    """
    cols, rows = payload.get("columns") or [], payload.get("rows") or []
    if not rows:
        return ["      the list behind it is EMPTY - no rows matched."]
    out = ["      THE ROWS BEHIND IT - these ARE the answer if the question "
           "asks which/what/list. Use them verbatim; do not re-query and do "
           "not summarise them into a single number. Keep the FIRST column - "
           "it is what identifies each row to a human, and a list without it "
           "cannot be acted on:",
           "      | " + " | ".join(str(c) for c in cols) + " |"]
    for r in rows[:40]:
        out.append("      | " + " | ".join("" if v is None else str(v) for v in r) + " |")
    if len(rows) > 40:
        out.append("      ...and %d more rows (say so if you list them)."
                   % (len(rows) - 40))
    return out


# --------------------------------------------------------------------------
# validate - run me after editing
# --------------------------------------------------------------------------

def validate(db_params=None, verbose=True):
    """Every metric's SQL must still run. Returns (ok, [problems])."""
    import pymysql
    problems = []
    for term, names in AMBIGUOUS.items():
        for name in names:
            if name not in METRICS:
                problems.append("AMBIGUOUS[%r] -> unknown metric %r" % (term, name))
    overlap = set(AMBIGUOUS) & set(UNDEFINED)
    if overlap:
        problems.append("term is both ambiguous and undefined: %s" % sorted(overlap))

    if db_params is None:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import mysql_config
        cfg = mysql_config.config["default"]
        db_params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
                     "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
                     "database": cfg.MYSQL_DATABASE}
    conn = pymysql.connect(charset="utf8mb4", **db_params)
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            for name, mtc in sorted(METRICS.items()):
                for key in ("value_sql", "rows_sql"):
                    if not mtc.get(key):
                        continue
                    try:
                        cur.execute(mtc[key])
                        rows = cur.fetchall()
                        if key == "value_sql" and verbose:
                            print("  %-26s %-52s = %s"
                                  % (name, mtc["label"][:52], rows[0][0]))
                    except Exception as exc:  # noqa: BLE001
                        problems.append("%s.%s failed: %s" % (name, key, str(exc)[:120]))

            # Every declared join path must still return rows. A path that
            # goes to zero is a schema change nobody told us about, and it
            # would otherwise surface as a confident "there are none".
            if verbose:
                print("\n  join paths:")
            for rel in RELATIONSHIPS:
                if not rel.get("sql"):
                    continue
                try:
                    cur.execute(rel["sql"])
                    n = cur.fetchall()[0][0]
                    if verbose:
                        print("  %-58s = %s" % (rel["path"][:58], n))
                    if not n:
                        problems.append("join path returns NO rows: %s" % rel["path"])
                except Exception as exc:  # noqa: BLE001
                    problems.append("join path failed (%s): %s"
                                    % (rel["path"], str(exc)[:120]))
    finally:
        conn.close()
    return (not problems), problems


if __name__ == "__main__":  # pragma: no cover
    import sys
    print("metrics (%d), ambiguous terms (%d), undefined terms (%d)\n"
          % (len(METRICS), len(AMBIGUOUS), len(UNDEFINED)))
    ok, probs = validate()
    print()
    for p in probs:
        print("  PROBLEM: %s" % p)
    print("OK" if ok else "%d problem(s)" % len(probs))
    sys.exit(0 if ok else 1)
