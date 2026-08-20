# Working on this repo

Flask + MySQL EMC test-lab app. Four blueprints mount from `app.py`: the core
request/planner app, `datasheet_gen` (fill datasheets, generate Word), `report_gen`
(the report wizard) and `nlp_search` ("Ask the Lab Data" — natural language to SQL).

Most of what follows is about `nlp_search`, because that is where a mistake is
invisible: a wrong answer there looks exactly like a right one.

---

## 1. `schema_catalog.py` is GENERATED. Regenerate it.

```bash
python -m nlp_search.build_catalog        # 0.6s, read-only against the DB
```

**Run it whenever the schema changes OR the data changes materially.**

Two hooks in `.claude/settings.json` handle the cases a person forgets, both
calling `.claude/hooks/catalog_guard.py`:

- **on edit** — touching `build_catalog.py`, `projection_schema.py`,
  `projection.py` or `models.py` regenerates the catalog immediately and says so.
- **at session start** — the committed catalog is compared against the live
  database, read-only. Tables missing from it, tables it describes that do not
  exist, and row counts that crossed zero or moved by more than half are
  announced before any question gets asked. Silence means it is current.

The session-start check is the safety net for the case no hook can catch: the
schema did not change, the *data* did. It cannot regenerate for you — writing a
source file at boot breaks read-only deployments — so it tells you and stops.

`nlp_search/schema_catalog.py` is the *only* thing the model sees of the database.
It never touches MySQL directly — it reads this text and writes SQL from it. About
150 facts in there are measured from live rows: every table's row count, the value
lists for 74 columns, the JSON keys of 16 text columns, and the measured join
fan-out warnings.

It is generated **and committed**, which has two consequences:

- A catalog generated on one database is *wrong* on another. The version in git
  describes whichever database was last used to build it, including any
  `is_synthetic=1` demo rows. Anyone deploying must regenerate against their own
  data before trusting an answer.
- Two people regenerating means a merge conflict on a generated file. Always
  resolve it by taking either side and re-running the generator, never by hand.

Why it matters, concretely: the catalog inherited in August said
`datasheet (24 rows)` and `datasheet_measurement (659 rows)` against a database
where both were empty, and listed three status values that did not exist. The model
believed all of it. That was a large share of the wrong answers.

`ALLOWED_TABLES` in that file is also the security boundary — `sql_guard` refuses
any table not listed, so a table missing from the catalog cannot be queried at all.

## 2. Verify with the evals, not by reasoning

```bash
python tools_routing_eval.py     # FREE, deterministic, ~1s. Must stay 32/32.
python tools_insight_probe.py    # FREE. Calls all 10 insight primitives.
node   tools_render_eval.js      # FREE. Does the answer SURVIVE rendering.
python tools_join_eval.py        # spends tokens; answers vs SQL truth
python tools_user_eval.py        # spends tokens; questions in the APP's words
```

Run the three free ones after any change to `nlp_search` — and run the render one
after any change to `templates/base.html` too, which is where the answer stops
being text and becomes something a person reads.

That third one exists because correctness is not the only way to lose. A
nine-row product listing came back right and reached the screen as a wall of
literal `|` characters: the model had skipped the `|---|---|` row that markdown
requires and models omit freely, so the table was never recognised as one. To a
user that is indistinguishable from a broken tool, and no eval that grades
*answers* would ever have caught it. It runs the real functions out of
`base.html` against a stub DOM, so it cannot drift from what the browser does.

`tools_join_eval.py` and `tools_user_eval.py` score the same system twenty points
apart, and the only difference is whose vocabulary the questions use — the first
says "requested tests" and "revisions" (table words), the second says "Job Number"
and "Data Sheet" (screen words). **Write test questions in the app's words**, taken
from the templates, not from the schema. A suite written by someone who has read
`models.py` measures how well the assistant answers that person.

Score three buckets, and watch the third: right / refused / **confidently wrong**.
A refusal costs a little time; a confident wrong answer costs the whole tool.

## 3. Do not fix a wrong answer by writing that question into the prompt

Add the general, schema-derived rule instead, and measure it at build time so it
regenerates and cannot go stale. `build_catalog.py` has the pattern: the judgement
is a short hand-written list (`COVERAGE_EXPECTED`, `TEXT_JOINS`,
`TABLE_OWNER_PREFIXES`), the numbers are measured.

This is not style. `evals.py` records that tuning against anecdotes oscillates —
tightening the grounding check to catch an invented count made it withhold a
correct one two runs later. Four prompt directives were added in one session and
two of them lost to the model anyway. Prefer:

- deleting a blunt rule over adding a narrow one (three keyword vetoes were doing
  more harm than good)
- a measured number in the catalog over a sentence in the prompt
- a deterministic primitive in `insights.py` over trusting model-authored SQL for
  anything with arithmetic in it

## 4. Traps in this schema that produce correct SQL and wrong answers

- **`tco_id` is a JOB, `job_number` is a different column.** `IEC-EMC-004` vs
  `TFS-EMC-2026-002`. A job number used in a `tco_id` filter matches nothing and
  looks identical to a real absence.
- **A job is not a datasheet.** One datasheet per test, up to twelve per job.
  Never filter a datasheet query on `tco_id` alone — add `test_code`.
- **Two axes, never mixed.** `datasheet.failure_reason_code` is why the *unit*
  failed a standard; `datasheet_status_history.reason_code` is why the *record* was
  sent back in peer review. `emc_reason_code.family` separates them. Conflating
  them is the specific error the taxonomy exists to prevent.
- **`datasheet_revision.status` is never the outcome** — it holds the status the
  revision was submitted *from*, i.e. `'Draft'` on every row including approved
  ones. The outcome is in `datasheet_status_history.to_status`.
- **`datasheet.revision_no` is a next-to-edit pointer**, always one higher than the
  highest frozen revision.
- **Three spellings per test code.** Requests say `RS` / `POWER_FREQ` / `FLICKER`;
  datasheets say `RS_RI` / `PFMF` / `VOLTAGEFLICKER`. Use `semantics.canon_sql`.
  A naive join drops four of eleven test types.
- **`planner_entries.status = 'datasheet_uploaded'` means a FILE was attached**,
  not that the form was filled — some entries in that status have no `datasheet`
  row at all.
- **`job_number` uses `''`, not NULL**, for "not yet issued", so
  `COUNT(DISTINCT job_number)` over-counts by one.
- **`equipment.name` is not unique** (9 duplicates) and `datasheet_equipment` joins
  it by name, so `COUNT(*)` over that join double-counts. `asset_id` is unique.

## 5. Deploying to another database

```bash
python tools_migrate_live.py --database THEIRDB            # dry run first
python tools_migrate_live.py --database THEIRDB --apply
python -m datasheet_gen.projection                          # recover CE measurements
python -m nlp_search.build_catalog                          # THE important one
```

Booting the app also applies the schema objects — `datasheet_gen/__init__.py` runs
five idempotent creators and `ensure_projection_tables` calls `_ensure_integrity`.
The migration tool exists so a production `ALTER TABLE` is a decision someone
makes rather than a side effect of a restart.

`--database` matters: `mysql_config.py` loads `.env` with `os.environ[k] = v`
unconditionally, so `.env` **overwrites** a real environment variable and
`MYSQL_DATABASE=prod python …` silently targets whatever `.env` names.

## 6. Demo data is synthetic and says so

`tools_seed_demo_requests.py`, `tools_lifecycle_probe.py`,
`tools_seed_rework_story.py` — all write through `records.upsert_record` and
`projection.record_transition`, the functions the app itself calls. Rows carry
`is_synthetic=1`, product names start with `DEMO`, TCOs are in the `DEMO-EMC-3xx`
block. Each takes `--clean`.

Never seed by INSERTing into the analytical tables directly: a `form_json` of
subtly the wrong shape does not error, it projects to nothing, and the empty
result looks like a mapping bug. Build forms from the test's own schema JSON in
`datasheet_gen/schemas/`.

Remove them before a production deploy. The assistant excludes them by default but
they are ordinary rows to every other report.
