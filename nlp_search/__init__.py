# -*- coding: utf-8 -*-
"""NL search over the EMC lab data (admin tool).

Architecture (as designed):
  - a coordinator agent (OpenAI Agents SDK) routes each question by intent;
  - query_database tool: NL->SQL over MySQL, SELECT-only, validated against a
    generated schema catalog, executed read-only with row/time caps;
  - search_documents tool: Pinecone RAG over generated datasheets - currently
    a stub so the routing is testable before the index exists.

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
    return app
