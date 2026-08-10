# -*- coding: utf-8 -*-
"""Boot check: are the tables this feature reads actually there?

The NL search only ever READS. It creates one table of its own
(nlp_search_audit) and otherwise queries tables owned by datasheet_gen and by
the main application - the nineteen projection tables, datasheet_records,
datasheet_fixed_values, the request and planner tables.

That is a dependency across module boundaries and nothing enforced it. On a
fresh database, or one restored from a dump taken before the normalisation
work, a table can simply be absent - and the failure that produces is bad in a
specific way. sql_guard validates the name against the generated catalog, so
the query is allowed; MySQL then errors on execution, the worker reports it
could not answer, and the user is told the lab has no such data. The feature
looks broken rather than unconfigured, and nothing points at the cause.

So this runs once at boot, costs a single query against information_schema,
and does three things in order:

  1. Compares the catalog's table list against what exists.
  2. If any are missing AND they belong to datasheet_gen, calls that module's
     own idempotent creators to build them. It does not carry its own copy of
     the DDL - one definition, in the module that owns it.
  3. Logs what is still missing after that, by name, so a real gap is visible
     in the boot log instead of surfacing later as a wrong answer.

It never raises. A failure here degrades the NL search, and the NL search is
an admin convenience - it must not stop the lab application from starting.
"""


def _existing_tables(db, database):
    from sqlalchemy import text
    rows = db.session.execute(
        text("SELECT TABLE_NAME FROM information_schema.TABLES "
             "WHERE TABLE_SCHEMA = :db"),
        {"db": database}).fetchall()
    return {r[0] for r in rows}


# Which module owns which tables, so a gap is repaired by the code that
# defines them rather than by a second copy of the DDL living here.
_REPAIRS = (
    ("datasheet_gen.projection_schema", "ensure_projection_tables"),
    ("datasheet_gen.records", "ensure_datasheet_record_tables"),
    ("datasheet_gen.fixed_store", "ensure_config_tables"),
)


def ensure_dependencies(app):
    """Verify the tables the catalog references exist; create ours if not.

    Returns (missing_before, missing_after) as sorted lists, for logging and
    for the tests. Both empty is the normal case on a healthy database.
    """
    try:
        from models import db
        from .schema_catalog import ALLOWED_TABLES
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("nlp_search: preflight skipped (%s)", exc)
        return [], []

    database = app.config.get("MYSQL_DATABASE")
    if not database:
        app.logger.warning("nlp_search: preflight skipped, MYSQL_DATABASE unset")
        return [], []

    with app.app_context():
        try:
            have = _existing_tables(db, database)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("nlp_search: preflight could not read "
                               "information_schema: %s", exc)
            return [], []

        missing = sorted(t for t in ALLOWED_TABLES if t not in have)
        if not missing:
            app.logger.info("nlp_search: preflight OK - all %d catalog tables "
                            "present", len(ALLOWED_TABLES))
            return [], []

        app.logger.warning("nlp_search: %d catalog table(s) missing: %s",
                           len(missing), ", ".join(missing))

        # Ask the owning modules to build theirs. Every one of these is
        # CREATE TABLE IF NOT EXISTS, so running them on a healthy database
        # is a no-op - which is why it is safe to call them unconditionally
        # once we know something is wrong.
        for module_name, func_name in _REPAIRS:
            try:
                import importlib
                module = importlib.import_module(module_name)
                getattr(module, func_name)(app)
                app.logger.info("nlp_search: ran %s.%s", module_name, func_name)
            except Exception as exc:  # noqa: BLE001 - try the rest regardless
                app.logger.warning("nlp_search: %s.%s failed: %s",
                                   module_name, func_name, exc)

        try:
            have = _existing_tables(db, database)
        except Exception:  # noqa: BLE001
            return missing, missing
        still = sorted(t for t in ALLOWED_TABLES if t not in have)

        if not still:
            app.logger.info("nlp_search: preflight repaired all %d missing "
                            "table(s)", len(missing))
        else:
            # Not fatal, and deliberately loud. These are tables the main
            # application owns - iec_emc_requests, planner_entries, users -
            # which this module has no business creating. A question that
            # needs one will now fail with a clear log line behind it.
            app.logger.error(
                "nlp_search: %d table(s) STILL MISSING after repair: %s. "
                "Questions needing them cannot be answered. These belong to "
                "the main application, not to nlp_search - check the database "
                "is the right one and fully migrated.",
                len(still), ", ".join(still))
        return missing, still
