import sys
import os
sys.path.append(os.getcwd())

from app import create_app
from sqlalchemy import text
from models import db, PlannerEntry

app = create_app()
with app.app_context():
    # 1. Unsubmit all planner entries for CE, RE, and Harmonic
    try:
        entries = PlannerEntry.query.filter(
            PlannerEntry.test_name.in_(['CE', 'RE', 'Harmonic', 'ce', 're', 'harmonic', 'HARMONIC'])
        ).all()
        
        unsubmitted_count = 0
        for entry in entries:
            if entry.status == 'datasheet_uploaded':
                entry.status = 'in_progress'
                entry.datasheet_file_path = None
                entry.datasheet_uploaded_at = None
                entry.datasheet_uploaded_by = None
                entry.datasheet_comments = None
                unsubmitted_count += 1
                
        db.session.commit()
        print(f"Successfully reset {unsubmitted_count} submitted CE/RE/Harmonic planner entries back to 'in_progress' status.")
    except Exception as e:
        db.session.rollback()
        print(f"Error resetting planner entries: {e}")

    # 2. Delete all CE, RE, and HARMONIC datasheet records (drafts) and their dependencies
    try:
        db.session.execute(text(
            "DELETE FROM datasheet_equipment_used WHERE datasheet_record_id IN "
            "(SELECT id FROM datasheet_records WHERE test_code IN ('CE', 'RE', 'HARMONIC'))"
        ))
        db.session.execute(text(
            "DELETE FROM datasheet_modifications WHERE datasheet_record_id IN "
            "(SELECT id FROM datasheet_records WHERE test_code IN ('CE', 'RE', 'HARMONIC'))"
        ))
        db.session.execute(text(
            "DELETE FROM datasheet_software_used WHERE datasheet_record_id IN "
            "(SELECT id FROM datasheet_records WHERE test_code IN ('CE', 'RE', 'HARMONIC'))"
        ))
        deleted_rows = db.session.execute(text(
            "DELETE FROM datasheet_records WHERE test_code IN ('CE', 'RE', 'HARMONIC')"
        ))
        db.session.commit()
        print("Successfully deleted all CE, RE, and HARMONIC draft records and dependencies from database.")
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting datasheet records: {e}")