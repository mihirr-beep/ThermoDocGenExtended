"""datasheet_gen - modular CE datasheet generation (Option A).

Self-contained: new module only. Register it on the Flask app with one call
(see register_datasheet_gen) - the single in-place hook into app.py.
"""
from .routes import datasheet_gen_bp
from .generic_routes import datasheet_generic_bp
from .records_routes import datasheet_records_bp
from .schema import ensure_datasheet_columns
from .records import ensure_datasheet_record_tables
from .fixed_store import ensure_config_tables
from .procedure_store import ensure_procedure_table
from .projection_schema import ensure_projection_tables
from .insight_schema import ensure_insight_schema
from .admin_routes import datasheet_admin_bp


def register_datasheet_gen(app):
    """Mount the datasheet generation blueprints (bespoke CE + generic engine)."""
    app.register_blueprint(datasheet_gen_bp)
    app.register_blueprint(datasheet_generic_bp)
    app.register_blueprint(datasheet_records_bp)
    app.register_blueprint(datasheet_admin_bp)
    # Ensure planner_entries has the datasheet/report columns this feature writes
    # (app.py's raw table creator predates them; see schema.py for the why).
    ensure_datasheet_columns(app)
    # Ensure the datasheet_records store (drafts + submitted filled forms) exists.
    ensure_datasheet_record_tables(app)
    # Ensure the admin-editable fixed-values + basic-standard mapping tables exist
    # (seeded once with the values that were previously hardcoded).
    ensure_config_tables(app)
    # the Test Procedure overrides an admin makes on the config page
    ensure_procedure_table(app)
    # Ensure the projection tables exist - the queryable copy of the filled forms.
    # Additive only; form_json remains the source of truth (see projection_schema).
    ensure_projection_tables(app)
    # Ensure the reason taxonomy and the two failure-classification columns
    # exist. A rejection reason is a lab fact, not a chatbot feature - it is
    # worth recording whether or not anyone ever asks the question - so it lives
    # with the datasheet domain rather than in nlp_search (see insight_schema).
    ensure_insight_schema(app)
    # The report wizard's draft store. It lives on this hook rather than a new
    # one because report_gen has no register step - the report is triggered by a
    # single endpoint in app.py - and inventing a second boot hook for one table
    # is how a project ends up with four places that create schema.
    try:
        from report_gen.draft import ensure_report_draft_table
        ensure_report_draft_table(app)
        # The wizard's blueprint rides the same hook for the same reason.
        from report_gen.wizard_routes import report_wizard_bp
        if "report_wizard" not in app.blueprints:
            app.register_blueprint(report_wizard_bp)
        from report_gen.wizard_pages import report_wizard_pages_bp
        if "report_wizard_pages" not in app.blueprints:
            app.register_blueprint(report_wizard_pages_bp)
    except Exception as exc:  # noqa: BLE001 - must never stop the app booting
        app.logger.warning("report draft table skipped: %s", exc)
    return app
