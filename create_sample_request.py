# -*- coding: utf-8 -*-
"""Seed one EMC request carrying EVERY test type, all assigned to one engineer.

    python create_sample_request.py                    # create it
    python create_sample_request.py --engineer 8       # a different engineer
    python create_sample_request.py --tco IEC-EMC-011  # a different TCO id
    python create_sample_request.py --delete           # remove it again

Why this exists: the seeded data is lopsided. Ten of the twelve test types
appear on a single job, most requests carry no datasheet at all, and one
engineer holds nearly everything. That is fine as a snapshot of real work and
useless as a test fixture - a query can look right against it for the wrong
reason. This gives a job where every test type is present exactly once and
every one has an engineer, so "how many tests is X assigned" has an answer you
can check by hand.

Test codes are the REQUEST-side spellings. The planner and the datasheet spell
four of them differently (FLICKER/VoltageFlicker, POWER_FREQ/PFMF,
VOLTAGE_DIPS/VoltageDips, RS/RS_RI), which is exactly the drift that makes a
naive join drop a third of the rows - see TEST_CODE_CANON in
nlp_search/semantics.py. Seeding the request side with the request spellings
keeps that difference real rather than papering over it.

Idempotent: creating a TCO that already exists replaces its tests rather than
duplicating them, so re-running is safe.
"""
import argparse
import datetime
import sys

# The twelve request-side test codes. Verified against the distinct values
# already present in iec_emc_request_tests.
TEST_CODES = (
    "CE",            # Conducted Emission
    "RE",            # Radiated Emission
    "ESD",           # Electrostatic Discharge
    "EFT",           # Electrical Fast Transient / Burst
    "SURGE",         # Surge
    "CRF",           # Conducted RF immunity
    "RS",            # Radiated Susceptibility        (planner: RS_RI)
    "RS_INTERIM",    # Radiated Susceptibility, interim - request-side only
    "HARMONIC",      # Harmonic current
    "FLICKER",       # Voltage flicker                (planner: VoltageFlicker)
    "POWER_FREQ",    # Power-frequency magnetic field (planner: PFMF)
    "VOLTAGE_DIPS",  # Voltage dips / interruptions   (planner: VoltageDips)
)

DEFAULT_TCO = "IEC-EMC-010"
DEFAULT_JOB = "TFS-EMC-2026-010"
DEFAULT_ENGINEER = 7          # Kondababu Arjilli; --engineer to change


def _now():
    return datetime.datetime.now()


def _connect(app):
    import pymysql
    cfg = app.config
    return pymysql.connect(
        host=cfg["MYSQL_HOST"], port=int(cfg.get("MYSQL_PORT") or 3306),
        user=cfg["MYSQL_USER"], password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DATABASE"], charset="utf8mb4", autocommit=False)


def _engineer(cur, engineer_id):
    cur.execute("SELECT id, username, role FROM users WHERE id = %s", (engineer_id,))
    row = cur.fetchone()
    if not row:
        sys.exit("No user with id %s. Pick one with --engineer." % engineer_id)
    if row[2] != "lab_engineer":
        print("  NOTE: user %s has role '%s', not lab_engineer - assigning anyway"
              % (row[1], row[2]))
    return row[0], row[1]


def _requester(cur):
    """Any admin, to own the request. Falls back to the lowest user id."""
    cur.execute("SELECT id, username, email FROM users WHERE role='admin' "
                "ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        return row
    cur.execute("SELECT id, username, email FROM users ORDER BY id LIMIT 1")
    return cur.fetchone()


def delete(app, tco):
    conn = _connect(app)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM iec_emc_requests WHERE tco_id = %s", (tco,))
            row = cur.fetchone()
            if not row:
                print("Nothing to delete - no request with tco_id %s." % tco)
                return
            rid = row[0]
            cur.execute("DELETE FROM iec_emc_request_tests WHERE request_id = %s", (rid,))
            n = cur.rowcount
            cur.execute("DELETE FROM iec_emc_requests WHERE id = %s", (rid,))
        conn.commit()
        print("Deleted request %s (id=%s) and its %d test rows." % (tco, rid, n))
    finally:
        conn.close()


def create(app, tco, job, engineer_id):
    conn = _connect(app)
    try:
        with conn.cursor() as cur:
            eng_id, eng_name = _engineer(cur, engineer_id)
            req_id_user, req_name, req_email = _requester(cur)
            now = _now()
            today = now.date()

            cur.execute("SELECT id FROM iec_emc_requests WHERE tco_id = %s", (tco,))
            existing = cur.fetchone()
            if existing:
                rid = existing[0]
                cur.execute("DELETE FROM iec_emc_request_tests WHERE request_id = %s",
                            (rid,))
                cur.execute(
                    "UPDATE iec_emc_requests SET job_number=%s, status=%s, "
                    "assigned_engineer_id=%s, assigned_engineer_name=%s, updated_at=%s "
                    "WHERE id=%s",
                    (job, "Assigned Lab Engineer", eng_id, eng_name, now, rid))
                print("Request %s already existed (id=%s) - replacing its tests."
                      % (tco, rid))
            else:
                cur.execute(
                    "INSERT INTO iec_emc_requests ("
                    "  user_id, tco_id, job_number, status, product_name, manufacturer,"
                    "  manufacturer_address, model_number, serial_number, test_samples,"
                    "  samples_available_in_lab, product_type, product_description,"
                    "  class_type, assigned_engineer_id, assigned_engineer_name,"
                    "  requester_name, requester_department, requester_group,"
                    "  requester_division, requester_site, requester_email,"
                    "  requester_contact, requester_designation, requester_date,"
                    "  requester_status, created_at, updated_at, submitted_at"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (req_id_user, tco, job, "Assigned Lab Engineer",
                     "Full-Scope EMC Sample Unit", "Thermo Fisher Scientific",
                     "Bangalore, India", "FSES-2026", "FSES-2026-0001", 1, "Yes",
                     "Laboratory Equipment",
                     "Sample request seeded with every EMC test type, for testing "
                     "the reporting and NL-search paths end to end.",
                     "Class B", eng_id, eng_name,
                     req_name, "IDT", "Engineering", "Analytical Instruments",
                     "Bangalore", req_email, "0000000000", "Engineer", today,
                     "Submitted", now, now, now))
                rid = cur.lastrowid
                print("Created request %s (id=%s), job %s." % (tco, rid, job))

            rows = [(rid, code, 1, 0, 4.0, "assigned", eng_id, eng_name,
                     today, today + datetime.timedelta(days=1), now, now)
                    for code in TEST_CODES]
            cur.executemany(
                "INSERT INTO iec_emc_request_tests ("
                "  request_id, test_code, is_selected, is_developmental,"
                "  planned_hours, workflow_status, assigned_engineer_id,"
                "  assigned_engineer_name, planned_start_date, planned_end_date,"
                "  created_at, updated_at"
                ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.test_code, t.is_selected, t.assigned_engineer_name "
                "FROM iec_emc_request_tests t WHERE t.request_id = %s "
                "ORDER BY t.test_code", (rid,))
            got = cur.fetchall()
        print("  %d tests, every one selected and assigned to %s (id=%s):"
              % (len(got), eng_name, eng_id))
        for code, sel, who in got:
            print("     %-14s selected=%s  %s" % (code, sel, who))
        return rid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tco", default=DEFAULT_TCO)
    ap.add_argument("--job", default=DEFAULT_JOB)
    ap.add_argument("--engineer", type=int, default=DEFAULT_ENGINEER,
                    help="users.id of the lab engineer to assign (default %d)"
                         % DEFAULT_ENGINEER)
    ap.add_argument("--delete", action="store_true",
                    help="remove the request and its tests instead")
    args = ap.parse_args()

    import app as app_module
    flask_app = app_module.create_app("default")
    with flask_app.app_context():
        if args.delete:
            delete(flask_app, args.tco)
        else:
            create(flask_app, args.tco, args.job, args.engineer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
