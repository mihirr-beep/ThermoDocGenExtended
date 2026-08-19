# -*- coding: utf-8 -*-
"""The half of the catalog that is measured from rows, kept off disk.

schema_catalog.py is generated and committed, and that is right for the schema:
table and column lists, foreign keys, domain slices, the routing index. Those
change when someone migrates, which is a reviewable event.

It was also carrying about 150 facts measured from live ROWS - every table's row
count, the value list of 80-odd columns, the JSON keys of 16 text columns, the
single-valued columns. Those change when someone uses the app, and a committed
file cannot keep up with that. Rejecting one datasheet this morning made three
statements in the prompt false at once:

    ### datasheet_harmonic (EMPTY - no rows yet)     it had a row
    test_code values: CE, CRF, EFT, ...              HARMONIC was missing
    datasheet_records.status IS 'Not Submitted'      'Submitted' had appeared

The third is the worst kind of stale, because it is not an omission - it is an
instruction. The catalog told the model to disregard a column that had just
become useful.

So the numbers live here instead, measured on demand and cached for TTL_SECONDS.
Measuring all of them costs about 54ms against this database (59 counts, 83
value lists, 16 JSON samples), which is why there is no cleverness about
refreshing the cheap parts more often than the expensive ones - there are no
expensive parts.

Nothing here is written to a source file. A disk cache under the instance
directory is a warm start only, best-effort, and its absence changes nothing:
read-only containers simply skip it and keep the in-process cache.
"""
import json
import os
import time

TTL_SECONDS = 120           # how long a measurement is allowed to be believed
MAX_ENUM_VALUES = 20        # keep in step with build_catalog
MIN_CLASS_REPEAT = 3
MIN_CONSTANT_ROWS = 8
JSON_SAMPLE_ROWS = 40

_CACHE = {"at": 0.0, "stats": None}
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DISK = os.path.join(_ROOT, "instance", "catalog_stats.json")

EMPTY = {"database": None, "measured_at": 0.0, "live": False,
         "row_counts": {}, "enums": {}, "constants": {}, "json_keys": {},
         "empty_columns": {}}


def _connect():
    import pymysql
    import mysql_config
    cfg = mysql_config.config["default"]
    return pymysql.connect(
        host=cfg.MYSQL_HOST, port=int(cfg.MYSQL_PORT), user=cfg.MYSQL_USER,
        password=cfg.MYSQL_PASSWORD, database=cfg.MYSQL_DATABASE,
        charset="utf8mb4", autocommit=True, connect_timeout=4, read_timeout=10)


def measure():
    """Read the volatile facts straight from the database. Read-only."""
    from . import schema_catalog as sc
    from .build_catalog import (_ENUMISH, _FLAGGISH, _NOT_A_CLASS, _ORDINAL_NAME,
                                _value_shape_ok, _values_are_measurements,
                                _json_profile)
    conn = _connect()
    try:
        cur = conn.cursor()
        db = conn.db.decode() if isinstance(conn.db, bytes) else conn.db
        out = dict(EMPTY, database=db, live=True, measured_at=time.time(),
                   row_counts={}, enums={}, constants={}, json_keys={},
                   empty_columns={})

        cur.execute("SELECT table_name, column_name, data_type, column_type, column_key "
                    "FROM information_schema.columns WHERE table_schema=%s "
                    "ORDER BY table_name, ordinal_position", (db,))
        by_table = {}
        for t, c, dtype, ctype, key in cur.fetchall():
            by_table.setdefault(t, []).append((c, (dtype or "").lower(), ctype, key))

        for table in sc.ALLOWED_TABLES:
            try:
                cur.execute("SELECT COUNT(*) FROM `%s`" % table)
                out["row_counts"][table] = cur.fetchone()[0]
            except Exception:  # noqa: BLE001 - a table may have gone
                continue

            # COLUMNS THAT HAVE NEVER HELD A VALUE. The catalog lists every
            # column as though it were usable, and 71 of them are NULL on every
            # row. Asked who was sending back the most work in peer review, the
            # model reached for iec_emc_requests.rejected_at / rejection_reason
            # / rejected_by - real columns, about an admin REFUSING A REQUEST,
            # which is a third rejection concept in this schema and nothing to
            # do with peer review. All three are empty, so it got zero, and
            # answered "there are zero rejections logged in peer review across
            # all records". Six exist, in datasheet_status_history.
            #
            # One query per table rather than one per column: 59 queries instead
            # of 1149, which is what makes this affordable per render.
            visible = [c for c in (sc.COLUMNS.get(table) or ())
                       if c in {x[0] for x in by_table.get(table, ())}]
            if visible and out["row_counts"].get(table):
                try:
                    cur.execute("SELECT %s FROM `%s`"
                                % (", ".join("COUNT(`%s`)" % c for c in visible),
                                   table))
                    counts = cur.fetchone() or ()
                    for col, n in zip(visible, counts):
                        if not n:
                            out["empty_columns"]["%s.%s" % (table, col)] = \
                                out["row_counts"][table]
                except Exception:  # noqa: BLE001 - a hint is never worth failing
                    pass

            for col, dtype, ctype, key in by_table.get(table, ()):
                ref = "%s.%s" % (table, col)
                if ref in sc.JSON_COLUMNS:
                    profile = _json_profile(cur, table, col)
                    if profile:
                        out["json_keys"][ref] = profile
                    continue
                if ref not in sc.CLASS_COLUMNS:
                    continue
                # The catalog already decided this column holds a class; here we
                # only refresh WHAT the values are, never re-litigate whether it
                # qualifies. That judgement is schema-shaped and belongs in the
                # committed file, or routing would shift under the model's feet.
                try:
                    cur.execute("SELECT COUNT(`%s`), COUNT(DISTINCT `%s`) FROM `%s`"
                                % (col, col, table))
                    non_null, distinct = cur.fetchone()
                    if not non_null or not distinct or distinct > MAX_ENUM_VALUES:
                        continue
                    cur.execute("SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IS NOT NULL "
                                "ORDER BY `%s` LIMIT %d"
                                % (col, table, col, col, MAX_ENUM_VALUES + 1))
                    vals = [str(r[0]) for r in cur.fetchall()]
                except Exception:  # noqa: BLE001
                    continue
                if not vals or len(vals) > MAX_ENUM_VALUES:
                    continue
                if len(vals) == 1:
                    if non_null >= MIN_CONSTANT_ROWS:
                        out["constants"][ref] = (vals[0], non_null)
                else:
                    out["enums"][ref] = tuple(sorted(vals))
        return out
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def current(force=False):
    """The volatile facts, fresh enough to state out loud.

    Never raises. When the database cannot be reached the last good measurement
    is reused, then the disk warm-start, then EMPTY - and EMPTY renders a prompt
    with no counts and no value lists rather than old ones, because a missing
    fact makes the model ask and a wrong fact makes it answer.
    """
    now = time.time()
    if not force and _CACHE["stats"] and now - _CACHE["at"] < TTL_SECONDS:
        return _CACHE["stats"]
    try:
        stats = measure()
        _CACHE.update(at=now, stats=stats)
        _write_disk(stats)
        return stats
    except Exception:  # noqa: BLE001 - a prompt must render without a database
        if _CACHE["stats"]:
            return _CACHE["stats"]
        disk = _read_disk()
        if disk:
            _CACHE.update(at=now, stats=disk)
            return disk
        return EMPTY


def _write_disk(stats):
    try:
        os.makedirs(os.path.dirname(_DISK), exist_ok=True)
        with open(_DISK, "w", encoding="utf-8") as fh:
            json.dump(stats, fh)
    except Exception:  # noqa: BLE001 - read-only deployments skip this silently
        pass


def _read_disk():
    try:
        with open(_DISK, encoding="utf-8") as fh:
            data = json.load(fh)
        data["live"] = False
        data["enums"] = {k: tuple(v) for k, v in (data.get("enums") or {}).items()}
        data["constants"] = {k: tuple(v) for k, v in (data.get("constants") or {}).items()}
        return data
    except Exception:  # noqa: BLE001
        return None


def invalidate():
    """Drop the cache. For a writer that knows it just changed something."""
    _CACHE.update(at=0.0, stats=None)
