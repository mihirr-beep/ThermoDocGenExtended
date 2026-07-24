# Merged Codebase — Workflow + Datasheet Engine

This folder is a **merge of two divergent copies** of the Thermo Fisher EMC test-lab
application, created 2026-07-24. It combines:

| Side | Source | What it contributes |
|---|---|---|
| **Workflow** | `report-automation` (client's work) | `app.py` request/equipment/peer-review logic, workflow `templates/`, `utils/emc_request_repository.py`, business-hours scheduling, review-status normalization, TCO alias lookup, `environment_policy.py`, `create_datasheet_records.py/.sql`, bare-metal setup helpers |
| **Datasheet engine** | `ThermoDocGenExtended` (your work, incl. **uncommitted** working-tree changes) | the entire `datasheet_gen/` package — 11 datasheet types, exact image sizing, observation legends, SURGE/EFT dropdowns, auto-fill, Word pagination, `preview_datasheets.py` |

Neither original folder was modified. This folder is a fresh `git clone` of
`ThermoDocGenExtended` with the merge performed on a new branch.

---

## How the merge was done (and why it's safe)

Both copies descend from the same repo. `report-automation` follows the **NLP** branch
lineage plus the client's workflow changes; `ThermoDocGenExtended` is the
**`feature/datasheet-generation`** branch plus your uncommitted datasheet edits. Their
common ancestor is commit `164d0b6` (2026-07-17), so a real **git 3-way merge** could be
used instead of hand-stitching the 13k-line `app.py`.

Branch: **`merged-workflow`**. History:

```
* Fold in ThermoDocGenExtended uncommitted datasheet work (your latest engine)
* Restore client bare-metal setup helpers dropped by the merge
*   Merge datasheet engine (feature/datasheet-generation) into client workflow
|\
| * (datasheet branch: image fixes, all datasheets + engine, remove Docker)
* | Client workflow layer (report-automation) over NLP baseline
```

**`app.py` auto-merged with zero conflicts** — the workflow changes and the datasheet
changes touched disjoint regions. Every merge conflict was inside `datasheet_gen/`
(schemas, `generic_service`, `generic_generator`, `generic_form`, `RS_RI.docx`) and was
resolved **in favor of your datasheet engine**, then the whole module was overlaid with
your current working-tree version so it matches your latest on-disk work exactly.

### Key integration point — peer-review datasheet generation
This was the one place the two sides could have conflicted, but they're compatible **by
design**. Your engine's render functions were written to be shared:
- `datasheet_gen/routes.py::_render_ce_docx` — *"Shared by 'send to peer review' and the post-approval 'generate final' regeneration."*
- `datasheet_gen/generic_routes.py::_render_datasheet_docx` — *"...both produce an identical document."*

Result flow: the `.docx` is generated up-front when a datasheet is **sent to peer review**
(reviewers see the real document), and `app.py::_generate_final_datasheet_after_peer_review`
re-invokes the **same** render functions on **approval** to produce the final. Verified:
call signatures and 3-tuple returns match.

---

## Decisions made during the merge

1. **`datasheet_gen/` = your version** (feature branch + your uncommitted edits), wholesale.
2. **Workflow files = client's version** (`app.py`, `templates/review.html`, `index.html`, `planner.html`, `assigned_test.html`, `utils/emc_request_repository.py`).
3. **Docker stays removed** (`.dockerignore` deleted, as on your datasheet branch).
4. **Client setup helpers restored** — the 3-way merge deleted `setup.ps1`, `setup.sh`,
   `sitecustomize.py`, `wait_for_db.py` (they're not on the datasheet branch). They don't
   conflict with anything, so they were restored to preserve the client's work. Remove them
   if you prefer the datasheet branch's bare-metal-docs-only setup.
5. **DB config = local dev default** (see below) — inherited from the client's `mysql_config.py`.

---

## Verification performed

- ✅ All **53 Python files byte-compile** (`py_compile`).
- ✅ **Full import smoke test** — `app`, all `datasheet_gen` modules, `models`, `utils`,
  `environment_policy`, and every render/record helper import cleanly (`create_app()` is
  guarded by `__main__`, so import needs no DB).
- ✅ No leftover conflict markers anywhere.
- ✅ `datasheet_gen/` is byte-identical to your `ThermoDocGenExtended` working tree.
- ✅ Workflow files are byte-identical to `report-automation`.

> Not yet done (needs a running MySQL + browser — your environment): live `create_app()`
> boot, end-to-end request → planner → datasheet → peer-review → approval flow, and
> generating each of the 11 datasheet `.docx` files. See test checklist below.

---

## Running it

Requires **Python 3.11** and **MySQL 8**.

```bash
# 1. create a virtualenv and install deps
python -m venv .venv
.\.venv\Scripts\activate            # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt

# 2. configure the database  (see "Database config" below)

# 3. one-time DB setup on a fresh/empty database
python init_db.py                    # create schema
python create_datasheet_records.py   # create the datasheet_records table (needed by the datasheet feature)
python seed.py                       # seed users + sample equipment

# 4. run
python app.py                        # http://localhost:3000
```

Seeded logins (all `Password@123`): `admin`, `engineer1`, `engineer2`, `requester1`, `requester2`.

### Database config
`mysql_config.py` currently defaults to **local dev**: `localhost` / `root` /
`Thermo@123` / database `test_plan_generator` (overridable via env vars
`MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`).

- To use a **local** MySQL: create the `test_plan_generator` database and matching user, or
  set the env vars / a `.env` file to your local credentials.
- To point at your **existing** DB (the one `ThermoDocGenExtended` uses), create a `.env`
  with those `MYSQL_*` values (the remote block is present but commented in `mysql_config.py`).

`.env.example` is included as a template. `.env` is git-ignored and not present.

---

## Manual test checklist (do these against your DB)

1. `python app.py` boots without error and serves `:3000`.
2. Log in as `admin`; the dashboard, planner, review, and equipment pages render.
3. Submit an EMC request → assign TCO → plan a test on the planner (confirm the new
   **09:00–18:00 business-hours** validation rejects out-of-hours windows).
4. As an engineer, open an assigned test → fill a datasheet → **exact image sizing**
   (cm inputs), observation-legend dropdowns, and draft save/discard all work.
5. **Send to peer review** → a `.docx` is generated and viewable.
6. As reviewer, **approve** → the final `.docx` regenerates identically.
7. Generate one datasheet of each type; spot-check `preview_datasheets.py` output.
8. Trigger equipment reminders (`send_equipment_reminders.py`) → confirm admin-fallback
   recipients and calibration/IC/maintenance scheduling.
