# AGENTS.md — Agent Guidance for ThermoDocGenExtended

## Quick Start
```bash
# Docker (recommended; see SETUP.md)
docker compose up -d --build
docker compose exec web python seed.py
# http://localhost:5000  →  admin@local.test / Password@123

# Bare metal (requires MySQL + .env)
python app.py
```

## Architecture at a Glance
- **Backend**: Flask 3.1 (monolithic `app.py` ~13k lines) + SQLAlchemy + MySQL 8
- **Frontend**: Jinja2 templates + Tailwind CSS (pre-compiled `static/css/output.css`)
- **Database**: MySQL `test_plan_generator` (37 tables, legacy + normalized schemas)
- **Auth**: Flask-Login, 3 roles (`user`/`lab_engineer`/`admin`), scrypt hashes
- **Docs**: `python-docx` + `docxtpl` for Word generation
- **Timezone**: IST (UTC+5:30) everywhere via `get_ist_now()`

## Key Entry Points
| Area | File/Module |
|------|-------------|
| App factory & routes | `app.py:create_app()` |
| Models (37 tables) | `models.py` |
| Auth blueprint | `auth_routes.py` + `forms.py` |
| Datasheet generation (active feature) | `datasheet_gen/` package |
| Utilities | `utils/` (doc gen, upload, EMC services) |

## Developer Commands
```bash
# Docker
docker compose logs -f web                    # tail logs
docker compose exec web python seed.py        # seed users + sample data
docker compose exec web python set_test_passwords.py  # reset all passwords
docker compose exec db mysql -uroot -pThermo@123 test_plan_generator  # SQL shell
docker compose down -v                        # wipe DB volume

# Bare metal
pip install -r requirements.txt
npm install && npm run build:css              # only if changing Tailwind styles
python seed.py                                # seed dev data
```

## Testing / Verification
No formal test suite. Manual verification steps:
1. `docker compose exec web python seed.py` → verify login works
2. Open `/planner` → create assignment → open datasheet form → generate docx
3. Check `outputs/` and `uploads/test_datasheets/` for generated files

## Architecture Conventions (Must Follow)
1. **No migration framework** — schema changes = idempotent `ensure_*()` helpers at startup (check `information_schema` → `ALTER TABLE`). See `datasheet_gen/schema.py:ensure_datasheet_columns()` and `app.py` helpers.
2. **Monolith pattern** — new self-contained features should follow `datasheet_gen`: own package + blueprint + one-line registration in `app.py` + bootstrap DDL.
3. **Legacy compatibility** — normalized `EMCRequest` still exposes `to_legacy_dict()` for frontend JS. ID resolution goes through `utils/emc_request_repository.py`.
4. **Test-name normalization** — planner conflict keys, registry codes, `TEST_CODE_MAP` all expect uppercase canonical codes (CE, RE, ESD, EFT, SURGE, CRF, HARMONIC, FLICKER, RS_RI, PFMF, VOLTAGEDIPS, RS_INTERIM).
5. **IST timezone** — never introduce naive UTC; use `get_ist_now()`.
6. **Role checks on every route** — admin sees all, lab engineers see own assignments, requesters see own requests.

## Datasheet Generation Module (`datasheet_gen/`)
- **Two-tier**: bespoke CE engine (`routes.py`, `service.py`, `generator.py`) + generic schema-driven engine for other 10 tests (`generic_routes.py`, `generic_service.py`, `generic_generator.py`)
- **Registry** (`registry.py:REGISTRY`) maps planner `test_name.upper()` → (form number, display name, template file). CE = IEC-FRM-504; others IEC-FRM-505…514.
- **Schema format** (`schemas/{CODE}.json`): sections with items of type `fields|field|textarea|image|table|static_table`. Table items → `{%tr for r in key%}` loops in docxtpl; form inputs named `key__col[]`.
- **Bootstrap**: `schema.py::ensure_datasheet_columns()` adds datasheet/report/cancel columns to `planner_entries` at startup.
- **Seed demo**: `datasheet_gen/seed_datasheet_demo.py` creates 3 requests (TCO-2026-001/002/003) with all 11 test assignments.

## Environment & Secrets
- Docker Compose sets: `MYSQL_HOST=db`, `MYSQL_PASSWORD=Thermo@123`, `APP_ENV=development`
- `.env` (bare metal) mirrors these; `mysql_config.py` loads it and installs PyMySQL shim
- **Never commit**: `uploads/`, `outputs/`, `*.sql`, `node_modules/` (see SETUP.md §11)

## Common Gotchas
| Issue | Cause | Fix |
|-------|-------|-----|
| `No module named 'MySQLdb'` | PyMySQL shim not loaded | Ensure `PYTHONPATH=/app` and `sitecustomize.py` present (Dockerfile handles) |
| `ERROR 1684 concurrent DDL` | Flask reloader runs `create_all()` twice | Keep `init_db.py` in Dockerfile CMD |
| `Row size too large` on dump import | `innodb_strict_mode=ON` | Import with `SET SESSION innodb_strict_mode=OFF;` |
| Port 5000/3307 in use | Another stack running | Stop other stack or change ports in `docker-compose.yml` |
| `WERKZEUG_SERVER_FD` KeyError | `WERKZEUG_RUN_MAIN=true` set | Remove that env var |

## Adding a New Datasheet Test Type
1. Add entry to `datasheet_gen/registry.py:REGISTRY`
2. Drop source .docx in `SRC_DIR` (`D:/THERMO/DocGenerator/OneDrive_1_22-6-2026/`)
3. Run `python datasheet_gen/spec_build.py` → generates schema + docxtpl template
4. Verify generated `schemas/{CODE}.json` and `word_templates/{CODE}.docx`
5. Seed an assignment via `seed_datasheet_demo.py` or planner UI
6. Test `/datasheet/g/{CODE}/<assignment_id>/form`

## Adding a Database Column
1. Add column to model in `models.py`
2. Create `ensure_<table>_<column>()` helper (check `information_schema.columns` → `ALTER TABLE`)
3. Call helper from `create_app()` in `app.py` or package bootstrap (e.g., `datasheet_gen/__init__.py`)

## References
- `SETUP.md` — full Docker setup + troubleshooting
- `CODEBASE_OVERVIEW.md` — detailed architecture, data model, module map
- `README.md` — user-facing features & setup
- `RELEASE_NOTES.md` — version history