# -*- coding: utf-8 -*-
"""Render the four datasheet .docx files for IEC-EMC-900 and record their paths.

WHY THIS EXISTS AS A THIRD STEP
-------------------------------
report_gen builds each per-test section one of two ways:

  SPLICE   lift the pages straight out of the datasheet .docx that peer review
           approved. This is what production does, because approving a
           datasheet writes planner_entries.datasheet_file_path.

  FALLBACK fill the report template's own copy of those tables from form_json,
           used only when the .docx is missing from disk.

tools_seed_full_request_sheets.py approved the four datasheets in the database
but never produced a .docx, so _datasheet_path() returned None for all four and
the first report off this fixture exercised the FALLBACK path end to end - the
one production almost never takes. Everything read from those sections was
therefore evidence about the wrong code path.

This renders the documents the same way generic_routes._render_datasheet_docx
does - gs.build_context -> gg.render - minus the image upload, which needs a
live request context and which this fixture has nothing to feed anyway.

    python tools_seed_full_request_docs.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "IEC-EMC-900"


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, PlannerEntry
    from datasheet_gen import generic_service as gs
    from datasheet_gen import generic_generator as gg
    from datasheet_gen import records as R
    from datasheet_gen.registry import REGISTRY, load_schema
    from datasheet_gen.generic_routes import _output_dir, _parent_request
    # CE is the bespoke form: no schema JSON, its own context builder and
    # renderer. Routing it through the generic path raises FileNotFoundError on
    # schemas/CE.json, which is how this was found.
    from datasheet_gen.service import build_ce_context
    from datasheet_gen.generator import render_ce_datasheet
    import json

    a = app_module.create_app("default")
    with a.app_context():
        rid = db.session.execute(text(
            "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
        if not rid:
            print("no %s - run tools_seed_full_request.py first" % TCO)
            return 1

        rows = db.session.execute(text(
            "SELECT p.id AS entry_id, p.test_name, d.form_json "
            "FROM planner_entries p "
            "JOIN datasheet_records d ON d.planner_entry_id = p.id "
            "WHERE p.test_request_id = :r ORDER BY p.id"), {"r": rid}).mappings().all()
        if not rows:
            print("no datasheets - run tools_seed_full_request_sheets.py first")
            return 1

        for row in rows:
            code = (row["test_name"] or "").strip().upper()
            if code not in REGISTRY:
                print("   %-5s not in REGISTRY - skipped" % code)
                continue

            entry = db.session.get(PlannerEntry, row["entry_id"])
            form = json.loads(row["form_json"])
            parent = _parent_request(entry)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = os.path.join(_output_dir(), "%s_%s_%s.docx" % (TCO, code, ts))

            # No images: this fixture has none to upload, and an image the
            # report cannot find is worse evidence than an absent one.
            if code == "CE":
                render_ce_datasheet(build_ce_context(form), out, images={})
            else:
                schema = load_schema(code)
                ctx = gs.build_context(schema, form, request_obj=parent)
                gg.render(code, ctx, gs.image_keys(schema), {}, out)

            entry.datasheet_file_path = out
            db.session.execute(text(
                "UPDATE datasheet_records SET generated_file_path = :p "
                "WHERE planner_entry_id = :e"), {"p": out, "e": entry.id})
            db.session.commit()

            print("   %-5s %-52s %6.0f KB" % (
                code, os.path.basename(out), os.path.getsize(out) / 1024.0))

        print("\nfour datasheet documents rendered; the report will now SPLICE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
