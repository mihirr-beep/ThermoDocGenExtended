# Codebase Overview — EMC Test Workflow & Datasheet Generator

> Written 2026-07-02 on branch `feature/datasheet-generation`. This document explains what the
> application is, how it is structured, how data flows through it, and the exact state of the
> in-progress datasheet-generation feature — so new features can be added with full context.

---

## 1. What this application is

A **Flask + MySQL web application for Thermo Fisher's EMC (Electromagnetic Compatibility) test
lab**. It manages the complete lifecycle of an EMC test request:

```
Requester submits EMC test request  →  Admin assigns TCO number & routes it
→  Lab engineer plans tests on a calendar (planner)  →  Tests are executed
→  Engineer fills a per-test DATASHEET (generated as a Word .docx)   ← the active feature branch
→  Reports are uploaded & reviewed  →  Admin signs off  →  Request completed
```

Alongside the request workflow it also provides **equipment management** (calibration /
intermediate-check / maintenance tracking with scheduled reminder e-mails), **user management**
with three roles, and **Word document generation** from templates at several points in the flow.

The product covers 12 EMC test types (CE, RE, ESD, EFT, SURGE, CRF, HARMONIC, FLICKER, RS/RI,
PFMF/Power-Frequency, Voltage Dips, RS-Interim), aligned to IEC/EN standards such as IEC 61000-4-x
and IEC 60601-1-2.

**Version 1.0.0** (March 2026) is the production baseline; the current branch
`feature/datasheet-generation` adds in-app datasheet generation.

---

## 2. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 3.1, Flask-Login, Flask-WTF/WTForms |
| ORM / DB | SQLAlchemy (Flask-SQLAlchemy) → MySQL 8 (`test_plan_generator` DB) |
| MySQL driver | PyMySQL masquerading as MySQLdb (`sitecustomize.py` shim in Docker; also installed by `mysql_config.py` in the working tree) |
| Documents | `python-docx` (build/edit .docx), `docxtpl` (Jinja-style .docx templating), `docx2txt` + optional Spire.Doc (text extraction), Pillow (image scaling) |
| Frontend | Jinja2 templates + Tailwind CSS (pre-compiled `static/css/output.css`) + vanilla JS |
| Runtime | Docker Compose (`db` = MySQL 8 on host port 3307, `web` = Flask dev server on 5000) or bare `python app.py` with a local MySQL via `.env` |
| E-mail | SMTP relay `SMTPRELAY1.THERMOFISHER.COM:25` (assignment/submission/equipment-reminder mails) |

**Timezone:** everything runs in IST (UTC+5:30) via a shared `get_ist_now()` helper.

### Running it

```bash
# Docker (recommended; see SETUP.md for full detail & troubleshooting)
docker compose up -d --build
docker compose exec web python seed.py     # seed users + sample equipment
# open http://localhost:5000 — admin@local.test / Password@123

# Bare metal (working tree currently supports this):
# .env provides MYSQL_HOST/PORT/USER/PASSWORD; mysql_config.py now loads .env
# and installs the PyMySQL shim itself.
python app.py
```

Seeded logins (all `Password@123`): `admin`, `engineer1`, `engineer2`, `requester1`,
`requester2`, `inactive` (deactivated, for testing the rejected-login path).

Docker startup order matters: `wait_for_db.py` → `init_db.py` (creates schema once, avoiding a
Flask-reloader concurrent-DDL race) → `app.py`. Don't remove these from the Dockerfile CMD.

---

## 3. Repository map

```
├── app.py                     ★ The whole main app: create_app() factory + ~55 routes (13,262 lines)
├── models.py                  ★ 37 SQLAlchemy models
├── auth_routes.py             Login/register/password blueprint (auth_bp)
├── forms.py                   WTForms (login, registration, password flows)
├── mysql_config.py            DB/app config profiles (+ .env loader & PyMySQL shim — uncommitted)
├── datasheet_gen/             ★ ACTIVE FEATURE: datasheet generation module (see §7)
├── utils/                     Document processing/generation, upload handling, EMC request services
├── templates/                 Jinja pages (index, planner, review, equipment, users, admin_approval…)
├── word_templates/            .docx templates for the test-plan/report side (IEC-FRM-503, TRF…)
├── static/                    Compiled Tailwind CSS
├── uploads/                   Uploaded files + generated datasheets (uploads/test_datasheets/)
├── outputs/                   Generated test-plan/report documents
├── seed.py / seed_users.py    Idempotent dev seeding
├── migrate_iec_emc_to_relational.py  One-time legacy→normalized data migration
├── import_equipment_csv.py    Equipment CSV importer
├── send_equipment_reminders.py  Daily cron/Task-Scheduler entry point for reminder e-mails
├── init_db.py / wait_for_db.py / sitecustomize.py  Docker boot helpers
├── Dockerfile / docker-compose.yml / setup.ps1 / setup.sh
└── README.md / SETUP.md / RELEASE_NOTES.md / TAILWIND_SETUP.md
```

★ = the three files/dirs where almost all meaningful logic lives.

---

## 4. Data model (`models.py` — 37 tables)

### 4.1 The two generations of request storage

- **Legacy:** one wide table `iec_emc_test_requests` (~170 columns) — no longer the source of
  truth. `migrate_iec_emc_to_relational.py` copies it (additively, non-destructively) into the
  normalized schema. When importing old SQL dumps this wide table needs
  `innodb_strict_mode=OFF`.
- **Normalized (current):** `EMCRequest` (`iec_emc_requests`) as parent + child tables for every
  repeating group. Backward compatibility is preserved by `EMCRequest.to_legacy_dict()`, which
  rebuilds the legacy wide payload for existing consumers (the frontend JS still consumes this
  shape), and by `legacy_request_id` / `utils/emc_request_repository.py`, which resolves either
  ID generation.

### 4.2 Core entities

| Model (table) | Role |
|---|---|
| `User` (`users`) | Auth + roles: `user` (requester), `lab_engineer`, `admin`. Scrypt password hashes, reset tokens, `is_active`. |
| `EMCRequest` (`iec_emc_requests`) | ★ The central request: product info, requester info, lab-manager block, assignment fields, `status` workflow, `tco_id`, `job_id`. 15 cascading child collections. |
| `EMCRequestTest` (`iec_emc_request_tests`) | One row per test per request (unique on `request_id`+`test_code`); `is_selected`, planned hours/dates, per-test `assigned_engineer_id`, `workflow_status`. |
| `EMCRequestTest{CE,RE,ESD,Harmonic,Flicker,RS,RSInterim,EFT,Surge,CRF,PowerFreq,VoltageDips}` | 12 one-to-one detail tables holding test-type-specific parameters (levels, coupling, cables, field strengths…). CE additionally has child `EMCRequestTestCESignalLine` rows. |
| Child tables of `EMCRequest` | `ServiceType`, `SerialNumber`, `AdditionalModel`, `Category`, `Accessory`, `Cable`, `EUTSpec`, `SupplyVF`, `Wireless`, `ProductStandard`, `ProductEnvironment`, `DecisionRule`, `FunctionalMode` — all `cascade='all, delete-orphan'`, most with `sort_order`. |
| `PlannerEntry` (`planner_entries`) | ★ Calendar/schedule entry per test execution: engineer, TCO, test name, dates/times, recurrence, `status` (default `in_progress`), plus **datasheet upload columns** (`datasheet_file_path/_uploaded_at/_uploaded_by/_comments`, `completion_date`) and report upload + cancellation columns. This is the table the datasheet feature writes to. |
| `Equipment` / `Maintenance` / `EquipmentHistory` | Equipment registry with calibration/IC/maintenance dates, EOU status, audit history (`created/updated/deleted` with old/new values). |
| `TestRequest` / `TestPlan` / `TestDatasheet` | Older upload-driven pipeline (upload a .docx request → extract → generate plan/datasheet). Still present; largely superseded by the EMCRequest flow. |

### 4.3 Conventions

- `TimestampMixin` gives `created_at`/`updated_at` in IST.
- JSON-in-TEXT columns for flexible payloads (`extracted_data`, `generated_files`,
  `plan_update_history`…), with helper getters/setters on the models.
- LONGTEXT columns store base64 images (signatures, block diagrams).
- Status strings drive the workflow (no enum table): request statuses seen in code include
  `Draft` → `At Review` → `Test Plan To Approve` → `Test Plan Approved` →
  `Engineer Assigned for Test` / `Test Schedule In Progress` → report stages
  (`Draft Report`, `Proceed Report`, `Admin Sign Off`) → `Completed`, plus `Rejected` and
  `Need More Information`.
- Schema evolution is done via **idempotent runtime DDL helpers**, not migrations: e.g.
  `ensure_planner_table()`, `ensure_equipment_document_link_column()` in `app.py`, and
  `datasheet_gen/schema.py::ensure_datasheet_columns()` — each checks
  `information_schema` and `ALTER TABLE`s missing columns at startup. **Follow this pattern when
  adding columns.**

---

## 5. The main application (`app.py`)

A single 13,262-line file. Everything — helpers, e-mail templates, ~55 routes — is defined
**inside `create_app(config_name)`** (bottom of file instantiates and runs it). Config profiles
(`default`/`testing`/`production`) come from `mysql_config.py`, selected by `APP_ENV`/`FLASK_ENV`
(default: `testing`, which shows a NON-PRODUCTION banner via a context processor).

Blueprints registered: `auth_bp` (from `auth_routes.py`), upload blueprint (`utils/upload_routes.py`),
and `register_datasheet_gen(app)` (the datasheet module, at ~line 3384).

### 5.1 Functional areas & key routes

| Area | Representative routes |
|---|---|
| Pages | `/` (request queue), `/planner`, `/dashboard` (admin), `/review`, `/assigned-tests`, `/equipment`, `/users`, `/admin-approval`, `/help` |
| Request CRUD | `POST /api/save-draft`, `POST /create-test-plan` (create/update + TCO generation `IEC-EMC-NNN`), `GET /api/test-requests` (role-filtered list), `GET|DELETE /api/test-requests/<id>`, `PATCH .../job-number`, `GET /api/test-requests/tco/<tco_id>` (TCO lookup/prefill) |
| Assignment & planning | `POST /api/test-requests/<id>/assign-tests` (per-test engineer + dates → creates PlannerEntries), `GET /api/lab-engineers`, `/api/planner` GET/POST, `/api/planner/<id>` GET/PUT/PATCH/DELETE, `PATCH /api/planner/<id>/status`, conflict detection on create/update |
| Review & approval | `POST /api/test-requests/<id>/review`, `.../review-comment(s)`, peer review (`/api/planner/peer-review`, `.../peer-review-approve`), admin `approve|assign|reject /api/admin/*`, `POST /api/admin/test-requests/<id>/final-approval`, `.../request-plan-update`, `.../reassign-owner` |
| Reports & sign-off | `POST .../upload-report`, `GET .../view-report(-data)`, `POST .../proceed-report`, `POST .../admin-sign-off`, `POST .../admin-completed`, `GET .../download-report` |
| Document generation | `GET .../download-form-docx` (request form as Word), `POST /generate` (test plan + datasheets), `GET /download/<filename>`, `POST /generate-surge-datasheet`, `POST /upload-test-datasheet` (older manual upload path) |
| Equipment | `/equipment` page; `/api/equipment` CRUD + `/search` + `/<id>/history` |
| Users (admin) | `/users` page; `/api/users/<id>` GET/PUT/DELETE, `/role`, `/status` |

### 5.2 Cross-cutting logic in app.py worth knowing

- **Planner conflict detection** (~lines 267–453): builds a snapshot of overlapping entries;
  blocks same-engineer double booking and test-capacity conflicts; normalizes test names
  (e.g. "Conducted Emission" → `CE`, "Radiations Susceptibility" → `RS_RI`) before comparing.
- **E-mail notifications** (~lines 1039–3283): HTML mails on submission, assignment, status
  changes; equipment calibration/IC/maintenance reminders (EOU: 60/30/15/7 days before due;
  non-EOU: 30/15/7) — triggered daily by `send_equipment_reminders.py`.
- **Word export builders** (~lines 5101–6600): `_build_test_request_word_export*()` fill
  `word_templates/*.docx` (IEC-FRM-503 test plan, TRF Rev1) with request data, including image
  embedding of block diagrams/signatures and table-row repetition.
- **Validation rules**: required product/requester fields; "RS Interim" only allowed with
  IEC 60601-1-2; "Development Assistance" service requires per-test hours; dimensions normalized
  to mm.
- **Role-based visibility** on every list endpoint: admin sees all, lab engineers see their
  assignments, requesters see their own requests/TCOs.

---

## 6. Supporting modules

| Module | Purpose |
|---|---|
| `auth_routes.py` + `forms.py` | Login (username **or** email), registration restricted to `@thermofisher.com` (username derived from the e-mail local part), password strength rules (8–128, upper/lower/digit/special), reset-token flow, change-password, session rotation on login, `session_protection='strong'`. |
| `utils/document_processor.py` | Extract text from uploaded .docx (docx2txt → python-docx → optional Spire.Doc) and pattern-match structured fields out of it. |
| `utils/enhanced_document_processor.py` | Same goal, Spire.Doc-first, plus `populate_iec_template()` to fill the IEC-FRM-503 plan. |
| `utils/document_generator.py` | Creates default test-plan/datasheet .docx templates with `{{PLACEHOLDER}}` tokens and fills them. |
| `utils/normalized_emc_request_service.py` | **Write path** for the normalized schema: `TEST_CODE_MAP` normalizes 40+ test-name variants to canonical codes; parses repeating rows; persists EMCRequest + children. |
| `utils/emc_request_repository.py` | **Read path**: resolve by normalized id, legacy id, or TCO; returns legacy-shaped payload dicts. |
| `utils/upload_handler.py` / `upload_routes.py` | Validated file uploads (50 MB cap, extension/MIME whitelist, malicious-content scan, unique filenames) → creates `TestRequest` rows. |
| `utils/equipment_manager.py` | Legacy JSON-file equipment store; superseded by MySQL but still referenced. |

---

## 7. The `datasheet_gen` module (the active feature branch) ★

**Goal:** when a lab engineer executes a scheduled test (a `PlannerEntry`), they open a web form
for that test type, fill in observations/measurements/photos, and the app generates the official
IEC-FRM-50x **Test Data Sheet** as a .docx, stores it against the planner entry, and marks the
assignment `datasheet_uploaded`.

### 7.1 Two-tier design

1. **Bespoke CE engine** (`routes.py`, `service.py`, `generator.py`, `templates/datasheet_gen/ce_form.html`,
   `word_templates/IEC-FRM-504_CE.docx`): hand-built, document-faithful form and template for the
   Conducted Emission datasheet (IEC-FRM-504) — line/neutral plots, QP/Avg limit tables,
   modification & equipment record grids, 4 image uploads, signature.
2. **Generic schema-driven engine** (`generic_routes.py`, `generic_service.py`,
   `generic_generator.py`, `templates/datasheet_gen/generic_form.html`) serving the **other 10
   tests** from per-test JSON schemas + auto-built docxtpl templates.

`registry.py` is the single source of truth: `REGISTRY` maps
`planner_entries.test_name.upper()` → (form number, display name, source .docx filename).
CE = IEC-FRM-504 … PFMF = IEC-FRM-514. `GENERIC_CODES` = all except CE.
Source documents live **outside the repo** at `D:/THERMO/DocGenerator/OneDrive_1_22-6-2026/`
(`SRC_DIR` constant — matters only for rebuilding templates, not at runtime).

### 7.2 Runtime data flow

```
GET /datasheet/ce/<assignment_id>/form           GET /datasheet/g/<CODE>/<assignment_id>/form
        │ prefill from EMCRequest + PlannerEntry (job number→tco, EUT name/model/serial,
        │ product standard, supply V/F, tested_by, schema field defaults…)
        ▼
   ce_form.html                                   generic_form.html (renders schema sections)
        │ user fills form, uploads images/CSV
        ▼
POST /datasheet/ce/generate                      POST /datasheet/g/<CODE>/generate
        │ build docxtpl context (scalars, table rows via <table>__<col>[] inputs,
        │ InlineImage with aspect-fit scaling; date fields validated not-in-future)
        ▼
docxtpl render of word_templates/{IEC-FRM-504_CE|CODE}.docx
        ▼
uploads/test_datasheets/{TCO}_{CODE}_{timestamp}.docx  (+ .docx.json metadata, images/ subdir)
        ▼
PlannerEntry updated: datasheet_file_path/_uploaded_at/_uploaded_by/_comments,
completion_date, status='datasheet_uploaded'
        ▼
GET /datasheet/[ce|g]/<assignment_id>/download
```

**Access control** on every route: `admin` or `lab_engineer` only; a lab engineer can only touch
assignments where `engineer_user_id` is theirs (or unset).

**DB bootstrap:** `schema.py::ensure_datasheet_columns()` idempotently ALTERs
`planner_entries` to add datasheet/report/cancel columns at startup (registered from
`register_datasheet_gen()` in `__init__.py`, called at app.py:3384).

### 7.3 JSON schema format (`schemas/{CODE}.json`)

```jsonc
{ "code": "EFT", "name": "Electrical Fast Transient", "form": "IEC-FRM-508",
  "sections": [ { "title": "EUT DETAILS", "items": [
    { "type": "fields",  "fields": [ {"key","label","input":"text|image","default"} ] },
    { "type": "field",   "key": "sop_reference", "label": "…" },
    { "type": "textarea","key": "test_procedure", "label": "…" },
    { "type": "image",   "key": "img_photo_1",   "label": "Photo 1: …" },
    { "type": "table",   "key": "…_rows", "columns": [{"key":"c0","label":"…"}, …],
                          "rows": [ /* optional pre-filled starter rows */ ] },
    { "type": "static_table", "label": "…" }   // read-only placeholder, filled manually in Word
  ] } ] }
```

`generic_form.html` renders these item types directly (date inputs inferred from key names,
`+Add Row` and CSV import for tables). `generic_service.build_context()` turns the POST back
into a docxtpl context; `iter_scalar_fields()`/`image_keys()` are the schema-walking helpers.

### 7.4 Build-time pipeline (`spec_build.py`) — how templates & schemas are made

`spec_build.py` reads each official Word datasheet from `SRC_DIR` and **auto-converts** it into
(a) a docxtpl template in `datasheet_gen/word_templates/` and (b) the matching JSON schema:

- classifies each table as **kv** (label→value rows → `{{ placeholders }}` + `fields` items) or
  **loop** (data grid → `{%tr for r in key%}` rows + `table` item);
- handles special paragraphs (functional check → `sop_reference`, DEVIATION / TEST PROCEDURE /
  MONITORING PARAMETERS headings → textareas, `<<…>>` markers and "Photo N:" captions → image
  placeholders); strips manual page breaks;
- tables it can't safely templatize (heavily merged headers) become `static_table` placeholders.

`build_ce_template.py` is the manual equivalent that produced the CE template.

**Regenerating:** run `spec_build.py` (needs the OneDrive source docs present at `SRC_DIR`);
it overwrites templates + schemas. Any hand-edits to schemas/templates after generation would be
lost, so treat generator + outputs as a unit.

### 7.5 Branch state: committed vs **uncommitted work in progress** (as of 2026-07-02)

Committed history on this branch:
- `b1ee936` finished the 10 generic tests end-to-end;
- `3e3bc40` / `b7a45eb` / `445f2e1` converted the TEST OBSERVATION grids (ESD ×3, EFT, SURGE,
  RS_RI, PFMF) and the HARMONIC results grid into **editable row-loop `table` items** (HARMONIC
  with 40 pre-filled order rows).

**The working tree then changed direction (uncommitted, ~4,100 added lines):** `spec_build.py`
was reworked to detect observation grids (`is_observation_grid()`: header cells like `+2/-2/+4`)
and flatten them into **per-cell scalar `fields`** instead of row-loop tables — each cell becomes
its own field, keyed by row label + column header (e.g. `f_0_5_common_mode_l_pe_0`,
"+0.5 – Common Mode L→PE 0°"), with the **original cell text captured as `"default"`**. It also
gained proper merged-cell handling (`cell._tc` identity), header-row skipping,
serial-numbered-row labeling, and multi-value-column splitting. All 10 schemas and .docx
templates were regenerated this way (e.g. ESD observation = 48+18+18 fields; SURGE = 2×128
fields; **HARMONIC's results grid is currently back to `static_table`**).
Matching uncommitted support changes: `generic_service.collect_prefill()` now honors schema
`"default"` values, and `mysql_config.py` loads `.env` + installs the PyMySQL shim so the app
runs outside Docker.

⚠️ Implication for new work: decide whether the per-cell-fields approach (working tree) or the
row-loop-table approach (HEAD) is the way forward before building on the observation grids —
they are mutually exclusive representations, and HARMONIC currently regressed to read-only under
the working-tree version.

### 7.6 Demo data

`datasheet_gen/seed_datasheet_demo.py` (idempotent, `--reset` supported) creates 3 requests
(TCO-2026-001/002/003) covering all 11 test codes as planner assignments split between
engineer1/engineer2 — the quickest way to get clickable datasheet forms after seeding.

---

## 8. Conventions & gotchas (read before changing things)

1. **`app.py` is a monolith** — all routes live inside `create_app()`. New self-contained
   features should follow the `datasheet_gen` pattern instead: own package + blueprint +
   one-line registration in `app.py` + idempotent DDL bootstrap in the package.
2. **No migration framework.** Schema changes = idempotent `ensure_*` helpers run at startup
   (information_schema check → `ALTER TABLE`). Never assume Alembic.
3. **Legacy compatibility is load-bearing.** Frontend JS consumes legacy-shaped payloads from
   `EMCRequest.to_legacy_dict()`; ID resolution must go through
   `utils/emc_request_repository.py` (normalized id first, then `legacy_request_id`).
4. **Test-name normalization is everywhere** (planner conflict keys, registry codes,
   `TEST_CODE_MAP`). A planner entry's `test_name` uppercased must match a `REGISTRY` code for
   datasheet routes to work.
5. **IST timezone** is hardcoded app-wide (`get_ist_now()`); don't introduce naive UTC.
6. **Templates are huge server-rendered pages** with inline JS (index.html ≈ 1.1 MB,
   assigned_test.html ≈ 868 KB). There is no JS build pipeline besides Tailwind CSS
   (`npm run build:css` only when styles change; compiled CSS is committed).
7. **Secrets/dev credentials** are local-only defaults (`Thermo@123`, `Password@123`,
   registration domain lock to `@thermofisher.com`, SMTP relay hardcoded). `uploads/`,
   `outputs/`, `*.sql` must never be committed (see SETUP.md §11).
8. **Windows paths appear in code** (`registry.SRC_DIR` = `D:/THERMO/...`) — only needed for
   template rebuilds, but be aware when running elsewhere.
9. **Generated artifacts in repo root** (`output_file.docx`, `measurement_data_example.csv`) are
   scratch/test outputs, currently untracked.
10. **docxtpl templates + JSON schemas are generated artifacts** of `spec_build.py` /
    `build_ce_template.py`; regenerate rather than hand-edit where possible, and keep schema ↔
    template ↔ form-renderer in sync (`table` item ⇔ `{%tr%}` loop ⇔ `<key>__<col>[]` inputs).

---

## 9. Quick pointers for adding features

- **New API/page in the workflow:** find the closest existing route in `app.py` (grep the route
  table in §5.1), mind role checks and status strings.
- **New datasheet test type:** add to `REGISTRY`, drop the source .docx in `SRC_DIR`, run
  `spec_build.py`, verify the generated schema/template, seed an assignment, test
  `/datasheet/g/<CODE>/<id>/form`.
- **New field on an existing datasheet:** edit the test's JSON schema **and** its .docx template
  together (placeholder must exist in both), or extend `spec_build.py` and regenerate.
- **New column on a table:** add to `models.py` **and** to the relevant `ensure_*` bootstrap.
- **Anything user-facing:** check all three roles — most bugs here are role-visibility bugs.
