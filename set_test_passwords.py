"""Set a known password on ALL users (for local testing of imported data).

The imported dump's passwords are one-way scrypt hashes and can't be recovered,
so this resets every account to a shared, known password using the app's own
hashing (so login's check_password matches).

Usage (from the project root, using the venv interpreter):
    .\.venv\Scripts\python.exe set_test_passwords.py            # uses Password@123
    .\.venv\Scripts\python.exe set_test_passwords.py MyPass123  # custom password
"""
import sys

from app import create_app
from models import db, User

PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "Password@123"


def main():
    app = create_app()
    with app.app_context():
        users = User.query.order_by(User.id).all()
        for user in users:
            user.set_password(PASSWORD)
        db.session.commit()
        print(f"Set password '{PASSWORD}' on {len(users)} user(s):")
        for user in users:
            print(f"  {user.role:13s} {user.email}  (username: {user.username})")


if __name__ == "__main__":
    main()
