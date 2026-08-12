# -*- coding: utf-8 -*-
"""Hit every GET page in the app and report what breaks.

Exercises the real WSGI stack - routing, permissions, queries, templates - so
it catches the failures that reading code does not: a template referencing a
column that moved, a query that only breaks when a table is empty, a page that
500s for one role and not another.

Authentication is done by planting the user id in the session, the way Flask-
Login itself does. No password is typed anywhere.

Parameterised routes get a REAL id pulled from the database, because the
interesting failures need real rows: <int:planner_id> filled with 1 mostly
proves that 404 works.

    python tools_smoke.py                 # admin
    python tools_smoke.py --role lab_engineer
"""
import argparse
import os
import re
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module                                    # noqa: E402
from models import db, User                                 # noqa: E402
from sqlalchemy import text                                 # noqa: E402

# Where to find a real value for each route argument. Anything not listed here
# is reported as skipped rather than guessed - a made-up id tests the 404 path
# and nothing else.
ARG_SQL = {
    "planner_id": "SELECT id FROM planner_entries ORDER BY id LIMIT 1",
    "assignment_id": "SELECT planner_entry_id FROM datasheet_records ORDER BY id LIMIT 1",
    "entry_id": "SELECT id FROM planner_entries ORDER BY id LIMIT 1",
    "request_id": "SELECT id FROM iec_emc_requests ORDER BY id LIMIT 1",
    "req_id": "SELECT id FROM iec_emc_requests ORDER BY id LIMIT 1",
    "equipment_id": "SELECT id FROM equipment ORDER BY id LIMIT 1",
    "user_id": "SELECT id FROM users ORDER BY id LIMIT 1",
    "test_id": "SELECT id FROM iec_emc_request_tests ORDER BY id LIMIT 1",
    "datasheet_id": "SELECT id FROM `datasheet` ORDER BY id LIMIT 1",
    "id": "SELECT id FROM iec_emc_requests ORDER BY id LIMIT 1",
}
ARG_CONST = {"code": "crf", "key": "eut_photo", "filename": "none.docx",
             "tco_id": "IEC-EMC-004"}


def resolve_args(rule, app):
    """Real values for every argument, or None when one cannot be sourced."""
    out = {}
    with app.app_context():
        for name in rule.arguments:
            if name in ARG_CONST:
                out[name] = ARG_CONST[name]
                continue
            sql = ARG_SQL.get(name)
            if not sql:
                return None, name
            try:
                v = db.session.execute(text(sql)).scalar()
            except Exception:
                v = None
            if v is None:
                return None, name
            out[name] = v
    return out, None


# A page can answer 200 and still be broken - a template that swallows an
# exception into the body, a JSON endpoint reporting success=false, an empty
# table where rows were expected. Status codes do not see any of it.
_ERROR_MARKERS = (
    "Traceback (most recent call last)", "jinja2.exceptions",
    "UndefinedError", "sqlalchemy.exc", "OperationalError",
    "ProgrammingError", "Internal Server Error",
    "\"success\": false", "'success': False",
)


def _renders_error(body):
    low = (body or "")
    for marker in _ERROR_MARKERS:
        if marker in low:
            i = low.find(marker)
            return low[max(0, i - 40):i + 120].replace("\n", " ")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="admin")
    args = ap.parse_args()

    app = app_module.create_app("default")
    app.config["WTF_CSRF_ENABLED"] = False
    app.login_manager.session_protection = None

    with app.app_context():
        user = User.query.filter_by(role=args.role, is_active=True).first()
        if user is None:
            print("no active %s user" % args.role)
            return 2
        uid, uname = user.id, user.username

    client = app.test_client()

    def login():
        """Re-plant the session before EVERY request.

        Planting it once did not survive the run: the routes sort
        alphabetically, /auth/logout comes before /dashboard, and hitting it
        logged the client out - so 24 pages afterwards reported a redirect to
        /auth/login and looked like a permission bug in the app. It was a bug in
        this file. Re-planting per request also means one page that clears the
        session cannot cascade into the rest of the report.
        """
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True

    rules = sorted((r for r in app.url_map.iter_rules()
                    if "GET" in r.methods and r.endpoint != "static"),
                   key=lambda r: str(r.rule))

    ok, broken, skipped, redirected = [], [], [], []
    print("as %s (%s), %d GET routes\n" % (uname, args.role, len(rules)))

    for rule in rules:
        filled, missing = resolve_args(rule, app)
        if filled is None:
            skipped.append((str(rule.rule), "no real value for <%s>" % missing))
            continue
        try:
            url = rule.rule
            for k, v in filled.items():
                url = re.sub(r"<[^:>]*:?%s>" % re.escape(k), str(v), url)
            login()
            resp = client.get(url)
            code = resp.status_code
            # Decode only what is actually text. as_text=True on a .docx blew up
            # with "'utf-8' codec can't decode byte 0x83" and got reported as an
            # app crash on /download-form-docx - a route that returns 200 and a
            # 62 KB document for all ten requests. The bug was here.
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text" in ctype or "json" in ctype or "html" in ctype:
                body = resp.get_data(as_text=True)
            else:
                body = "<%s, %d bytes>" % (ctype.split(";")[0] or "binary",
                                           len(resp.get_data()))
        except Exception as exc:                            # noqa: BLE001
            broken.append((str(rule.rule), "EXCEPTION", str(exc)[:160]))
            continue

        if code >= 500:
            broken.append((url, code, body.replace("\n", " ")[:160]))
        elif _renders_error(body):
            broken.append((url, "%d but renders an error" % code, _renders_error(body)))
        elif code in (301, 302, 303, 307, 308):
            redirected.append((url, resp.headers.get("Location", "")[:70]))
        elif code in (401, 403, 404):
            broken.append((url, code, "(permission or missing row)"))
        else:
            ok.append(url)

    print("=" * 78)
    print("BROKEN - %d" % len(broken))
    print("=" * 78)
    for url, code, detail in broken:
        print("  %-46s %s" % (url[:46], code))
        if detail:
            print("      %s" % detail)

    print("\nREDIRECTED - %d (usually a permission gate, worth an eye)" % len(redirected))
    for url, loc in redirected:
        print("  %-46s -> %s" % (url[:46], loc))

    print("\nSKIPPED - %d (could not source an argument)" % len(skipped))
    for url, why in skipped:
        print("  %-46s %s" % (url[:46], why))

    print("\nOK - %d" % len(ok))
    print("-" * 78)
    print("%d ok, %d broken, %d redirected, %d skipped"
          % (len(ok), len(broken), len(redirected), len(skipped)))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
