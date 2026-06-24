"""Block until MySQL accepts a real connection, then exit 0.

Run before the app so a fresh `docker compose up` (first-time MySQL init, where the
healthcheck can pass against the temporary init server) doesn't crash the app with
'Connection refused'.
"""
import os
import sys
import time

import pymysql

HOST = os.environ.get("MYSQL_HOST", "db")
PORT = int(os.environ.get("MYSQL_PORT", "3306"))
USER = os.environ.get("MYSQL_USER", "root")
PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
DATABASE = os.environ.get("MYSQL_DATABASE", "")

ATTEMPTS = 60
DELAY = 2

for i in range(1, ATTEMPTS + 1):
    try:
        conn = pymysql.connect(
            host=HOST, port=PORT, user=USER, password=PASSWORD,
            database=DATABASE, connect_timeout=3,
        )
        conn.close()
        print(f"Database is ready (after {i} attempt(s)).")
        sys.exit(0)
    except Exception as exc:
        print(f"waiting for database {i}/{ATTEMPTS}: {exc}")
        time.sleep(DELAY)

print("Database not reachable after %d attempts." % ATTEMPTS, file=sys.stderr)
sys.exit(1)
