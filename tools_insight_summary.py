# -*- coding: utf-8 -*-
"""Can it tell the STORY? No scoring - just ask and print.

The coverage suite measures whether specific facts appear. That is the wrong
question for a manager, who wants to know what happened, in a paragraph, without
reading a table. So these are deliberately high-level: no frequencies named, no
codes, no product IDs beyond the name.

    python tools_insight_summary.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

QUESTIONS = [
    "In one short paragraph, what happened with the DEMO Aurora Centrifuge C5?",
    "Give me a high-level summary of what is going wrong across the lab.",
    "Summarise the DEMO Orion Analyzer O9 story for my manager in a few lines.",
    "What is the overall picture on the Full-Scope EMC Sample Unit?",
]


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.setdefault("NLP_SEARCH_MODEL", "gpt-5-nano")
    os.environ.setdefault("NLP_WORKER_MODEL", "gpt-5-nano")

    import app as app_module
    from nlp_search import orchestrator

    flask_app = app_module.create_app("default")
    cfg = flask_app.config
    db_params = {"host": cfg.get("MYSQL_HOST"), "port": cfg.get("MYSQL_PORT"),
                 "user": cfg.get("MYSQL_USER"), "password": cfg.get("MYSQL_PASSWORD"),
                 "database": cfg.get("MYSQL_DATABASE")}
    with flask_app.app_context():
        for q in QUESTIONS:
            print("=" * 78)
            print("Q: %s" % q)
            r = orchestrator.answer(q, db_params, verify_answer=True)
            print("-" * 78)
            print(r.get("answer") if r.get("success") else "NO ANSWER: %s" % r.get("message"))
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
