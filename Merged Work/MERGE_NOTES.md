# Merged Work — EMC Test Workflow (company base + datasheet work)

This folder merges the internal **company** codebase with the **datasheet-generation** work,
so the datasheet feature can go onto the company's repo with **no change to their implementation**
and **no conflict** with their enhancements.

## Composition

- **Base (verbatim): `Thermo-Master`** — the internal company work, copied unchanged. Includes the
  **peer-review QA workflow** and every other company enhancement. `app.py`, `models.py`,
  `templates/`, `utils/`, `auth_routes.py`, tests, docs, config = **company code, untouched**.
- **Overlay: datasheet work from `Healthark/ThermoDocGenExtended`:**
  - `datasheet_gen/` — **replaced** with the newer engine (6 datasheets — CE, RE, Harmonic,
    Flicker, Voltage Dips, EFT — dynamic observation grids, admin-editable fixed values via
    `fixed_store.py`/`admin_routes.py`, RTF import via `rtf_import.py`, env-driven `registry.py`).
  - `unsubmit.py` — **added** (admin utility to revert a submitted datasheet to `in_progress`).
  - `sample_harmonic_data.csv` — **added** (sample data for the Harmonic import).

Not copied (regenerable/runtime only): `node_modules/`, `outputs/`, `uploads/`, `__pycache__/`.
`.env` is local-only (gitignored) and is **not** part of what gets merged to the repo.

## Why this is conflict-free (verified, not assumed)

- `datasheet_gen` is a **self-contained Flask blueprint**; the company `app.py` already mounts it
  with one line — `register_datasheet_gen(flask_app)` (app.py:3922) — so **no `app.py` change**.
- The overlay's `datasheet_gen/__init__.py` registers a **superset** of the company's blueprints
  (CE + generic + records **+** your admin blueprint + fixed-values bootstrap) — nothing the
  company registered is lost.
- Package cross-imports resolve safely against the company app: `from app import get_ist_now`
  works (module-level); `from app import _update_parent_request_datasheet_status` is wrapped in
  `try/except` and no-ops (that function is **nested inside `create_app()` in BOTH** codebases —
  pre-existing behavior, not introduced by this merge).
- **`models.py` unchanged** — the company's is a superset (baseline datasheet columns + peer-review
  columns); the package's `ensure_*` helpers create their own tables at startup.

**Verified end-to-end** (isolated DB `test_plan_generator_merged`, port 3000):
- `init_db.py` added the company's peer-review columns (`peer_reviewer_user_id`,
  `peer_review_assigned_at`, `idx_planner_peer_reviewer`) **and** seeded your datasheet tables
  (`datasheet_records`, `datasheet_fixed_values`, `basic_standard_map`) in a single clean run.
- Login OK → `GET /api/planner/peer-review` = **200** (company peer review live) and
  `GET /datasheet/g/VOLTAGEFLICKER/8/form` = **200 "Flicker Datasheet"** (your datasheet live).

## Implemented: peer-review-gated datasheet flow (2026-07-15)

Per the team's direction ("Send to Peer Review instead of Generate; admin approves, then a button
to generate"), the datasheet form now routes through the company's **existing** peer-review gate.
**All changes are inside `datasheet_gen/` — no company-code edits (app.py/models.py untouched).**

**New flow (all 6 datasheets — CE + the 5 generic):**
1. Engineer fills the form → **"Send to Peer Review"** (was "Generate Datasheet"). Requires
   picking a **peer reviewer** (new dropdown). This generates the `.docx`, sets
   `datasheet_file_path`, assigns `peer_reviewer_user_id` + `peer_review_assigned_at`, sets
   `status='Peer Review'`, and appends a `SENT FOR REVIEW` note to `datasheet_comments`.
2. The entry now appears in the company's **unchanged** peer-review page (`/api/planner/peer-review`)
   and is actioned with the company's **unchanged** `/api/planner/<id>/peer-review-action`
   (approve → `datasheet_uploaded`; reject → `in_progress`, back to engineer).
3. After approval, the form shows a **"Generate Final Datasheet"** button →
   `POST /datasheet/g/<code>/<id>/generate-final` (or `/datasheet/ce/<id>/generate-final`)
   regenerates from the approved saved data and downloads the `.docx`.

**Files changed (package-local only):** `datasheet_gen/generic_routes.py`, `datasheet_gen/routes.py`
(split generate → "send to review" + `generate-final`; added reviewer resolve/candidates/audit-note
helpers), `datasheet_gen/templates/datasheet_gen/generic_form.html` + `ce_form.html` (reviewer
picker, relabeled button, post-approval button, submit JS).

**Verified end-to-end** (isolated DB, port 3000): engineer submits Flicker → assignment enters
`Peer Review` → the assigned **lab_engineer** sees it in the company queue and approves → status
`datasheet_uploaded`, single shared audit thread (SENT FOR REVIEW + APPROVED) → "Generate Final"
downloads a valid `.docx`. CE smoke-tested the same submit path.

## Remaining notes for the company (behavioral — NOT code conflicts)

1. **Parent-request status rollup at submit.** On approval the company's action calls its own
   `_update_parent_request_datasheet_status` (rolls the parent EMCRequest up correctly). At the
   *submit* step the package cannot call that helper (it's nested in `create_app()`), so the
   parent request's status isn't updated until approval. If you want the parent to flip to
   "Peer Review" the moment a datasheet is sent, expose that helper at module level.
2. **Template source path.** `datasheet_gen/registry.py` reads `DATASHEET_SRC_DIR` (env,
   defaulting to a sibling `Reference/` folder) instead of a hardcoded `D:/THERMO/...` path. Used
   only to REBUILD templates (`spec_build.py`); runtime uses the bundled
   `datasheet_gen/word_templates/`.
3. **DB credentials.** The company `mysql_config.py` (unchanged here) hardcodes internal DB
   fallbacks; the local `.env` overrides them. Flagged as a pre-existing concern — not modified.

## Run locally

```powershell
cd "Merged Work"
py -m venv .venv                                            # first time
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# create DB test_plan_generator_merged + import data, then:
.\.venv\Scripts\python.exe init_db.py                       # first run on an empty DB
.\.venv\Scripts\python.exe app.py                           # http://localhost:3000
```
This copy's `.env` uses database `test_plan_generator_merged` and the app serves on **port 3000**.

## For the actual git merge into the company repo

This is an **additive** merge: bring the `datasheet_gen/` directory (replacing the company's),
plus `unsubmit.py` and `sample_harmonic_data.csv`. Do **not** touch `app.py` / `models.py`. The
only thing that could conflict is if the company modified `datasheet_gen/` after the fork —
reconcile that inside the package only.
