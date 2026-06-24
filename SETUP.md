# Local Setup (Docker)

Spin up the full app (MySQL + Flask) and load prefilled login accounts.

## Prerequisites
- Docker Desktop installed and **running**.

## Quick start
```bash
docker compose up -d --build               # build + start MySQL and the web app
docker compose exec web python seed.py     # load login accounts + sample data
```
Then open **http://localhost:5000**.

Or use the one-shot helper (does both, with retries):
- Windows (PowerShell): `./setup.ps1`
- macOS / Linux: `./setup.sh`

## Seeded login accounts
All passwords: **`Password@123`**. The login **Username** field accepts the email **or** the short username.

| Username | Email | Role |
|---|---|---|
| `admin` | `admin@local.test` | admin |
| `engineer1` | `engineer1@local.test` | lab_engineer |
| `engineer2` | `engineer2@local.test` | lab_engineer |
| `requester1` | `requester1@local.test` | user |
| `requester2` | `requester2@local.test` | user |
| `inactive` | `inactive@local.test` | user *(deactivated — for testing login)* |

## Common commands
```bash
docker compose logs -f web                 # tail application logs
docker compose exec web python seed.py     # re-seed (idempotent)
docker compose down                        # stop containers
docker compose down -v                     # stop AND wipe the database
docker compose up -d                       # start again (no rebuild)
```

## Clean slate
```bash
docker compose down -v && docker compose up -d --build && docker compose exec web python seed.py
```

## Notes
- The database schema is created automatically on first start (SQLAlchemy `create_all`).
- `seed.py` is idempotent — running it again only adds what's missing.
- DB connection is configured via env in `docker-compose.yml` (`MYSQL_HOST=db`, user `root`, password `Thermo@123`, database `test_plan_generator`). Host port `3307` maps to MySQL for optional external access.
