# -*- coding: utf-8 -*-
"""REAL or DEMO: which corpus a question is allowed to see.

WHY THIS IS A POLICY AND NOT A PROMPT LINE
------------------------------------------
The demo corpus was seeded to make insight questions demonstrable, and it was
built to look real, because a synthetic campaign that obviously isn't one
proves nothing. That is exactly what makes it dangerous sitting unlabelled next
to accredited test data. Measured on this database on 2026-08-27:

    iec_emc_requests      10 real   12 synthetic   (22 total)
    planner_entries       17 real   55 synthetic   (72 total)
    request-level tests   40 real   55 synthetic   (95 total)

So three quarters of the schedule is synthetic. An unscoped "how many tests are
assigned" is not slightly off - it is mostly answering about data that does not
exist. The eval case that asks how many requests there are expects 9 and the
system answered 22.

TWO THINGS THAT LOOK LIKE THEY WOULD WORK AND DO NOT
----------------------------------------------------
1. Filtering on the TCO prefix. `DEMO-EMC-301` through `316` are demo, but so
   are `IEC-EMC-900` and `IEC-EMC-911`, which carry the real prefix and are
   flagged synthetic. Prefix-matching would let two synthetic jobs through and
   there is no way to see that from the name. `is_synthetic` is the only
   authority.

2. Telling the model. The instruction "exclude demo rows unless asked" is one
   line in a prompt that already carries a lab-rules block, a semantics block,
   a gates block and a catalog, and the model has demonstrably dropped caveats
   that were sitting in its prompt. A rule that must hold on every query is
   enforced where the query runs, not where it is composed.

So the scope is decided here from the question's own words, threaded through
to sql_guard, and REJECTED at validation time if a query that reads the request
table does not carry the filter. The model can be wrong about it and the answer
is still right.

WHAT SCOPE MEANS
----------------
    REAL   (default)  is_synthetic = 0. What a lab user means every time.
    DEMO              is_synthetic = 1. Only when explicitly asked for.
    ALL               no filter, and the answer must say it is mixed.

ALL is not reachable from the default path on purpose - it exists for "compare
the demo set against real jobs", which is a question an admin asks knowingly.
"""
import re

REAL = "REAL"
DEMO = "DEMO"
ALL = "ALL"

DEFAULT = REAL

# The column that decides it, and the one table that carries it. Everything
# else inherits scope by joining back to this table.
FLAG_TABLE = "iec_emc_requests"
FLAG_COLUMN = "is_synthetic"

# Tables whose rows belong to a request, and are therefore contaminated when
# the request is. Measured on 2026-08-27 - the proportion of each table that
# hangs off a synthetic request:
#
#     datasheet_status_history   110/110   100%
#     datasheet_observation      945/980    96%
#     datasheet                   50/59     85%
#     datasheet_measurement     2748/3418   80%
#     datasheet_equipment        187/241    78%
#     planner_entries             55/72     76%
#     iec_emc_request_tests       55/135    41%
#
# At those rates an unscoped answer about the schedule or a datasheet is not
# slightly off - it is mostly a statement about data the lab never produced.
# So the guard requires the flag whenever any of these is read, which in
# practice forces the join back to the request.
#
# equipment, maintenance, equipment_history and users are NOT here: they are
# shared lab infrastructure with no request of their own. Requiring a join
# from them would be requiring a join that does not exist.
SCOPED_TABLES = frozenset({
    "iec_emc_requests",
    "iec_emc_request_tests",
    "planner_entries",
    "datasheet",
    "datasheet_records",
    "datasheet_revision",
    "datasheet_draft_history",
    "datasheet_status_history",
    "datasheet_measurement",
    "datasheet_observation",
    "datasheet_observation_legend",
    "datasheet_equipment",
    "datasheet_software",
    "datasheet_modification",
    "datasheet_ce", "datasheet_crf", "datasheet_eft", "datasheet_esd",
    "datasheet_harmonic", "datasheet_pfmf", "datasheet_re", "datasheet_rs_ri",
    "datasheet_surge", "datasheet_voltagedips", "datasheet_voltageflicker",
    "iec_emc_request_accessories", "iec_emc_request_cables",
    "iec_emc_request_categories", "iec_emc_request_decision_rules",
    "iec_emc_request_eut_specs", "iec_emc_request_functional_modes",
    "iec_emc_request_product_environments",
    "iec_emc_request_product_standards", "iec_emc_request_serial_numbers",
    "iec_emc_request_service_types", "iec_emc_request_supply_vf",
    "iec_emc_request_test_ce", "iec_emc_request_test_crf",
    "iec_emc_request_test_eft", "iec_emc_request_test_esd",
    "iec_emc_request_test_flicker", "iec_emc_request_test_harmonic",
    "iec_emc_request_test_power_freq", "iec_emc_request_test_re",
    "iec_emc_request_test_rs", "iec_emc_request_test_standards",
    "iec_emc_request_test_surge", "iec_emc_request_test_voltage_dips",
    # Four more that hang off a request in THIS catalog. Two of them
    # (iec_emc_request_test_ce_signal_lines, iec_emc_request_wireless) do not
    # exist in the catalog this list was first written against, which is why it
    # omitted them - our catalog is newer. A query joining through a
    # request-derived table that is missing here bypasses the guard completely,
    # so the set has to track the catalog rather than the other way round.
    "iec_emc_request_additional_models",
    "iec_emc_request_test_ce_signal_lines",
    "iec_emc_request_wireless",
    "report_draft",
    # datasheet_fixed_values and datasheet_procedures are deliberately NOT
    # here: they are admin configuration keyed by test_code, with no request of
    # their own, so requiring a join back to the flag would require a join that
    # does not exist.
})

# How to get from a table back to the flag. Written out because "join to the
# request" is three different joins depending on where you start, and the model
# picking the wrong one is how a count gets multiplied.
JOIN_RECIPES = (
    ("planner_entries",
     "JOIN iec_emc_requests r ON r.id = p.test_request_id"),
    ("iec_emc_request_tests",
     "JOIN iec_emc_requests r ON r.id = t.request_id"),
    ("datasheet",
     "JOIN iec_emc_requests r ON r.id = d.test_request_id"),
    ("datasheet_<code> / datasheet_measurement / datasheet_observation / "
     "datasheet_status_history / datasheet_equipment",
     "JOIN `datasheet` d ON d.id = <table>.datasheet_id "
     "JOIN iec_emc_requests r ON r.id = d.test_request_id"),
    ("iec_emc_request_<child>",
     "JOIN iec_emc_requests r ON r.id = <table>.request_id"),
)

# Asking for the demo data. Deliberately narrow: "demo" has to be about the
# DATA, not about a demonstration, so "show me a demo of the report" does not
# silently switch corpus.
_DEMO_RE = re.compile(
    r"\bdemo\b(?!\s*(?:of|for)\b)"
    r"|\bdemos\b"
    r"|\bsynthetic\b"
    r"|\bfake\b"
    r"|\bdummy\b"
    r"|\bseeded\b"
    r"|\bsample\s+(?:data|corpus|dataset)\b"
    r"|\btest\s+(?:data|corpus|dataset)\b"
    r"|\bDEMO-[A-Z]{2,}-\d+\b",
    re.I)

# Asking for both at once.
_ALL_RE = re.compile(
    r"\b(?:including|include|includes|with|plus)\s+(?:the\s+)?"
    r"(?:demo|synthetic|test)\s*(?:data|rows|records|jobs)?\b"
    r"|\b(?:real\s+and\s+demo|demo\s+and\s+real|both\s+real\s+and)\b"
    r"|\beverything\s+including\b"
    r"|\bregardless\s+of\s+(?:whether|scope)\b",
    re.I)

# Saying "real" out loud. Pins REAL even if the sentence also says "demo",
# which is how "how many real jobs, not the demo ones" has to resolve.
_REAL_RE = re.compile(
    r"\breal\b|\bgenuine\b|\bactual\s+(?:jobs|requests|data)\b"
    r"|\bnot\s+(?:the\s+)?(?:demo|synthetic)\b"
    # exclude / excluding / ignore / omit / without, imperative or participle.
    # "excluding" alone was not enough: "Exclude the demo rows - how many
    # requests?" is the imperative and read as a request FOR demo data, which
    # is the exact opposite of what was asked.
    r"|\b(?:exclude|excludes|excluding|ignore|ignoring|omit|omitting|"
    r"without|minus|apart\s+from|other\s+than)\s+(?:the\s+|any\s+|all\s+)?"
    r"(?:demo|synthetic|test)\b", re.I)


def detect(question):
    """REAL | DEMO | ALL - which corpus this question is asking about.

    Order matters. An explicit "real" wins over an incidental "demo", because
    the only way to say "not the demo ones" is to name them both.
    """
    q = question or ""
    if _REAL_RE.search(q):
        return REAL
    if _ALL_RE.search(q):
        return ALL
    if _DEMO_RE.search(q):
        return DEMO
    return DEFAULT


def sql_predicate(scope, alias=None):
    """The WHERE fragment for this scope, or "" when nothing is filtered."""
    col = "%s.%s" % (alias, FLAG_COLUMN) if alias else FLAG_COLUMN
    if scope == REAL:
        return "%s = 0" % col
    if scope == DEMO:
        return "%s = 1" % col
    return ""


def wants_filter(scope):
    return scope in (REAL, DEMO)


def describe(scope):
    """One line for the prompt and the debug trace."""
    if scope == REAL:
        return ("REAL - genuine lab data only. Synthetic/demo rows are excluded "
                "and the exclusion is enforced below the model.")
    if scope == DEMO:
        return "DEMO - the synthetic demonstration corpus only, as asked."
    return "ALL - real and synthetic data together. Say so in the answer."


def caveat(scope):
    """What the reader is owed about scope, or "" when nothing is."""
    if scope == DEMO:
        return ("These figures come from the synthetic demonstration data, not "
                "from real lab records.")
    if scope == ALL:
        return ("These figures mix real lab records with the synthetic "
                "demonstration data.")
    return ""


def prompt_block(scope):
    """The scope section injected into the worker prompt.

    This tells the model what will happen rather than asking it to remember:
    the guard rejects a non-conforming query, so the useful thing to say is
    what the filter looks like, not that it matters.
    """
    lines = ["DATA SCOPE: %s" % describe(scope)]
    if not wants_filter(scope):
        return "\n".join(lines)
    lines.append(
        "  Most of this database is synthetic demonstration data - 76%% of the "
        "schedule, 85%% of datasheets, 100%% of the review history. Any query "
        "touching a request, the schedule or a datasheet MUST reach "
        "%s.%s and filter `%s`. The SQL guard rejects it otherwise - this is "
        "not advisory."
        % (FLAG_TABLE, FLAG_COLUMN, sql_predicate(scope, "r")))
    lines.append(
        "  %s is the ONLY authority. Do NOT filter on the tco_id prefix: two "
        "synthetic jobs carry a real-looking IEC-EMC- id." % FLAG_COLUMN)
    lines.append("  Joins back to the flag, by where you start:")
    for start, join in JOIN_RECIPES:
        lines.append("    %-28s %s" % (start, join))
    lines.append("  equipment / maintenance / users are lab-wide and need no "
                 "scope filter.")
    return "\n".join(lines)
