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
    # SharePoint mirror. The URLs are what the UI can link to; synced_at and error make a
    # failed push visible and retryable instead of silently lost. VARCHAR(1000) because a
    # Graph webUrl carries the whole encoded path.
    "sharepoint_draft_url": "ADD COLUMN sharepoint_draft_url VARCHAR(1000) NULL",
    "sharepoint_approved_url": "ADD COLUMN sharepoint_approved_url VARCHAR(1000) NULL",
    "sharepoint_synced_at": "ADD COLUMN sharepoint_synced_at DATETIME NULL",
    "sharepoint_error": "ADD COLUMN sharepoint_error TEXT NULL",
}

#: The job folder's name, resolved once and kept on the REQUEST. product_name and
#: job_number stay editable after datasheets are uploaded, so re-deriving this per upload
#: would split one request's datasheets across two SharePoint folders the first time
#: somebody fixed a typo.
_REQUIRED_REQUEST = {
    "sharepoint_folder_name": "ADD COLUMN sharepoint_folder_name VARCHAR(255) NULL",
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

        # the request-side column, same pattern
        try:
            if "iec_emc_requests" in inspector.get_table_names():
                have = {c["name"] for c in inspector.get_columns("iec_emc_requests")}
                for name, clause in _REQUIRED_REQUEST.items():
                    if name in have:
                        continue
                    try:
                        db.session.execute(text(f"ALTER TABLE iec_emc_requests {clause}"))
                        db.session.commit()
                        app.logger.info("datasheet_gen: added iec_emc_requests.%s", name)
                    except Exception as exc:
                        db.session.rollback()
                        if not any(w in str(exc).lower() for w in ("duplicate", "exists")):
                            app.logger.error(
                                "datasheet_gen: failed adding iec_emc_requests.%s: %s", name, exc)
        except Exception as exc:  # noqa: BLE001 - boot must not depend on this
            app.logger.warning("datasheet_gen: iec_emc_requests column check skipped: %s", exc)
