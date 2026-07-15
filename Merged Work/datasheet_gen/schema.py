"""Self-contained schema guard for the datasheet feature.

WHY THIS EXISTS
---------------
On a fresh/empty database, app.py's ``ensure_planner_table()`` creates
``planner_entries`` with raw DDL that predates the datasheet/report workflow,
and its ALTER-migration map omits the newer columns. Because that raw table
already exists before SQLAlchemy's ``create_all()`` runs, ``create_all()`` never
adds the model's newer columns (it only creates missing *tables*, never alters
existing ones). The datasheet feature writes several of those columns
(``status``, ``datasheet_file_path``, ``completion_date`` ...), so without this
guard the generate endpoints fail with "Unknown column" on a clean install.

This module keeps the fix self-contained (no edits to app.py beyond the existing
two-line hook): ``register_datasheet_gen`` calls ``ensure_datasheet_columns`` to
additively add only the columns this feature needs. It is idempotent and safe to
run repeatedly (and under Flask's reloader, which runs create_app twice).
"""
from sqlalchemy import inspect, text

# column name -> ALTER clause. Matches models.PlannerEntry. Additive only.
_REQUIRED = {
    "status": "ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'in_progress'",
    "datasheet_file_path": "ADD COLUMN datasheet_file_path VARCHAR(500) NULL",
    "datasheet_uploaded_at": "ADD COLUMN datasheet_uploaded_at DATETIME NULL",
    "datasheet_uploaded_by": "ADD COLUMN datasheet_uploaded_by INT NULL",
    "datasheet_comments": "ADD COLUMN datasheet_comments TEXT NULL",
    "completion_date": "ADD COLUMN completion_date DATE NULL",
    "cancel_reason": "ADD COLUMN cancel_reason TEXT NULL",
    "cancelled_at": "ADD COLUMN cancelled_at DATETIME NULL",
    "cancelled_by": "ADD COLUMN cancelled_by INT NULL",
    "report_file_path": "ADD COLUMN report_file_path VARCHAR(500) NULL",
    "report_comments": "ADD COLUMN report_comments TEXT NULL",
    "report_uploaded_at": "ADD COLUMN report_uploaded_at DATETIME NULL",
    "report_uploaded_by": "ADD COLUMN report_uploaded_by INT NULL",
}


def ensure_datasheet_columns(app):
    """Add any datasheet/report columns missing from planner_entries.

    Best-effort: never raises out (a logging-only failure must not break app boot).
    """
    try:
        from models import db
    except Exception:  # pragma: no cover - models always importable in app
        return

    with app.app_context():
        try:
            inspector = inspect(db.engine)
            if "planner_entries" not in inspector.get_table_names():
                return  # create_all()/ensure_planner_table will make it; rerun later
            existing = {c["name"] for c in inspector.get_columns("planner_entries")}
        except Exception as exc:  # DB not ready yet, etc.
            app.logger.warning("datasheet_gen: planner_entries column check skipped: %s", exc)
            return

        added = []
        for name, clause in _REQUIRED.items():
            if name in existing:
                continue
            try:
                db.session.execute(text(f"ALTER TABLE planner_entries {clause}"))
                db.session.commit()
                added.append(name)
            except Exception as exc:
                db.session.rollback()
                msg = str(exc).lower()
                if "duplicate" in msg or "exists" in msg:
                    continue  # added concurrently (reloader race) - fine
                app.logger.error("datasheet_gen: failed adding planner_entries.%s: %s", name, exc)

        if added:
            app.logger.info("datasheet_gen: added planner_entries columns: %s", ", ".join(added))
