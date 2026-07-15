# Local Setup Guide — EMC Test Workflow App

This guide brings the application up **from a clean clone on any machine**, using Docker.
It is written so that **either a developer or an AI coding agent (e.g. Claude Code)** can
follow it top‑to‑bottom and self‑correct using the Troubleshooting section.

> TL;DR
> ```bash
> docker compose up -d --build           # start MySQL + the Flask app
> docker compose exec web python seed.py # load login accounts + sample data
> # open http://localhost:5000  → log in: admin@local.test / Password@123
> ```

---

## 0. Notes for an AI agent

- The **only hard prerequisite is Docker Desktop running**. MySQL and Python run inside containers — do **not** install MySQL or pip packages on the host.
- Run every command from the **project root** (the directory that contains `docker-compose.yml`).
- Steps are ordered. If a step errors, find the symptom in **§7 Troubleshooting** before retrying.
- Do not "fix" things by editing `app.py`; the Docker setup already works around its quirks via helper scripts (explained in §4). Changing them is usually the wrong move.
- This is a **local/dev** setup (Flask debug server). Not for production.

---

## 1. Prerequisites

| Tool | Why | Check |
|---|---|---|
| **Docker Desktop** (running) | runs MySQL + the app | `docker version` shows a **Server** version |
| **git** | clone the repo | `git --version` |

> If `docker` commands hang, Docker Desktop's engine isn't fully started yet — wait for the whale icon to go solid, then retry.

---

## 2. Get the code

```bash
git clone <YOUR_REPO_URL>
cd <repo>            # cd into the folder that contains docker-compose.yml
```

---

## 3. Start the application

**Option A — two commands (recommended, explicit):**
```bash
docker compose up -d --build               # build + start db and web
docker compose exec web python seed.py     # seed login accounts + sample data
```

**Option B — one‑shot helper (does both, with retries):**
- Windows (PowerShell): `./setup.ps1`
- macOS / Linux: `./setup.sh`

Then open **http://localhost:5000**.

To watch it boot: `docker compose logs -f web` — success looks like:
```
Database is ready (after N attempt(s)).
Schema ready.
 * Running on http://0.0.0.0:5000
```

---

## 4. What happens on startup (and why the helper files exist)

`docker compose up` starts two services (see `docker-compose.yml`):

- **`db`** — MySQL 8. Auto‑creates database `test_plan_generator` (user `root`, password `Thermo@123`). Data persists in the `db_data` volume.
- **`web`** — the Flask app. Its command is `wait_for_db.py → init_db.py → app.py`:
  1. **`wait_for_db.py`** — blocks until MySQL accepts a real connection (MySQL's first‑run init briefly passes the healthcheck before the server is actually ready).
  2. **`init_db.py`** — creates all tables **once**, in a single process (the app runs Flask's debug reloader, which would otherwise run `create_all()` in two processes at once and crash a fresh DB with MySQL error 1684 "concurrent DDL").
  3. **`app.py`** — serves on port 5000.

Other helpers:
- **`sitecustomize.py`** — makes PyMySQL satisfy the app's `mysql+mysqldb://` driver (so the `mysqlclient` C‑extension isn't needed). It auto‑loads because the Dockerfile sets `PYTHONPATH=/app`.
- The Dockerfile also `pip install cryptography` — required by PyMySQL for MySQL 8's `caching_sha2_password` auth.

---

## 5. Login accounts (from `seed.py`)

All seeded passwords: **`Password@123`**. The login **Username** field accepts the **email or the short username**.

| Username | Email | Role |
|---|---|---|
| `admin` | `admin@local.test` | admin |
| `engineer1`, `engineer2` | `engineer1@local.test`, … | lab_engineer |
| `requester1`, `requester2` | `requester1@local.test`, … | user |
| `inactive` | `inactive@local.test` | user *(deactivated — to test the rejected‑login path)* |

`seed.py` is idempotent and also seeds a few sample equipment rows.

---

## 6. (Optional) Load a real MySQL dump

If you have a `mysqldump` `.sql` of this app's database and want to test against real data:

```bash
# 1) stop the app so it doesn't write during the import
docker compose stop web

# 2) reset the database to a clean state
docker compose exec -T db mysql -uroot -pThermo@123 \
  -e "DROP DATABASE IF EXISTS test_plan_generator; CREATE DATABASE test_plan_generator CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 3) import (innodb_strict_mode OFF: the wide legacy table iec_emc_test_requests,
#    170+ columns, otherwise hits MySQL row-size ERROR 1118)
{ echo "SET SESSION innodb_strict_mode=OFF;"; cat ./Dump20260624.sql; } \
  | docker compose exec -T db mysql -uroot -pThermo@123 test_plan_generator

# 4) start the app again
docker compose start web
```

Dumped passwords are **one‑way scrypt hashes** (unrecoverable). To log in, set a known
password on every user:
```bash
docker compose exec web python set_test_passwords.py        # sets Password@123 on all users
```
> In some dumps the `users.username` column holds a person's **full name** (with a space),
> so log in with the **email**.

---

## 7. Troubleshooting (symptom → fix)

| Symptom in logs / output | Cause | Fix |
|---|---|---|
| `docker ...` hangs | Docker engine still starting | Wait for Docker Desktop to be fully up, retry |
| `No module named 'MySQLdb'` | driver shim not loaded | Ensure `PYTHONPATH=/app` (Dockerfile) and `sitecustomize.py` present; rebuild |
| `'cryptography' package is required ... caching_sha2_password` | missing dep | It's installed in the image — `docker compose build --no-cache web` |
| `Can't connect to MySQL ... Connection refused` on first boot | DB still initializing | Expected; `wait_for_db.py` retries. Just wait |
| MySQL `ERROR 1684 ... concurrent DDL` | two `create_all()` at once on empty DB | `init_db.py` prevents this — don't remove it from the Dockerfile CMD |
| `KeyError: 'WERKZEUG_SERVER_FD'` | someone set `WERKZEUG_RUN_MAIN=true` | **Remove** that env var — it's the wrong way to disable the reloader |
| MySQL `ERROR 1118 ... Row size too large` (during dump import) | strict mode on | Import with `SET SESSION innodb_strict_mode=OFF;` (see §6) |
| `service "web" is not running` | web container crashed | `docker compose logs web` to see the real error |
| Port `5000` or `3307` already in use | another stack is using it | Stop the other stack, or change the host port in `docker-compose.yml` |

Reset everything (⚠️ wipes the database):
```bash
docker compose down -v && docker compose up -d --build && docker compose exec web python seed.py
```

---

## 8. Common commands

```bash
docker compose logs -f web                  # tail app logs
docker compose exec web python seed.py      # (re)seed accounts + sample data
docker compose exec web python set_test_passwords.py   # reset all user passwords
docker compose exec db mysql -uroot -pThermo@123 test_plan_generator   # SQL shell
docker compose down                          # stop
docker compose down -v                       # stop + wipe DB volume
docker compose up -d                         # start again (no rebuild)
```

---

## 9. Configuration

Set via env in `docker-compose.yml` (read by `mysql_config.py`):

| Var | Default | Meaning |
|---|---|---|
| `MYSQL_HOST` | `db` | DB hostname (the compose service) |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `root` / `Thermo@123` | DB credentials |
| `MYSQL_DATABASE` | `test_plan_generator` | database name |
| `APP_ENV` | `development` | config profile |

Host ports: app `5000:5000`, MySQL `3307:3306` (host 3307 avoids clashing with a local MySQL).

---

## 10. About `node_modules` / Tailwind CSS

`node_modules` is **only** Tailwind build tooling — **not needed to run the app**. The compiled
`static/css/output.css` is committed and served directly. You only need Node if you change
styles:
```bash
npm install
npm run build:css     # rebuild static/css/output.css
```

---

## 11. ⚠️ Before pushing to GitHub (read this)

This repo can contain **real data and secrets** — clean it up first:

1. **Add these to `.gitignore`** (they are NOT ignored by default):
   ```gitignore
   node_modules/
   outputs/
   uploads/
   *.sql
   ```
   `uploads/` and `outputs/` hold **real uploaded/generated documents**; `*.sql` would commit a database dump containing **real user data and password hashes**.
2. **Never commit a database dump** or the `db_data` volume.
3. If any of the above are already tracked, untrack them (keeps the files locally):
   ```bash
   git rm -r --cached node_modules outputs uploads
   git commit -m "Stop tracking generated/real-data folders"
   ```
4. The dev DB password (`Thermo@123`) and seed password (`Password@123`) are **local‑only defaults** — change them for any shared or real deployment, and prefer real env vars / secrets.
5. The app runs the **Flask debug server** — local development only.

---

## 12. Project map (where things live)

```
docker-compose.yml      db (MySQL) + web (Flask) services
Dockerfile              Python 3.11 image; CMD = wait_for_db -> init_db -> app
wait_for_db.py          waits for MySQL to accept connections
init_db.py              creates the schema once (avoids reloader DDL race)
sitecustomize.py        PyMySQL -> MySQLdb shim
seed.py                 seed login accounts + sample equipment (idempotent)
set_test_passwords.py   set a known password on all users (after a dump import)
setup.ps1 / setup.sh    one-shot: up + seed
app.py                  the whole Flask app (routes defined inside create_app)
models.py               SQLAlchemy models (37 tables)
auth_routes.py          login / register / password flows
utils/                  document generation, upload handling, services
templates/              Jinja HTML pages (+ static/css/output.css)
word_templates/         Word document templates
```
