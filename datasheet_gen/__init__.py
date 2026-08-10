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
from .projection_schema import ensure_projection_tables
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
    # Ensure the projection tables exist - the queryable copy of the filled forms.
    # Additive only; form_json remains the source of truth (see projection_schema).
    ensure_projection_tables(app)
    return app
