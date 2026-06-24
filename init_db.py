"""Create all database tables once, in a single process, before the app starts.

The app runs with Flask's debug reloader, which loads app.py in two processes; on a
fresh database their two create_all() calls race and fail with MySQL error 1684
('concurrent DDL'). Creating the schema here first makes the app's create_all() a
no-op (tables already exist), so there's no concurrent DDL.
"""
from app import create_app
from models import db

app = create_app()
with app.app_context():
    db.create_all()

print("Schema ready.")
