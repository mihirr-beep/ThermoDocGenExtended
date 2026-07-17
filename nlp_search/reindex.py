# -*- coding: utf-8 -*-
"""Backfill the Pinecone index with already-approved datasheets.

Ingestion normally happens live when a peer approves a datasheet. This CLI
indexes the ones approved BEFORE the feature existed (and lets you rebuild):

    python -m nlp_search.reindex            # all approved planner entries with a .docx
    python -m nlp_search.reindex --all      # every planner entry that has a .docx

Requires OPENAI_API_KEY and PINECONE_API_KEY in the environment (.env).
"""
import os
import sys


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    take_all = "--all" in argv

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import mysql_config  # noqa: F401 - loads .env into os.environ

    from . import embeddings, ingest, vector_store
    if not embeddings.available():
        print("OPENAI_API_KEY not set - aborting."); return 1
    if not vector_store.available():
        print("PINECONE_API_KEY not set - aborting."); return 1

    import pymysql; pymysql.install_as_MySQLdb()
    from flask import Flask
    from mysql_config import config
    from models import db, PlannerEntry

    app = Flask(__name__)
    cfg = config["default"]; app.config.from_object(cfg); cfg.init_app(app); db.init_app(app)

    ok = skipped = errors = 0
    with app.app_context():
        q = PlannerEntry.query.filter(PlannerEntry.datasheet_file_path.isnot(None))
        if not take_all:
            q = q.filter(PlannerEntry.status == "datasheet_uploaded")
        entries = q.all()
        print("Found %d planner entr%s to index%s"
              % (len(entries), "y" if len(entries) == 1 else "ies",
                 "" if take_all else " (status=datasheet_uploaded)"))
        for e in entries:
            res = ingest.ingest_on_approval(e)
            status = res.get("status")
            print("  entry %-5s %-16s -> %s (%s chunks)%s"
                  % (e.id, (e.test_name or "?"), status, res.get("chunks", 0),
                     "" if status == "ok" else "  [%s]" % res.get("reason", "")))
            ok += status == "ok"; skipped += status == "skipped"; errors += status == "error"
    print("Done. ok=%d skipped=%d errors=%d" % (ok, skipped, errors))
    print("Index stats:", vector_store.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
