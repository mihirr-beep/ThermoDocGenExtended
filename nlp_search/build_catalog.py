# -*- coding: utf-8 -*-
"""Regenerate schema_catalog.py from the live MySQL database.

Run whenever the schema changes:

    python -m nlp_search.build_catalog

It introspects the database configured in mysql_config/.env, keeps only the
tables an NL->SQL agent should see (non-empty, business-relevant), and writes
nlp_search/schema_catalog.py: the table allowlist plus a compact catalog text
that goes into the agent's system prompt (columns, join keys, and the actual
values of low-cardinality status/code columns so the model filters correctly).
"""
import io
import os
import re
import sys

# One-line purposes, hand-authored. A table with no purpose line still gets
# cataloged with a generic line, but these make the model's routing better.
PURPOSES = {
    "users": "application users (requesters, lab engineers, admins); role column decides permissions",
    "equipment": "lab test-equipment inventory (make, model, serial, calibration due dates)",
    "maintenance": "maintenance records for lab equipment",
    "equipment_history": "audit trail of changes to equipment records (who changed what, when)",
    "iec_emc_requests": "MASTER EMC test request, one row per TCO/job: product, requester, status, assignment, key dates",
    "iec_emc_request_tests": "one row per EMC test per request (test_code like CE/RE/EFT/ESD/SURGE...); is_selected=1 = test in scope; per-test workflow status + engineer",
    "planner_entries": "lab test scheduling & execution, one row per scheduled test: engineer, dates, status (incl. peer review), generated datasheet path",
    "datasheet_records": "saved datasheet FORMS (draft or submitted) per planner entry: test_code, status, environment data, result, tester",
    "basic_standard_map": "admin mapping: product standard -> basic standard used by datasheets",
    "datasheet_fixed_values": "admin-editable fixed values (uncertainty, SOP refs, limits) per datasheet type",
    "test_requests": "LEGACY upload-based test requests (older flow; the EMC flow is iec_emc_requests)",
    "test_plans": "generated test-plan documents for legacy test_requests",
    "test_datasheets": "generated datasheet documents metadata for the legacy flow",
    "iec_emc_request_service_types": "service types requested (per request)",
    "iec_emc_request_serial_numbers": "EUT serial numbers (per request)",
    "iec_emc_request_categories": "product categories (per request)",
    "iec_emc_request_accessories": "EUT accessories (per request)",
    "iec_emc_request_cables": "EUT cables (per request)",
    "iec_emc_request_eut_specs": "EUT electrical specs: voltage/frequency/phase/power (per request)",
    "iec_emc_request_supply_vf": "supply voltage/frequency combinations to test (per request)",
    "iec_emc_request_product_standards": "product standards declared on the request (e.g. EN 61326-1)",
    "iec_emc_request_product_environments": "intended product environments (per request)",
    "iec_emc_request_decision_rules": "conformity decision rules chosen (per request)",
    "iec_emc_request_functional_modes": "EUT functional/operating modes (mode_value feeds the datasheet Test Mode)",
    "iec_emc_request_test_standards": "basic test standards per test per request",
    "iec_emc_request_test_ce": "Conducted Emission test parameters captured on the request",
    "iec_emc_request_test_re": "Radiated Emission test parameters captured on the request",
    "iec_emc_request_test_esd": "ESD test parameters captured on the request",
    "iec_emc_request_test_harmonic": "Harmonic-current test parameters captured on the request",
    "iec_emc_request_test_flicker": "Voltage flicker test parameters captured on the request",
    "iec_emc_request_test_rs": "Radiated Susceptibility test parameters captured on the request",
    "iec_emc_request_test_eft": "EFT/Burst test parameters captured on the request",
    "iec_emc_request_test_surge": "Surge test parameters captured on the request",
    "iec_emc_request_test_crf": "Conducted RF immunity test parameters captured on the request",
    "iec_emc_request_test_power_freq": "Power-frequency magnetic field test parameters captured on the request",
    "iec_emc_request_test_voltage_dips": "Voltage dips/interruptions test parameters captured on the request",
}

# Non-empty tables we still exclude from the NL->SQL surface.
EXCLUDE = {
    "iec_emc_test_requests",           # legacy orphan; name-collides with iec_emc_request_tests
    "equipment_audit_log",             # near-empty raw audit log
    "iec_emc_request_test_rs_interim", # transient scratch data
}

# Tables whose SELECT * is refused because they carry credential columns.
DENIED_STAR = ("users",)

_ENUMISH = re.compile(
    r"status|type|code|role|mode|class|result|state|level|category|active|verdict|decision", re.I)

# Credential-ish columns are omitted from the catalog text entirely (sql_guard
# additionally blocks them at validation time - this just keeps them out of
# the model's sight).
_HIDDEN_COLUMN = re.compile(
    r"password|\bpwd\b|secret|api_key|(?:reset|auth|session|csrf|access|refresh)_token", re.I)

_JOIN_HINTS = """Core relationships (use these joins):
- iec_emc_requests is the MASTER EMC request (one row per TCO / job_number).
  Every iec_emc_request_* child table joins via request_id -> iec_emc_requests.id.
- iec_emc_request_tests lists the tests per request: test_code values include
  CE, RE, EFT, ESD, SURGE, VOLTAGEDIPS, HARMONIC, VOLTAGEFLICKER, CRF, PFMF,
  RS_RI. is_selected=1 means the test is in scope for that request.
- planner_entries is the lab schedule: joins to iec_emc_requests via tco_id
  (string) and to users via engineer_user_id / peer_reviewer_user_id /
  datasheet_uploaded_by. status lifecycle: scheduled -> in_progress ->
  'Peer Review' (sent for review) -> datasheet_uploaded (approved);
  cancelled is terminal. datasheet_file_path is the generated .docx.
- datasheet_records stores the saved datasheet FORM per planner entry
  (planner_entry_id -> planner_entries.id): status 'Not Submitted' = draft,
  'Submitted' = sent for review; form_json holds the raw form (avoid selecting
  it unless asked - it is large).
- Legacy pipeline (older, mostly historical): test_requests -> test_plans ->
  test_datasheets.
- users.role is one of: user, lab_engineer, admin."""


def _short_type(t):
    return re.sub(r"\((?:\d+|\d+,\d+)\)", "", t or "").strip()


def introspect(conn, database):
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    names = sorted(r[0] for r in cur.fetchall())
    tables = []
    for name in names:
        if name in EXCLUDE:
            continue
        cur.execute("SELECT COUNT(*) FROM `%s`" % name)
        rows = cur.fetchone()[0]
        if rows == 0:
            continue
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
            (database, name))
        cols = [(c, _short_type(t), k) for c, t, k in cur.fetchall()]
        cur.execute(
            "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (database, name))
        fks = {c: (rt, rc) for c, rt, rc in cur.fetchall()}
        enums = {}
        for c, t, _k in cols:
            if not _ENUMISH.search(c):
                continue
            if not (t.startswith("varchar") or t.startswith("enum") or t.startswith("char")):
                continue
            cur.execute("SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IS NOT NULL LIMIT 16"
                        % (c, name, c))
            vals = [str(r[0]) for r in cur.fetchall()]
            if 0 < len(vals) <= 15:
                enums[c] = sorted(vals)
        tables.append({"name": name, "rows": rows, "cols": cols,
                       "fks": fks, "enums": enums})
    return tables


def render_table_text(t):
    lines = ["### %s (%d rows) - %s"
             % (t["name"], t["rows"], PURPOSES.get(t["name"], "supporting table"))]
    parts = []
    for c, typ, key in t["cols"]:
        if _HIDDEN_COLUMN.search(c):
            continue  # never shown to the model
        piece = "%s %s" % (c, typ)
        if key == "PRI":
            piece += " PK"
        if c in t["fks"]:
            piece += " ->%s.%s" % t["fks"][c]
        parts.append(piece)
    lines.append("  columns: " + "; ".join(parts))
    for c, vals in sorted(t["enums"].items()):
        pretty = ", ".join("'%s'" % v for v in vals)
        lines.append("  %s values: %s" % (c, pretty))
    return "\n".join(lines)


def build_module_text(tables):
    allowed = tuple(t["name"] for t in tables)
    catalog = "\n\n".join(render_table_text(t) for t in tables)
    return '''# -*- coding: utf-8 -*-
"""AUTO-GENERATED schema catalog for the NL->SQL agent.

Do not edit by hand - regenerate after schema changes with:

    python -m nlp_search.build_catalog

ALLOWED_TABLES drives the sql_guard allowlist; catalog_prompt_text() is
embedded in the orchestrator's system prompt.
"""

ALLOWED_TABLES = %r

# SELECT * is refused on these (they carry credential columns).
DENIED_STAR_TABLES = %r

_JOIN_HINTS = %r

_TABLES_TEXT = %r


def catalog_prompt_text():
    return _JOIN_HINTS + "\\n\\n" + _TABLES_TEXT
''' % (allowed, tuple(DENIED_STAR), _JOIN_HINTS, catalog)


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import pymysql
    import mysql_config  # noqa: F401 - loads .env into os.environ
    cfg = mysql_config.config["default"]
    conn = pymysql.connect(host=cfg.MYSQL_HOST, port=int(cfg.MYSQL_PORT),
                           user=cfg.MYSQL_USER, password=cfg.MYSQL_PASSWORD,
                           database=cfg.MYSQL_DATABASE, charset="utf8mb4")
    try:
        tables = introspect(conn, cfg.MYSQL_DATABASE)
    finally:
        conn.close()
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_catalog.py")
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(build_module_text(tables))
    print("Wrote %s (%d tables)" % (out_path, len(tables)))
    for t in tables:
        print("  %-42s %6d rows" % (t["name"], t["rows"]))


if __name__ == "__main__":
    main()
