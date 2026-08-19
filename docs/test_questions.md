# Questions used to test the assistant

Every question below was put through the live pipeline, and the verdict beside it
is against SQL truth rather than against a reading of the prose. Re-runnable:

```bash
python tools_routing_eval.py     # FREE, deterministic, ~1s. 32 questions.
python tools_insight_probe.py    # FREE. Calls all 11 insight primitives.
python tools_insight_eval.py     # ~$0.04. The 8 insight questions below.
python tools_form_eval.py        # ~$0.05. The 9 form_json questions below.
```

They are written the way somebody asks a colleague — "what went wrong with",
"sent back", "barely filled in" — with no table or column names and the odd
missing apostrophe. That is not cosmetic. `tools_join_eval` and `tools_user_eval`
score the same system twenty points apart on the same data, and the only
difference between them is whose vocabulary the questions use.

Cost is about **$0.003–0.006 a question**, so a full pass of both paid suites is
under ten cents. Money is not the reason to test sparingly; time is.

## How to read the verdicts

| verdict | meaning |
|---|---|
| **RIGHT** | the fact is there and it matches the database |
| **DECLINED** | it said it could not answer — a **pass** when the data genuinely cannot support the question |
| **WITHHELD** | the analysis was correct and `verify` blocked the prose — a near-miss, not a wrong answer |
| **WRONG** | it stated something the database contradicts |

Only **WRONG** costs anything real. A decline wastes a minute; a wrong number
reaches a report.

---

# Part 1 — Insight questions

`python tools_insight_eval.py`. These are the questions the insight layer exists
for. **7 of 8 right**, up from 3 of 8 before the fixes described in
`git log --grep "insight layer"`.

| # | question | verdict | note |
|---|---|---|---|
| 1 | what went wrong with the Lifecycle Probe Analyser | RIGHT | CE test, `CE_LIMIT_EXCEEDED`. Watch for it tying the failure to `CAL_EXPIRED`, which is the paperwork axis — `verify._axis_crossed` now catches that |
| 2 | did any datasheet get sent back more than once, and what for | RIGHT | one: DEMO-EMC-304 ESD, `CAL_EXPIRED` then `INCOMPLETE_OBS` |
| 3 | what do the reviewers keep sending sheets back for | RIGHT | answers in readable labels rather than codes, with correct counts 2/2/1/1 |
| 4 | has any other product failed the same way as the Lifecycle Probe Analyser | **UNSTABLE** | correct when it calls `cohort`, wrong when it calls `failure_modes` and lists products that failed *differently*. Varies run to run |
| 5 | what did they change on the Spectra Bench Photometer | RIGHT | the common-mode choke on the sensor harness |
| 6 | how many of our units actually failed their test | RIGHT | 3. Said "1" before the catalog learned that criterion C/D is also a failure |
| 7 | what changed in the readings after the ESD sheet was sent back | RIGHT | ESD has no quasi-peak grid, so there is no per-frequency comparison to make |
| 8 | who is sending back the most work in peer review | RIGHT | Saimounika Chandavolu, 3 of 6. Said "zero rejections exist" before |

**Case 4 is the honest ceiling.** Both mitigations are in — `cohort` takes a
product directly so it is one call, and every `failure_modes` row now says
whether the mode is shared before naming a product — but which tool the model
reaches for is not deterministic, and one pass cannot tell you which you will
get.

---

# Part 2 — form_json questions

`python tools_form_eval.py`. These probe the datasheet **as the engineer
submitted it**. Most are deliberately unanswerable: `form_json` is a
megabyte-class column hidden from the model on purpose, so a decline is the right
outcome and an answer is the thing to check.

**3 right, 2 correct declines, 1 withheld, 3 wrong.**

| # | question | verdict | what happened |
|---|---|---|---|
| 1 | which datasheets look barely filled in | RIGHT | named DEMO-EMC-301's RE sheet. Reasoned from "no result, still Draft" rather than field count, but landed on the right sheet |
| 2 | which fields do engineers usually leave blank | DECLINED | correct — a blank *field* exists only in the form. Cost $0.017 and 9 queries to get there, the most expensive decline in the set |
| 3 | are any of the observation grids only half filled in | **WRONG** | "DEMO-EMC-302 CRF power grid: 1 cell of a max potential of 6, fill ratio 0.1667". That grid **has** one cell and is full. The denominator was invented |
| 4 | what do engineers end up changing after a datasheet gets sent back | DECLINED | correct for the lab-wide ranking. Per datasheet, `review_history` does answer it |
| 5 | do the ambient conditions on our datasheets look sensible | WITHHELD | computed it correctly — avg 19.375 °C, min/max, out-of-range counts — then `verify` blocked the prose over its own rounded figures (19.38, 37.17) and the 20/30 thresholds it chose |
| 6 | which datasheets have nobody signed off on them | **WRONG** | "Two datasheets have no lab manager sign-off". `signoff_date` is NULL on **6** of 12. It found 2 and presented them as the whole answer |
| 7 | what deviations from the standard have engineers written down | RIGHT | all three, verbatim |
| 8 | are the two ESD datasheets filled in the same way | RIGHT | "No — ambient and environmental fields differ." Correct |
| 9 | was the ESD grid already incomplete the first time it was submitted | **WRONG** | "No, the first submission was already populating the grid." It checked the `air` grid, which was complete, and missed that `ind` was 24 of 48 cells empty |

## Why 3, 6 and 9 all failed the same way

None of them invented a *value* — every number came from somewhere real. Each
picked the **wrong population** and then reported its answer as complete:

- **#3** counted cells in a grid and made up what the total should be.
- **#6** found 2 of 6 and stopped.
- **#9** checked one grid of three and generalised.

That is the class of error left after this session's work. Everything built here
validates numbers against the ledger — the denominator on filtered counts, the
arithmetic fallback, the instrumentation fix. A number that is genuinely in the
evidence but describes the wrong set passes all of it.

## What #9 costs, and why it cannot be fixed in the model

#9 is the highest-value question in the set — *"was this incomplete before the
reviewer caught it"* — and it is structurally unanswerable:

```
DEMO-EMC-304 ESD, indirect discharge grid
  revision 1   24 of 48 cells empty   rejected for CAL_EXPIRED
  revision 2   24 of 48 cells empty   rejected for INCOMPLETE_OBS
  revision 3    0 of 48 cells empty   approved
```

The reviewer missed the half-empty grid on the first round and caught it on the
second, so the engineer made two trips for something one query finds before
submitting. But the projection **drops empty cells** rather than storing blanks:
`datasheet_rev_observation` holds 24 rows for the broken revisions and 48 for the
fixed one, so the incompleteness is a row count with nothing declaring what the
count should be — and that table is excluded from the catalog regardless.

`docs/form_json_analysis.md` **A9** answers it in SQL. A person can run it; the
assistant cannot.

---

# Part 3 — Free suites, run these first

`tools_routing_eval.py` — 32 questions, deterministic, about a second, no tokens.
It only asks whether each question reached the worker that owns the tables for it,
which is worth checking before spending anything: a question routed to a worker
that cannot see the answer comes back as a confident absence.

`tools_insight_probe.py` — calls all 11 primitives with real arguments and reports
`OK` / `EMPTY` / `RAISED`. Free. `RAISED` must stay 0.

Both must be green before the paid suites tell you anything useful.

---

# Part 4 — Questions you asked in the UI

From `nlp_search_audit`. These found real bugs, and every one is now covered by a
regression above.

| you asked | what it exposed |
|---|---|
| Which of the equipments are in maintenance | the catalog's row counts were stale against the live database |
| how many equipment used in job TFS-EMC-2026-002 | the `equipment.name` join multiplies rows |
| any sheets in which it was rejected after review | a `tco_id` filter without `test_code` collapses a job into a datasheet |
| Why DEMO-EMC-301 was rejected and any rejected more than once | `product` + `tco` are ANDed; a mismatched pair matched nothing and became "no rejections for 302 or 303" when both had been |
| How can we make that Test accepted again | the answer was withheld over `LIMIT 200` and a row id, neither of which is a claim about the lab |
| any tests whose deadline has ended and not submitted | 16 rows with 16 blank test names — `test_code` selected from a table the WHERE clause excludes |
| which tests were these based on the tco | "these" resolved to a sheet from two turns earlier instead of the 16 overdue items |

---

# Writing new questions

Four rules, each learned the hard way here:

1. **Use the app's words, not the schema's.** "Data Sheet" and "Job Number", not
   `datasheet` and `tco_id`. A suite written by someone who has read `models.py`
   measures how well the assistant answers that person.
2. **Give every question a truth in SQL.** Not a description of the answer — the
   query. Prose expectations drift and cannot be re-run.
3. **Write the `must_not` before the `must`.** The failure that costs anything is
   a confident wrong answer, and it is easier to name what would be wrong than to
   enumerate everything that would be right.
4. **Distrust your own grader.** Mine was wrong five times: it demanded raw codes
   over readable labels, matched `"3"` inside `"DEMO-EMC-301"`, missed an axis
   crossing phrased differently from its fixed pattern, scored a refusal as a
   pass, and read `"not filled in identically"` as a claim that they were
   identical. The verdicts narrow what you have to read. They do not replace
   reading it.
