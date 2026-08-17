#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bring a live database up to the schema this code expects. Idempotent.

    python tools_migrate_live.py                      # DRY RUN - report only
    python tools_migrate_live.py --apply              # apply, then verify
    python tools_migrate_live.py --apply --yes        # skip the confirmation
    python tools_migrate_live.py --database NAME ...  # target a specific database

PICK THE TARGET EXPLICITLY
--------------------------
`--database` exists because the environment cannot be trusted to select it here.
mysql_config.py loads .env with `os.environ[key] = value`, unconditionally - so
.env OVERWRITES a real environment variable rather than filling in for it, and
`MYSQL_DATABASE=prod python tools_migrate_live.py` silently migrates whatever
.env names instead. That is a bad surprise for an ALTER TABLE. Pass --database
and read the target line the script prints before you answer the prompt.

WHY THIS EXISTS WHEN THE APP ALREADY SELF-HEALS
-----------------------------------------------
datasheet_gen/__init__.py runs five idempotent creators at every boot, and
ensure_projection_tables() calls _ensure_integrity(), so starting the app on a
live database already applies everything below. That is fine for development and
wrong for production: an ALTER TABLE that adds a foreign key to a table with
millions of rows should be a decision someone makes, at a time they choose, with
the before-and-after in front of them - not a side effect of a restart nobody
was watching.

So this changes nothing the app would not do. It makes it explicit, ordered and
verifiable, and it reports the state whether or not you let it write.

IT DOES NOT CARRY ITS OWN DDL
-----------------------------
Every creation below is delegated to the module that owns the definition -
insight_schema owns emc_reason_code, projection_schema owns the projection tables
and the constraints. A second copy of a CREATE TABLE in a migration script is how
two definitions drift apart, and the one in the migration always wins on the box
you ran it on. The only SQL written here is the read-only checks.

WHAT IT WILL NOT DO
-------------------
  * It never DELETES a row to make a constraint fit. A dangling reference is
    re-pointed or set NULL; if that is impossible the constraint is reported as
    not applied and you decide.
  * It never seeds demo data. tools_seed_demo_requests.py and
    tools_lifecycle_probe.py do that, they are for development, and their rows
    are marked is_synthetic=1. This script WARNS if it finds any on the target,
    because synthetic rows in production would be quoted back to users as real
    jobs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (label, module, function) - the owning module's own idempotent creator.
CREATORS = (
    ("projection tables + constraints", "datasheet_gen.projection_schema",
     "ensure_projection_tables"),
    ("datasheet record tables", "datasheet_gen.records",
     "ensure_datasheet_record_tables"),
    ("admin config tables", "datasheet_gen.fixed_store", "ensure_config_tables"),
    ("reason-code taxonomy", "datasheet_gen.insight_schema", "ensure_insight_schema"),
    ("report wizard draft store", "report_gen.draft", "ensure_report_draft_table"),
)

# What must exist afterwards. ("table", name) / ("column", table, col) /
# ("fk", table, column) / ("rows", table, minimum)
EXPECTED = (
    # The reason taxonomy. Present since 2026-08-12 but never populated by the
    # application until the reviewer dropdown and the datasheet field landed, so
    # a database that has the columns may still have them all NULL - which is
    # not a schema problem and is reported separately below.
    ("table", "emc_reason_code"),
    ("rows", "emc_reason_code", 16),
    ("column", "datasheet", "failure_reason_code"),
    ("column", "datasheet_revision", "failure_reason_code"),
    ("column", "datasheet_status_history", "reason_code"),
    # The constraint added in this change set.
    ("fk", "datasheet_draft_history", "datasheet_id"),
    # The report wizard's draft store, which nlp_search now describes.
    ("table", "report_draft"),
)


# --------------------------------------------------------------------------
# Reading the target WITHOUT touching it
# --------------------------------------------------------------------------
# Every check below runs on a raw PyMySQL cursor, not through the Flask app, and
# that is the whole point of the dry run.
#
# The first version built the app to get a connection, and its "DRY RUN - nothing
# will be written" banner was false: create_app() runs datasheet_gen's five
# creators as a side effect of construction, so by the time the checks ran the
# tables and the foreign key had already been created. It reported "nothing to
# do" against an empty scratch database and was telling the truth only because it
# had just done everything itself.
#
# A migration tool that writes during a dry run is worse than no tool, so the
# read path now has no Flask in it at all.
def _scalar(cur, sql, args=()):
    cur.execute(sql, args)
    row = cur.fetchone()
    return row[0] if row else None


def table_exists(cur, name):
    return bool(_scalar(cur, "SELECT COUNT(*) FROM information_schema.tables "
                             "WHERE table_schema=DATABASE() AND table_name=%s", (name,)))


def column_exists(cur, table, col):
    return bool(_scalar(cur, "SELECT COUNT(*) FROM information_schema.columns "
                             "WHERE table_schema=DATABASE() AND table_name=%s "
                             "AND column_name=%s", (table, col)))


def fk_exists(cur, table, col):
    return bool(_scalar(cur, "SELECT COUNT(*) FROM information_schema.key_column_usage "
                             "WHERE table_schema=DATABASE() AND table_name=%s "
                             "AND column_name=%s AND referenced_table_name IS NOT NULL",
                        (table, col)))


def check(cur):
    """[(ok, description)] for everything in EXPECTED."""
    out = []
    for spec in EXPECTED:
        kind = spec[0]
        if kind == "table":
            out.append((table_exists(cur, spec[1]), "table %s" % spec[1]))
        elif kind == "column":
            ok = table_exists(cur, spec[1]) and column_exists(cur, spec[1], spec[2])
            out.append((ok, "column %s.%s" % (spec[1], spec[2])))
        elif kind == "fk":
            ok = table_exists(cur, spec[1]) and fk_exists(cur, spec[1], spec[2])
            out.append((ok, "foreign key on %s.%s" % (spec[1], spec[2])))
        elif kind == "rows":
            n = _scalar(cur, "SELECT COUNT(*) FROM `%s`" % spec[1]) \
                if table_exists(cur, spec[1]) else 0
            out.append((n >= spec[2], "%s has >= %d rows (has %s)"
                        % (spec[1], spec[2], n)))
    return out


def dangling_draft_history(cur):
    """Rows whose datasheet_id points at a datasheet that no longer exists.

    These block the foreign key, and the constraint refuses to apply rather than
    deleting them - which is correct, and means they must be repaired first. The
    repair itself lives in projection_schema._ensure_integrity.
    """
    if not (table_exists(cur, "datasheet_draft_history") and table_exists(cur, "datasheet")):
        return 0
    return _scalar(cur,
                   "SELECT COUNT(*) FROM datasheet_draft_history h "
                   "LEFT JOIN `datasheet` d ON d.id = h.datasheet_id "
                   "WHERE h.datasheet_id IS NOT NULL AND d.id IS NULL")


def advisories(cur):
    """Things that are not schema faults but change what the lab will see."""
    notes = []
    if table_exists(cur, "iec_emc_requests") and column_exists(
            cur, "iec_emc_requests", "is_synthetic"):
        n = _scalar(cur, "SELECT COUNT(*) FROM iec_emc_requests WHERE is_synthetic=1")
        if n:
            notes.append(
                "%d SYNTHETIC request(s) present (is_synthetic=1). These are demo "
                "rows from tools_seed_demo_requests.py / tools_lifecycle_probe.py. "
                "On a production database they should be removed - the assistant "
                "excludes them by default but they are real rows in every other "
                "report. Remove with: python tools_seed_demo_requests.py --clean "
                "and python tools_lifecycle_probe.py --clean" % n)
    if table_exists(cur, "datasheet_status_history"):
        total = _scalar(cur, "SELECT COUNT(*) FROM datasheet_status_history "
                            "WHERE to_status='Rejected'")
        coded = _scalar(cur, "SELECT COUNT(*) FROM datasheet_status_history "
                            "WHERE to_status='Rejected' AND reason_code IS NOT NULL") \
            if column_exists(cur, "datasheet_status_history", "reason_code") else 0
        if total and not coded:
            notes.append(
                "%d rejection(s) recorded, none with a reason_code. The column and "
                "the dropdown exist now, but rejections made BEFORE this change "
                "cannot be categorised retrospectively - nobody recorded which "
                "finding applied. Rejection-pattern questions will only cover "
                "rejections made from here on." % total)
    if table_exists(cur, "datasheet") and column_exists(cur, "datasheet", "test_code"):
        n = _scalar(cur, "SELECT COUNT(*) FROM `datasheet`")
        if not n:
            notes.append(
                "The `datasheet` table is EMPTY, so every datasheet and revision "
                "question will correctly answer 'nothing recorded'. If that is not "
                "expected, check this is the right database.")
    return notes


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    apply_it = "--apply" in sys.argv
    assume_yes = "--yes" in sys.argv

    override = None
    if "--database" in sys.argv:
        i = sys.argv.index("--database")
        if i + 1 >= len(sys.argv):
            print("--database needs a name")
            return 1
        override = sys.argv[i + 1]

    # mysql_config is safe to import - it only reads .env. `app` is NOT: importing
    # it and calling create_app() runs datasheet_gen's creators, so it must not
    # happen on the read path.
    import mysql_config
    import pymysql

    cfg = mysql_config.config["default"]
    name = override or cfg.MYSQL_DATABASE
    host = cfg.MYSQL_HOST

    def connect():
        # autocommit, because PyMySQL defaults to False and a plain SELECT then
        # opens a transaction that is never committed. That deadlocked this
        # script against itself: the read connection held a metadata lock on
        # datasheet_draft_history while the same process tried to ALTER that
        # table to add the foreign key, and the migration hung until it was
        # killed. A read path has nothing to commit, so it should not be in a
        # transaction at all.
        return pymysql.connect(host=host, port=int(cfg.MYSQL_PORT),
                               user=cfg.MYSQL_USER, password=cfg.MYSQL_PASSWORD,
                               database=name, charset="utf8mb4", autocommit=True)

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        print("cannot connect to '%s' on %s: %s" % (name, host, exc))
        return 1
    cur = conn.cursor()

    print("=" * 74)
    print("target : %s on %s   (MySQL %s)" % (name, host, _scalar(cur, "SELECT VERSION()")))
    print("mode   : %s" % ("APPLY" if apply_it else "DRY RUN - nothing will be written"))
    print("=" * 74)

    before = check(cur)
    dang = dangling_draft_history(cur)
    print("\nCURRENT STATE")
    for ok, desc in before:
        print("  [%s] %s" % ("ok" if ok else "MISSING", desc))
    print("  [%s] datasheet_draft_history dangling rows: %d"
          % ("ok" if not dang else "REPAIR", dang))

    missing = [d for ok, d in before if not ok]
    if not missing and not dang:
        print("\nNothing to do - this database already matches the code.")
        for note in advisories(cur):
            print("\nNOTE: %s" % note)
        conn.close()
        return 0

    print("\nWOULD APPLY" if not apply_it else "\nAPPLYING")
    for label, module_name, func_name in CREATORS:
        print("  - %-34s (%s.%s)" % (label, module_name, func_name))
    if dang:
        print("  - re-point or NULL %d dangling draft-history link(s), then add "
              "the foreign key" % dang)

    if not apply_it:
        print("\nDry run. Re-run with --apply to make these changes.")
        for note in advisories(cur):
            print("\nNOTE: %s" % note)
        conn.close()
        return 0

    if not assume_yes:
        print("\nThis will ALTER the schema of '%s' on %s." % (name, host))
        try:
            reply = input("Type the database name to continue: ").strip()
        except EOFError:
            reply = ""
        if reply != name:
            print("Aborted - nothing was written.")
            conn.close()
            return 1

    # Closed BEFORE anything writes, belt as well as braces alongside autocommit:
    # nothing this process holds should be able to block its own ALTER TABLE.
    conn.close()

    # Only now is the app allowed to exist. Overridden on the config class rather
    # than on app.config, because Flask-SQLAlchemy caches its engine on first use
    # and a later rewrite changes the banner without changing the connection.
    if override:
        for c in mysql_config.config.values():
            c.MYSQL_DATABASE = override

    import app as app_module
    a = app_module.create_app("default")

    print()
    for label, module_name, func_name in CREATORS:
        try:
            import importlib
            module = importlib.import_module(module_name)
            getattr(module, func_name)(a)
            print("  ran %s.%s" % (module_name, func_name))
        except Exception as exc:  # noqa: BLE001 - try the rest regardless
            print("  FAILED %s.%s: %s" % (module_name, func_name, exc))

    # Verified on a FRESH connection: the checks read information_schema, and the
    # one opened before the ALTERs can serve a stale view of it.
    conn = connect()
    cur = conn.cursor()

    after = check(cur)
    still = [d for ok, d in after if not ok]
    dang_after = dangling_draft_history(cur)
    print("\nVERIFY")
    for ok, desc in after:
        print("  [%s] %s" % ("ok" if ok else "STILL MISSING", desc))
    print("  [%s] datasheet_draft_history dangling rows: %d"
          % ("ok" if not dang_after else "STILL", dang_after))

    for note in advisories(cur):
        print("\nNOTE: %s" % note)
    conn.close()

    if still:
        print("\n%d item(s) still missing. These are owned by the main "
              "application, not by datasheet_gen - check the database is the "
              "right one and fully migrated." % len(still))
        return 1
    print("\nDone - '%s' now matches the code." % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
