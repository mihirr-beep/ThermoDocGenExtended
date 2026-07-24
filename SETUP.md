# Local Setup Guide — EMC Test Workflow & Datasheet Generator

Brings the app up **from a clean checkout**, running **bare-metal** (Python + a local MySQL 8
server). No Docker. Written for Windows (PowerShell); macOS/Linux differences are noted inline.

> TL;DR (after the one-time setup in §2–§5)
> ```powershell
> .\.venv\Scripts\python.exe app.py      # serve on http://localhost:3000
> ```

---

## 1. Prerequisites

| Tool | Why | Check |
|---|---|---|
| **Python 3.11+** | runs the app (3.13 works — all deps ship wheels for it) | `py --version` |
| **MySQL Community Server 8.0** | the database | `Get-Service MySQL80` shows **Running** |

Install MySQL via the official **MySQL Installer** ("Server only"). Set the **root password** and
let it configure MySQL as a **Windows service** (default `MySQL80`, port `3306`). The app's `.env`
below uses root password **`Thermo@123`** — match it, or set your own and edit `.env`.

> Datasheet **generation** reads its source `.docx` templates from `datasheet_gen/word_templates/`
> (bundled, so runtime needs nothing extra). Only *rebuilding* templates via `spec_build.py` needs
> the original source docs — point `DATASHEET_SRC_DIR` at them if you do that.

---

## 2. Python environment

From the project root (the folder with `app.py`):

```powershell
py -m venv .venv                                  # macOS/Linux: python3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`PyMySQL` + `cryptography` (in `requirements.txt`) cover MySQL 8's `caching_sha2_password` auth,
so the `mysqlclient` C extension is **not** needed. `mysql_config.py` registers the
PyMySQL→MySQLdb shim itself at import.

---

## 3. Create the database

The app needs an **empty** database named `test_plan_generator`; it creates its own tables.

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -uroot -p `
  -e "CREATE DATABASE IF NOT EXISTS test_plan_generator CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

---

## 4. Configure `.env`

Create `.env` in the project root (it's gitignored). `mysql_config.py` reads it and its values
**override** the hardcoded fallbacks in that file:

```dotenv
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=Thermo@123
MYSQL_DATABASE=test_plan_generator
APP_ENV=development
```

(No `python-dotenv` needed — ignore Flask's "install python-dotenv" tip.)

---

## 5. Create tables + load data

**On a fresh/empty DB, create the schema once before the first run:**
```powershell
.\.venv\Scripts\python.exe init_db.py            # prints "Schema ready."
```
> Why once, separately: the Flask debug reloader loads `app.py` in two processes; on an empty DB
> their two `create_all()` calls race and fail with MySQL error 1684 ("concurrent DDL"). Doing it
> first makes the app's `create_all()` a no-op. It also creates the datasheet + peer-review tables.

**Then load data — pick one:**

*Sample/dev data:*
```powershell
.\.venv\Scripts\python.exe seed.py               # login accounts + sample equipment
```
Seeded accounts (all password **`Password@123`**): `admin@local.test`, `engineer1@local.test`,
`engineer2@local.test`, `requester1@local.test`, `requester2@local.test`, `inactive@local.test`.

*Real `mysqldump`:* reset the DB and import (the wide legacy table `iec_emc_test_requests` needs
`SET SESSION innodb_strict_mode=OFF;` — put it as the dump's first line), then run
`set_test_passwords.py` (dump passwords are unrecoverable scrypt hashes → sets `Password@123` on all
users; log in with the **email**, since `username` may hold a full name with spaces).

---

## 6. Run

```powershell
.\.venv\Scripts\python.exe app.py
```
Open **http://localhost:3000**. Stop with **Ctrl+C**. MySQL keeps running as a service, so
day-to-day you only start the app.

---

## 7. The datasheet + peer-review flow

Lab engineers fill a per-test **datasheet** from **Assigned Tests** and click **Send to Peer
Review** (picking a reviewer). That generates the `.docx`, sets the planner entry to
`Peer Review`, and routes it into the peer-review queue. A reviewer **Approves** (→
`datasheet_uploaded`) or **Rejects** (→ back to the engineer). After approval the engineer clicks
**Generate Final Datasheet** to produce the official `.docx`. See `MERGE_NOTES.md` for details.

---

## 8. Troubleshooting (symptom → fix)

| Symptom | Cause | Fix |
|---|---|---|
| `Access denied for user 'root'@'localhost'` | wrong password | Make `.env` `MYSQL_PASSWORD` match your MySQL root password |
| `Can't connect to MySQL server` | service not running | `Start-Service MySQL80` |
| `Unknown database 'test_plan_generator'` | DB not created | Do §3 |
| MySQL `ERROR 1684 ... concurrent DDL` | two `create_all()` at once on empty DB | Run `python init_db.py` once before `app.py` (§5) |
| MySQL `ERROR 1118 ... Row size too large` (dump import) | strict mode on | Prepend `SET SESSION innodb_strict_mode=OFF;` to the dump |
| `No module named 'MySQLdb'` | deps not installed / wrong interpreter | Install `requirements.txt` into `.venv`; run via `.\.venv\Scripts\python.exe` |
| Port `3000` already in use | stale instance / another app | Stop the other process, or change the port at the bottom of `app.py` |

---

## 9. Configuration reference

Read by `mysql_config.py` from `.env` (or process env; `.env` overrides the in-file fallbacks):

| Var | Meaning |
|---|---|
| `MYSQL_HOST` / `MYSQL_PORT` | DB host / port (`localhost` / `3306`) |
| `MYSQL_USER` / `MYSQL_PASSWORD` | DB credentials (`root` / `Thermo@123`) |
| `MYSQL_DATABASE` | database name (`test_plan_generator`) |
| `APP_ENV` | config profile (`development`) |
| `DATASHEET_SRC_DIR` | source `.docx` templates for *rebuilding* datasheet templates (optional) |

---

## 10. About `node_modules` / Tailwind CSS

`node_modules` is **only** Tailwind build tooling — **not needed to run the app**. The compiled
`static/css/output.css` is committed and served directly. You only need Node to change styles
(`npm install` then `npm run build:css`). See `TAILWIND_SETUP.md`.

---

## 11. ⚠️ Before pushing to GitHub

Keep out of git (already in `.gitignore`): `.env`, `.venv/`, `uploads/`, `outputs/`, and any
`*.sql` dump (real user data + password hashes). The dev password (`Thermo@123`) and seed password
(`Password@123`) are **local-only defaults** — change them for any shared/real deployment. The app
runs the **Flask debug server** — local development only.
