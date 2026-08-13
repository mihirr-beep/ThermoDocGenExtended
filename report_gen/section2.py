# -*- coding: utf-8 -*-
"""The three parts of section 2 that are DERIVED across every test, not typed.

WHY THESE THREE SIT APART FROM THE REST OF SECTION 2
----------------------------------------------------
Almost all of section 2 is one request field to one report cell, and
service.collect() already maps it - manufacturer, EUT name, model, serial,
samples, size, weight, operating voltage and frequency, power rating, measured
current, category, type, configuration and monitoring all resolve today. Where
they print blank it is because the REQUEST is blank, not because nothing maps
them.

These three are different: each has to be computed by looking across ALL the
tests on the request, so no single field can hold the answer.

  2.3 SOFTWARE AND FIRMWARE   every software row from every test's datasheet.
                              The template ships a <placeholder> paragraph and
                              builder.py deliberately left it - "no source in the
                              request" - which was true of the request and untrue
                              of the datasheets, where datasheet_software has
                              held the name and version all along.

  2.4 is NOT here. service.modification_rows() already merged the modification
  table across tests, so the 0..N gap-filling went there rather than becoming a
  second implementation reading the same data from a different table.

  2.7 EUT MODES OF OPERATION  the request stores mode DESCRIPTIONS in order; the
                              report prints them labelled "Mode A:", "Mode B:".
                              The letter is positional and exists nowhere in the
                              database.
"""
import json
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# Mode A .. Mode J. The request form caps functional modes at ten, and a
# report that ran off the end of the alphabet would be a data-entry problem
# rather than something to paper over with "Mode AA".
MODE_LETTERS = "ABCDEFGHIJ"


def _rows(sql, **params):
    from models import db
    return [dict(m) for m in db.session.execute(text(sql), params).mappings().all()]


# ---------------------------------------------------------------------------
# 2.3 SOFTWARE AND FIRMWARE DETAILS
# ---------------------------------------------------------------------------

def software_rows(request_id, include_tests=None):
    """[[test, software, version], ...] for every test on the request.

    One row per (test, software). The same tool usually runs every test - on
    IEC-EMC-013 all eleven use iec.control 10.3.2 and Net.Control 3.2.6 - so
    collapsing to a distinct list would be tidier and would also stop a reader
    checking that the test THEY care about had the software they expect. The
    report is read by an assessor; per test is the useful shape.
    """
    rows = _rows("""
        SELECT d.test_code AS test_code,
               COALESCE(NULLIF(TRIM(s.software_name), ''), '')    AS name,
               COALESCE(NULLIF(TRIM(s.software_version), ''), '') AS version
        FROM datasheet_software s
        JOIN `datasheet` d ON d.id = s.datasheet_id
        JOIN planner_entries p ON p.id = d.planner_entry_id
        WHERE p.test_request_id = :r
          AND (s.software_name IS NOT NULL AND TRIM(s.software_name) <> '')
        ORDER BY d.test_code, s.row_no
    """, r=int(request_id))
    keep = {str(c).upper() for c in (include_tests or [])}
    out, seen = [], set()
    for r in rows:
        code = (r["test_code"] or "").upper()
        # A cancelled test's datasheet still has software rows; the report covers
        # only the tests it actually contains.
        if keep and code not in keep:
            continue
        key = (code, r["name"].lower(), r["version"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append([code, r["name"], r["version"]])
    return out


# ---------------------------------------------------------------------------
# 2.4 EUT MODIFICATION RECORD
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2.7 EUT MODES OF OPERATION
# ---------------------------------------------------------------------------

def mode_lines(request_obj=None, request_id=None):
    """["Mode A: <description>", ...] in the order the request records them.

    The letter is positional - the first functional mode on the request is Mode
    A - and appears nowhere in the database, which is why this cannot be a plain
    column read. A description that already starts with its own "Mode X:" is left
    alone rather than becoming "Mode A: Mode A: ...".
    """
    values = []
    if request_obj is not None:
        for fm in (getattr(request_obj, "functional_modes", None) or []):
            v = str(getattr(fm, "mode_value", "") or "").strip()
            if v:
                values.append(v)
    if not values and request_id is not None:
        values = [r["mode_value"] for r in _rows(
            "SELECT COALESCE(mode_value,'') AS mode_value "
            "FROM iec_emc_request_functional_modes "
            "WHERE request_id = :r ORDER BY sort_order, id", r=int(request_id))
            if str(r["mode_value"]).strip()]
    if not values and request_obj is not None:
        # older requests kept the modes as one free-text block
        import re
        blob = str(getattr(request_obj, "operation_modes", "") or "")
        values = [x.strip() for x in re.split(r"[\r\n]+", blob) if x.strip()]

    out = []
    for i, v in enumerate(values):
        if i < len(MODE_LETTERS):
            letter = MODE_LETTERS[i]
            low = v.lower()
            if low.startswith("mode %s" % letter.lower()):
                out.append(v)
            else:
                out.append("Mode %s: %s" % (letter, v))
        else:
            log.warning("request has more than %d functional modes; "
                        "mode %d printed unlabelled", len(MODE_LETTERS), i + 1)
            out.append(v)
    return out
