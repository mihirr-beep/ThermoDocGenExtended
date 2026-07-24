# -*- coding: utf-8 -*-
"""Pre-flight checks for the EMC Test Workflow & Datasheet Generator.

Run by run.bat (with the venv Python) just before launching app.py. Verifies the
things that block a successful start and prints a plain-language message for each
problem instead of a Python traceback.

Exit code 0  -> everything the app needs is in place, safe to launch.
Exit code 1  -> a blocking problem was found (message already printed).
"""
import os
import sys
import socket

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    """Load KEY=VALUE lines from .env into the environment (same rules as app.py)."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key:
                os.environ.setdefault(key, val.strip().strip("'\""))


def fail(message):
    print("\n[BLOCKED] " + message + "\n")
    sys.exit(1)


def main():
    load_env()

    host = os.environ.get("MYSQL_HOST", "localhost")
    port = int(os.environ.get("MYSQL_PORT", "3306") or "3306")
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "test_plan_generator")

    print("Checking database: user '%s' at %s:%s, database '%s'" % (user, host, port, database))

    # 1) PyMySQL importable (installed by run.bat).
    try:
        import pymysql
    except ImportError:
        fail("The PyMySQL package is not installed.\n"
             "  Re-run run.bat so it can install the dependencies first.")

    # 2) Is the MySQL server reachable on the network?
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(5)
    try:
        probe.connect((host, port))
    except Exception as exc:
        fail("Cannot reach a MySQL server at %s:%s (%s).\n"
             "  - Is MySQL Server installed and running?\n"
             "      Windows: open 'Services' and confirm 'MySQL80' is Running,\n"
             "      or install MySQL Community Server 8.0 from\n"
             "      https://dev.mysql.com/downloads/installer/\n"
             "  - Are MYSQL_HOST and MYSQL_PORT correct in your .env file?"
             % (host, port, exc.__class__.__name__))
    finally:
        probe.close()

    # 3) Do the credentials work?
    try:
        conn = pymysql.connect(host=host, port=port, user=user,
                               password=password, connect_timeout=8)
    except Exception as exc:
        fail("MySQL refused the connection for user '%s' (%s).\n"
             "  - Check MYSQL_USER and MYSQL_PASSWORD in your .env file." % (user, exc))

    # 4) Does the target database exist? Create it if we're allowed to.
    try:
        cur = conn.cursor()
        cur.execute("SHOW DATABASES LIKE %s", (database,))
        if cur.fetchone() is None:
            print("Database '%s' not found - creating it..." % database)
            safe = database.replace("`", "")
            try:
                cur.execute("CREATE DATABASE `%s` CHARACTER SET utf8mb4 "
                            "COLLATE utf8mb4_unicode_ci" % safe)
                conn.commit()
                print("Created database '%s'. (The app will create its tables on start.)" % database)
            except Exception as exc:
                fail("Database '%s' does not exist and could not be created (%s).\n"
                     "  - Ask a DBA to create it, or grant CREATE privilege to '%s', then:\n"
                     "      CREATE DATABASE %s CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                     % (database, exc, user, database))
    finally:
        conn.close()

    # 5) Is port 3000 free? (Non-blocking: warn only.)
    check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        check.bind(("127.0.0.1", 3000))
    except Exception:
        print("\n[WARNING] Port 3000 is already in use.\n"
              "          The app may already be running in another window, or another\n"
              "          program holds the port. Close it, or the app will fail to start.")
    finally:
        check.close()

    print("\nPre-flight checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
