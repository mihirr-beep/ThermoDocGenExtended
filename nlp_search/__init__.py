# -*- coding: utf-8 -*-
"""NL search over the EMC lab data (admin tool).

The database is the only source of truth. A coordinator agent (OpenAI Agents
SDK) turns the question into read-only SQL, which is validated against a
generated schema catalog and executed with row, size and time caps. There is
no vector store and no document-retrieval lane: if a fact is not in a table,
the assistant says so rather than producing it from somewhere else.

Wire-up: call register_nlp_search(app) next to register_datasheet_gen(app).
"""


def register_nlp_search(app):
    """Mount the NL-search blueprint (page + ask endpoint) and ensure the audit
    table exists. The import is lazy so utility entry points
    (python -m nlp_search.build_catalog) work even before schema_catalog.py has
    been generated."""
    from .routes import nlp_search_bp
    app.register_blueprint(nlp_search_bp)
    try:
        from .audit import ensure_audit_table
        ensure_audit_table(app)
    except Exception as exc:  # noqa: BLE001 - never block boot
        app.logger.warning("nlp_search: audit table setup skipped: %s", exc)

    # Everything else this feature reads is owned by other modules. Check it
    # is there, create what datasheet_gen owns if it is not, and say plainly
    # what is still absent - see preflight.py for why a missing table is
    # worse here than an obvious error.
    try:
        from .preflight import ensure_dependencies
        ensure_dependencies(app)
    except Exception as exc:  # noqa: BLE001 - never block boot
        app.logger.warning("nlp_search: preflight skipped: %s", exc)
    return app
