"""datasheet_gen - modular CE datasheet generation (Option A).

Self-contained: new module only. Register it on the Flask app with one call
(see register_datasheet_gen) - the single in-place hook into app.py.
"""
from .routes import datasheet_gen_bp
from .generic_routes import datasheet_generic_bp


def register_datasheet_gen(app):
    """Mount the datasheet generation blueprints (bespoke CE + generic engine)."""
    app.register_blueprint(datasheet_gen_bp)
    app.register_blueprint(datasheet_generic_bp)
    return app
