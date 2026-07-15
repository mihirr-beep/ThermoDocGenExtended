"""Seed prefilled data for local/dev.

After `docker compose up`, run this to populate the database with working login
accounts (every role) plus a little sample equipment, so you can start working on
the app (e.g. the login flow) immediately.

Usage:
    docker compose up -d --build
    docker compose exec web python seed.py

Idempotent: safe to run repeatedly (matches on unique keys).
The app creates the schema on startup; this script only waits for the tables and
inserts rows (so it never races the app's create_all()).
All seeded users share the password:  Password@123
"""
import time
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import create_app
from models import db, User, Equipment

DEFAULT_PASSWORD = "Password@123"

# (username, email, role, is_active)
SEED_USERS = [
    ("admin",      "admin@local.test",      "admin",        True),
    ("engineer1",  "engineer1@local.test",  "lab_engineer", True),
    ("engineer2",  "engineer2@local.test",  "lab_engineer", True),
    ("requester1", "requester1@local.test", "user",         True),
    ("requester2", "requester2@local.test", "user",         True),
    ("inactive",   "inactive@local.test",   "user",         False),  # for testing deactivated login
]

# (asset_id, name, type, make, model_no, serial_no, location, test_type, eou_status)
SEED_EQUIPMENT = [
    ("EMC-RX-001",    "EMI Test Receiver", "Instrument", "Rohde & Schwarz", "ESR3",   "100123", "EMC Lab 1",  "EMC",    "EOU"),
    ("EMC-LISN-002",  "LISN (V-Network)",  "Instrument", "Rohde & Schwarz", "ENV216", "200456", "EMC Lab 1",  "EMC",    "Non EOU"),
    ("EMC-ESD-003",   "ESD Generator",     "Instrument", "Teseq",           "NSG438", "300789", "EMC Lab 2",  "EMC",    "Non EOU"),
    ("SAF-HIPOT-004", "Hipot Tester",      "Instrument", "Chroma",          "19055",  "400111", "Safety Lab", "Safety", "Non EOU"),
]


def _wait_for_schema(retries=60, delay=2):
    """Wait for DB connectivity AND for the schema to exist.

    The app creates the tables on startup; we just wait for them, so this script
    never issues DDL concurrently with the app. As a fallback (e.g. the app isn't
    running), create the tables ourselves after the wait window.
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            db.session.execute(text("SELECT 1 FROM users LIMIT 1"))
            return
        except (OperationalError, ProgrammingError) as exc:
            last = exc
            db.session.rollback()
            print(f"  waiting for database/schema... ({attempt}/{retries})")
            time.sleep(delay)
    print(f"  schema still missing ({getattr(last, 'orig', last)}); creating tables now...")
    db.create_all()


def seed_users():
    created = []
    for username, email, role, is_active in SEED_USERS:
        if User.query.filter((User.email == email) | (User.username == username)).first():
            continue
        user = User(username=username, email=email, role=role, is_active=is_active)
        user.set_password(DEFAULT_PASSWORD)
        db.session.add(user)
        created.append((username, email, role, is_active))
    db.session.commit()
    return created


def seed_equipment():
    created = []
    try:
        cal = date.today() - timedelta(days=30)
        due = date.today() + timedelta(days=335)
        for asset_id, name, etype, make, model_no, serial_no, location, test_type, eou in SEED_EQUIPMENT:
            if Equipment.query.filter_by(asset_id=asset_id).first():
                continue
            db.session.add(Equipment(
                asset_id=asset_id, name=name, type=etype, make=make, model_no=model_no,
                serial_no=serial_no, location=location, test_type=test_type, eou_status=eou,
                calibration_required="Yes", calibration_frequency="Annual",
                calibration_date=cal, calibration_due_date=due, status="Active",
            ))
            created.append(asset_id)
        db.session.commit()
    except Exception as exc:  # never let optional sample data block user seeding
        db.session.rollback()
        print(f"  (skipped equipment seed: {exc})")
    return created


def main():
    app = create_app()
    with app.app_context():
        _wait_for_schema()

        users = seed_users()
        equipment = seed_equipment()

        print("\n=== Seed complete ===")
        if users:
            print(f"Users created ({len(users)}) - password '{DEFAULT_PASSWORD}':")
            for username, email, role, is_active in users:
                flag = "" if is_active else "   [INACTIVE]"
                print(f"  {role:13s} {email:26s} (username: {username}){flag}")
        else:
            print("Users: already present, nothing to add.")
        print(f"Equipment created: {len(equipment)}" if equipment
              else "Equipment: already present, nothing to add.")
        print("\nOpen http://localhost:5000 - the Username field accepts the email or the short username.")


if __name__ == "__main__":
    main()
