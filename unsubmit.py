import sys
import os

sys.path.append(os.getcwd())

from sqlalchemy import text
from app import create_app
from models import db, PlannerEntry

def main():
    app = create_app()
    with app.app_context():
        # 1. Unsubmit all planner entries that are in 'datasheet_uploaded' status
        try:
            entries = PlannerEntry.query.filter_by(status='datasheet_uploaded').all()
            unsubmitted_count = 0
            for entry in entries:
                entry.status = 'in_progress'
                entry.datasheet_file_path = None
                entry.datasheet_uploaded_at = None
                entry.datasheet_uploaded_by = None
                entry.datasheet_comments = None
                unsubmitted_count += 1
                    
            db.session.commit()
            print(f"Successfully reset {unsubmitted_count} submitted planner entries back to 'in_progress' status.")
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting planner entries: {e}")

        # 2. Delete all draft/submitted datasheet records and their dependencies (undraft all)
        try:
            db.session.execute(text("DELETE FROM datasheet_equipment_used"))
            db.session.execute(text("DELETE FROM datasheet_modifications"))
            db.session.execute(text("DELETE FROM datasheet_software_used"))
            db.session.execute(text("DELETE FROM datasheet_records"))
            db.session.commit()
            print("Successfully deleted all draft/submitted datasheet records and dependencies for all test types from database.")
        except Exception as e:
            db.session.rollback()
            print(f"Error deleting datasheet records: {e}")

if __name__ == '__main__':
    main()
