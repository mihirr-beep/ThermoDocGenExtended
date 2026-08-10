# -*- coding: utf-8 -*-
"""The worker nodes: one specialist agent per slice of the schema.

Each worker owns a domain, sees only that domain's catalog, and can only reach
that domain's tables. The orchestrator calls them as tools and keeps control of
the conversation, so it can fan out across domains for one question, re-ask a
worker with a narrower brief, and decide when it has enough.

Scoping earns its keep twice over. A worker's prompt is a third of the schema
instead of all of it, which measurably improves the SQL a model writes; and the
allowlist handed to sql_guard is the worker's own, so a question about
calibration cannot produce a query against the datasheets however the model
phrases it. The narrowing is enforced at validation time, not by instruction.

Workers do not carry the evidence. Their SQL tool writes every row it receives
straight into the shared ledger, so what a worker *says* is navigation and what
the ledger *holds* is the record. The two are checked against each other before
anything reaches the user.
"""
import os

from . import probes, semantics, sql_tool
from .schema_catalog import DOMAIN_META, index_prompt_text, tables_for


def _full_catalog():
    """Whole-schema text, for the NLP_SINGLE_AUTHOR variant only (off by
    default). That agent has no describe_table tool and no domain to slice by,
    so it still carries everything."""
    from .schema_catalog import catalog_prompt_text
    return catalog_prompt_text(None)

# Follows the orchestrator - see DEFAULT_MODEL there for why.
DEFAULT_WORKER_MODEL = "gpt-5-nano"

_WORKER_INSTRUCTIONS = """You are the {title} specialist for a Thermo Fisher EMC test lab.

The orchestrator sends you one focused sub-question. Answer it from YOUR tables
only, using SQL, and report what you found. You are not talking to the end
user - you are reporting to another agent, so be terse and factual.

## Your domain
{blurb}

## How to work
1. Read the sub-question. If it needs a table you do not have, say so in one
   line - name what you would need. Do NOT guess and do NOT apologise; the
   orchestrator will route that part elsewhere.
2. Before you filter on any literal you have not already seen - a status, a
   test code, a result, a name - check it exists:
     * list_values(table, column) for a categorical column;
     * resolve_entity(kind, text) for a person, job, product or equipment name.
   A filter on a value that is not in the table returns zero rows, which looks
   exactly like a genuine "none". This is the single most common way to be
   confidently wrong. Spend the extra query.
3. Run your SELECT with run_sql. On an error, read it, fix the SQL, retry (up
   to 3 attempts).
4. Report back: the figures you found, and the SQL shape you used. If you found
   nothing, say whether it is "no such value in this column" or "the value
   exists but no rows match" - those mean different things and the orchestrator
   needs to know which.

   When the sub-question uses a value that does not exist, say so explicitly
   AND list the real ones. Not "0 rejected" - that reads as though Rejected is
   a status that happens to be unused. Say: "there is no 'Rejected' status;
   status is only ever Approved or Draft, and his two are both Approved." The
   orchestrator will pass that on, and the user learns the shape of the data
   instead of being confirmed in a wrong assumption.

## Look at the data before you trust the schema
The column list tells you a column EXISTS. It does not tell you what is in
it, and every wrong answer this system has produced came from that gap.

- Joining two tables on a TEXT column (a name, a code, a title)? Run
  sample_rows on both first. The same test is spelled 'FLICKER' in one table
  and 'VoltageFlicker' in another; a join that looks right drops a third of
  the rows and reports the smaller number with total confidence.
- Counting or filtering on a column you have not used before? profile_column
  it. A column that is 20%% empty gives you an undercount that looks exact.
- A joined query comes back with a join_check note comparing the result to
  the driving table's own row count. READ IT. More rows than the base table
  means the join multiplied and your COUNT is inflated; fewer means it
  dropped rows and your answer is short. This is the only warning you get -
  a wrong join does not raise an error, it returns a confident wrong number.

## Writing SQL (MySQL 8)
- ONE single SELECT per call (WITH ... SELECT is fine). No comments, no
  semicolons, no SET, no multiple statements.
- Only the tables and columns in your catalog below. Never invent a name. A
  column that is not listed either does not exist or is deliberately out of
  reach - do not try it.
- Always LIMIT (200 max). Prefer COUNT / GROUP BY over dumping rows.
- Free text (names, products, standards): match case-insensitively with a
  PARTIAL LIKE - LOWER(col) LIKE '%fragment%' - never '='. People are stored as
  full names, so a first name only ever matches with LIKE.
- Relative dates: CURDATE() with DATE_SUB / INTERVAL. Today is {today}.
  Timestamps are IST.
- Never select credential columns.

## Lab rules you must follow
These are decisions the lab has made. The data cannot tell you them and you
must not decide them yourself on the fly.
{lab_rules}

## IF A QUERY IS REFUSED OR FAILS, DO NOT ANSWER A DIFFERENT QUESTION
A rejected query means you could not answer THAT question. It does not mean
you should find a question you can answer and report that instead. Asked
which engineers are on tests with no datasheet, a worker was refused the
datasheet table, ran "engineers with an active planner entry" and returned
those names as the answer. It named someone with no unfilled test at all.
Say what was refused and what you would need. A named gap is useful; a
confident answer to a question nobody asked is a defect.

## Honesty
Report only what the rows actually said. Do not round, extrapolate, average or
"approximately" anything the SQL did not compute. If a result is truncated, say
so. If you could not answer, say precisely what was missing. An honest "I did
not find it" is a useful result; a plausible number is not.

Never report a raw id as if it identified someone. "User ID 5 has the most
work" is useless to a human - join to users and give the name. The same goes
for request_id, planner_entry_id and datasheet_id: report the job number, the
test code, the person.

tco_id is the ONE exception - "IEC-EMC-004" is what the lab calls that job out
loud, and it is the only handle a job has before a job number is issued. Always
carry it through. Never identify a job by product name alone; products repeat
across jobs.

{joins}

{metrics}

## Your tables (the ONLY ones you may query)
You get names and purposes here, NOT columns. Call describe_table with the
two or three you need - all at once, comma-separated - then write the SQL.
Guessing a column name wastes a turn on a MySQL error; describing costs one
turn for every table you need.
{catalog}
"""


_STANDALONE_BLOCK = """
## YOU ARE ANSWERING THE USER DIRECTLY

There is no orchestrator above you on this question - what you write is what
they read. So:

- Write for a person. Lead with the direct answer, then one short supporting
  line. PLAIN TEXT: no markdown, no **bold**, no ### headings. The interface
  shows your reply verbatim, so markup arrives as literal asterisks.
- Never print a raw database id. "user_id 5" means nothing to a reader - join
  to users and give the name. Same for request_id, planner_entry_id,
  datasheet_id: give the job number, the test code, the person.
- Be efficient. You have the whole question; plan the ONE query that answers it
  rather than exploring. Probe only a value you genuinely have not seen.
- If the question needs a table you do not have, say so and name what you would
  need. Do not answer from a table that merely sounds close.
- ONLY ask when the user NAMED something and that name is ambiguous. A
  question naming nobody is not ambiguous - it asks about everything. "Are
  there tests requested on a job but never scheduled" means ACROSS ALL JOBS.
  Do not answer a question with "which one did you mean?" when the user
  deliberately did not name one; run the query over everything.
- WHEN A NAME IS AMBIGUOUS, ASK. If resolve_entity returns more than one
  candidate, list them with something that tells them apart - role, job - and
  ask which is meant. Do NOT pick one and do NOT add them together. There are
  two people called Krishna here; "3 CE datasheets" is the sum of two different
  people's work and is true of neither of them.
"""


def _build_one(domain, db_params, ledger, model=None, extra_blocks=(),
               standalone=False):
    """One worker agent, wired to its own slice of the schema.

    ``standalone`` means it is answering the user directly rather than
    reporting to the orchestrator - so it must write for a person instead of
    filing a terse report. Running a worker at the top level for a question
    that plainly belongs to one domain removes the orchestrator's turns, which
    were most of the cost: nine model calls for a question needing one SELECT.
    """
    from agents import Agent, function_tool

    meta = DOMAIN_META[domain]
    allowed = tables_for(domain)

    @function_tool(name_override="run_sql")
    def run_sql(sql: str) -> str:
        """Run ONE read-only MySQL SELECT against your domain's tables.

        Args:
            sql: A single SELECT using only tables from your catalog. Must
                include a LIMIT clause (max 200 rows).
        """
        return sql_tool.run_select(sql, db_params, allowed_tables=allowed,
                                   ledger=ledger, worker=domain)

    @function_tool(name_override="list_values")
    def list_values(table: str, column: str, contains: str = "") -> str:
        """The DISTINCT values that actually exist in one column, with row
        counts. Use this before filtering on a value you have not seen, and to
        tell a real "none" from a filter that matched nothing.

        Args:
            table: A table from your catalog.
            column: A column on that table.
            contains: Optional substring filter on the values.
        """
        return probes.list_values(db_params, table, column,
                                  contains=contains or None,
                                  allowed_tables=allowed, ledger=ledger)

    @function_tool(name_override="resolve_entity")
    def resolve_entity(kind: str, text: str) -> str:
        """Find the real rows matching a name before you filter on it. Returns
        the candidates, or an explicit no-match.

        Args:
            kind: One of person, job, product, equipment, standard.
            text: The name or fragment the user gave.
        """
        return probes.resolve_entity(db_params, kind, text, ledger=ledger)

    @function_tool(name_override="sample_rows")
    def sample_rows(table: str, limit: int = 5) -> str:
        """A few REAL rows from one table, so you can see what the values
        actually look like - spellings, casing, formats.

        The column list cannot tell you that this table spells a test
        'VoltageFlicker' while another spells the same test 'FLICKER'. Five
        rows can. Look before you join on any text column.

        Args:
            table: A table from your list.
            limit: How many rows, 1-20 (default 5).
        """
        return probes.sample_rows(db_params, table, limit,
                                  allowed_tables=allowed, ledger=ledger)

    @function_tool(name_override="profile_column")
    def profile_column(table: str, column: str) -> str:
        """Whether one column can be trusted for what you are about to do:
        how much of it is empty, whether values repeat, and its real range.

        Use it before counting or filtering on a column (a mostly-empty column
        gives a confident undercount) and before joining on one (repeated
        values multiply rows).

        Args:
            table: A table from your list.
            column: A column on that table.
        """
        return probes.profile_column(db_params, table, column,
                                     allowed_tables=allowed, ledger=ledger)

    @function_tool(name_override="lab_metric")
    def lab_metric(name: str) -> str:
        """Run one of the lab's REVIEWED measures by name and get its figure,
        already computed, plus the rows behind it where they are the answer.

        Use this whenever the question asks for something on the measures list
        in your instructions. The SQL was written and checked by a human, so it
        is right; a version you derive yourself may not be, and if the two
        disagree this one wins.

        Args:
            name: A measure name exactly as listed, e.g. "test_unfilled".
        """
        return semantics.run_metric(name, db_params, ledger=ledger)

    @function_tool(name_override="describe_table")
    def describe_table(tables: str) -> str:
        """Columns, notes and permitted values for tables you own. Call this
        BEFORE writing SQL against a table, and name every table you need in
        one call - the prompt is resent each turn, so three separate calls
        cost three times over.

        Args:
            tables: One or more table names from your list, comma-separated,
                e.g. "datasheet, datasheet_equipment".
        """
        return probes.describe_table(tables, allowed_tables=allowed)

    @function_tool(name_override="read_grid")
    def read_grid(datasheet_id: int, grid: str = "") -> str:
        """The measurement / observation TABLE recorded on one datasheet - the
        rows of numbers, with their column headings. This is the only way to
        reach them: they are stored as JSON and are not selectable by SQL.

        For CE that is the Line and Neutral measurement tables (frequency,
        quasi-peak, limit, margin); for ESD, SURGE and the rest it is the
        observation grids; for RE and HARMONIC the scan tables.

        Get the datasheet_id from a query on `datasheet` first.

        Args:
            datasheet_id: `datasheet`.id.
            grid: optional single grid name, e.g. "line_measurements".
        """
        return probes.read_grid(db_params, datasheet_id, grid=grid or None,
                                ledger=ledger)

    import datetime
    instructions = _WORKER_INSTRUCTIONS.format(
        lab_rules=semantics.LAB_RULES,
        joins=semantics.relationship_block(allowed),
        metrics=semantics.metric_menu(),
        title=meta["title"], blurb=meta["blurb"],
        today=datetime.date.today().isoformat(),
        catalog=index_prompt_text(domain))
    if standalone:
        instructions += _STANDALONE_BLOCK
    for block in extra_blocks:
        if block:
            instructions += block

    chosen = model or os.environ.get("NLP_WORKER_MODEL",
                                     os.environ.get("NLP_SEARCH_MODEL",
                                                    DEFAULT_WORKER_MODEL))
    from .model_settings import for_model
    settings = for_model(chosen)
    return Agent(
        name="%s specialist" % domain,
        instructions=instructions,
        model=chosen,
        **({"model_settings": settings} if settings else {}),
        tools=[lab_metric, describe_table, sample_rows, profile_column,
               run_sql, list_values, resolve_entity, read_grid])


# What the orchestrator reads when choosing where to send a sub-question.
_TOOL_DESCRIPTIONS = {
    "requests": (
        "Ask the REQUESTS specialist. It knows what customers asked for: the EMC "
        "request / job / TCO, the product under test and its specs, which tests "
        "are in scope, declared product and test standards, requester details, "
        "approval and rejection state, planned dates. Use it for intent and "
        "scope - NOT for what was measured."),
    "schedule": (
        "Ask the SCHEDULE specialist. It knows who is doing what and when: "
        "scheduled tests, assigned engineers, workflow status, peer-review "
        "assignment and approval, cancellations, users and their roles."),
    "datasheets": (
        "Ask the DATASHEETS specialist. It knows what was actually MEASURED: "
        "per-test results and pass/fail, ambient conditions, the recorded test "
        "parameters for each test type, every individual observation cell, the "
        "equipment and software used on a test, and the review history. Use it "
        "for outcomes - NOT for what was requested."),
    "inventory": (
        "Ask the INVENTORY specialist. It knows the instruments the lab OWNS: "
        "what equipment exists, make/model/serial, calibration and maintenance "
        "due dates, and the change history. It does NOT know which equipment "
        "was USED on a test - that is recorded per datasheet, so ask_datasheets "
        "for 'what was used on', 'used most across tests', 'which instruments "
        "did this job use'."),
}


def build_standalone(domain, db_params, ledger, model=None, extra_blocks=()):
    """One worker, answering the user directly. No orchestrator above it."""
    return _build_one(domain, db_params, ledger, model=model,
                      extra_blocks=extra_blocks, standalone=True)


def build_workers(db_params, ledger, model=None):
    """[(domain, Agent, tool_description)] for every domain in the catalog."""
    return [(d, _build_one(d, db_params, ledger, model=model), _TOOL_DESCRIPTIONS[d])
            for d in DOMAIN_META if d in _TOOL_DESCRIPTIONS]


def worker_tools(db_params, ledger, model=None):
    """The workers, wrapped as tools the orchestrator can call."""
    out = []
    for domain, agent, description in build_workers(db_params, ledger, model=model):
        out.append(agent.as_tool(
            tool_name="ask_%s" % domain,
            tool_description=description))
    return out


# --------------------------------------------------------------------------
# the single author
# --------------------------------------------------------------------------
# The four-way split is retained above but no longer used by default. It cost
# more than it bought.
#
# It was meant to shrink each prompt and narrow each allowlist. The first
# premise is wrong at this size: filtering a schema that already fits in the
# context window measurably HURTS, because a column wrongly excluded is
# unrecoverable while a column wrongly included is merely noise. The whole
# catalog is 28k characters.
#
# The second premise was never load-bearing: the connection is read-only, the
# caller is an admin who can already see every one of these rows in the UI, and
# sql_guard's credential rules are global rather than per-domain. Narrowing the
# allowlist protected nothing.
#
# What it did do was make three joins unreachable by ANY worker - equipment
# used on a test, requested-versus-recorded per job, products tested versus
# only requested. "What is left to do on job X" is the most useful question a
# lab admin can ask and it was structurally unanswerable.

_AUTHOR_INSTRUCTIONS = """You are the SQL author for a Thermo Fisher EMC test lab's database. The
orchestrator sends you one focused question. Answer it from SQL and report what
you found, tersely and factually - you are reporting to another agent, not to
the user.

## How to work
1. Before filtering on any literal you have not already seen - a status, a test
   code, a result, a name - check it exists:
     * list_values(table, column) for a categorical column;
     * resolve_entity(kind, text) for a person, job, product or equipment name.
   A filter on a value that is not in the table returns zero rows, which looks
   exactly like a genuine "none". This is the most common way to be confidently
   wrong. Spend the extra query.
2. Run your SELECT with run_sql. On an error, read it, fix it, retry (max 3).
3. Report the figures, and say which query produced which. If you found
   nothing, say whether it is "no such value in this column" or "the value
   exists but no rows match" - those mean different things.

## Look at the data before you trust the schema
The column list tells you a column EXISTS. It does not tell you what is in
it, and every wrong answer this system has produced came from that gap.

- Joining two tables on a TEXT column (a name, a code, a title)? Run
  sample_rows on both first. The same test is spelled 'FLICKER' in one table
  and 'VoltageFlicker' in another; a join that looks right drops a third of
  the rows and reports the smaller number with total confidence.
- Counting or filtering on a column you have not used before? profile_column
  it. A column that is 20%% empty gives you an undercount that looks exact.
- A joined query comes back with a join_check note comparing the result to
  the driving table's own row count. READ IT. More rows than the base table
  means the join multiplied and your COUNT is inflated; fewer means it
  dropped rows and your answer is short. This is the only warning you get -
  a wrong join does not raise an error, it returns a confident wrong number.

## Writing SQL (MySQL 8)
- ONE single SELECT per call (WITH ... SELECT is fine). No comments, no
  semicolons, no multiple statements.
- Only the tables and columns in the catalog below. Never invent a name.
- Always LIMIT (200 max). Prefer COUNT / GROUP BY over dumping rows.
- Free text (names, products, standards): LOWER(col) LIKE '%fragment%', never
  '='. People are stored as full names.
- Relative dates: CURDATE() with DATE_SUB / INTERVAL. Today is {today}. IST.
- Never select credential columns.

## Two things that will make your answer wrong
- TEST CODES DIFFER PER TABLE. Joining requests to schedules or datasheets on a
  raw test_code silently drops four of eleven test types. Normalise both sides;
  the catalog's glossary gives the mapping.
- NEVER report a raw id to a human. "user_id 5" is useless - join to users and
  give the name. Same for request_id, planner_entry_id, datasheet_id: report
  the job number, the test code, the person.

## Honesty
Report only what the rows said. Do not round, average, extrapolate or
"approximately" anything the SQL did not compute. If a result is truncated, say
so. An honest "I did not find it" is a useful result; a plausible number is not.

## The schema
{catalog}
"""


def build_author(db_params, ledger, model=None):
    """One agent with the whole allowlist. Returns (Agent, description)."""
    from agents import Agent, function_tool

    allowed = tables_for(None)          # every table in the catalog

    @function_tool(name_override="run_sql")
    def run_sql(sql: str) -> str:
        """Run ONE read-only MySQL SELECT against the lab database.

        Args:
            sql: A single SELECT using only catalog tables. Must LIMIT (max 200).
        """
        return sql_tool.run_select(sql, db_params, allowed_tables=allowed,
                                   ledger=ledger, worker="sql")

    @function_tool(name_override="list_values")
    def list_values(table: str, column: str, contains: str = "") -> str:
        """The DISTINCT values that actually exist in one column, with row
        counts. Use before filtering on a value you have not seen, and to tell
        a real "none" from a filter that matched nothing.

        Args:
            table: A table from the catalog.
            column: A column on that table.
            contains: Optional substring filter on the values.
        """
        return probes.list_values(db_params, table, column,
                                  contains=contains or None,
                                  allowed_tables=allowed, ledger=ledger)

    @function_tool(name_override="resolve_entity")
    def resolve_entity(kind: str, text: str) -> str:
        """Find the real rows matching a name before you filter on it. Handles
        typos and reports when the text matches a different kind of thing.

        Args:
            kind: One of person, job, product, equipment, standard.
            text: The name or fragment the user gave.
        """
        return probes.resolve_entity(db_params, kind, text, ledger=ledger)

    import datetime
    agent = Agent(
        name="sql author",
        instructions=_AUTHOR_INSTRUCTIONS.format(
            today=datetime.date.today().isoformat(),
            catalog=_full_catalog()),
        model=model or os.environ.get("NLP_WORKER_MODEL",
                                      os.environ.get("NLP_SEARCH_MODEL",
                                                     DEFAULT_WORKER_MODEL)),
        tools=[lab_metric, describe_table, sample_rows, profile_column,
               run_sql, list_values, resolve_entity, read_grid])

    description = (
        "Ask the DATABASE. It can read every table in the lab: EMC requests and "
        "jobs, the schedule and peer review, filled datasheets with their results "
        "and observations, the equipment inventory and calibration, and users. "
        "Send it ONE focused question at a time; send several for a question with "
        "several parts. It can join across all of these - equipment used on a "
        "test, what was requested versus what was recorded, who did what when.")
    return agent, description


def author_tool(db_params, ledger, model=None):
    """The single SQL author, wrapped as a tool."""
    agent, description = build_author(db_params, ledger, model=model)
    return agent.as_tool(tool_name="query_database", tool_description=description)
