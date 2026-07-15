#!/usr/bin/env python3
r"""
Scheduled task script to send equipment reminder emails.

This script should be run daily (via cron job or Windows Task Scheduler) to send
reminders for equipment due dates (Calibration, IC, Maintenance) based on EOU status.

Reminder schedule:
- EOU: 2 months, 1 month, 15 days, 1 week before due date(3 MONTHS)
- Non EOU: 1 month, 15 days, 1 week before due date(2 MONTHS)

Usage:
    python send_equipment_reminders.py

For cron (Linux):
    0 9 * * * /path/to/venv/bin/python /path/to/send_equipment_reminders.py

For Windows Task Scheduler:
    Create a daily task at 9:00 AM to run:
    C:\path\to\venv\Scripts\python.exe C:\path\to\send_equipment_reminders.py
"""

import logging
from app import create_app
import sys
import os

# Add the parent directory to the path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main function to send equipment reminder emails."""
    try:
        logger.info("=" * 60)
        logger.info("Starting equipment reminder email process...")
        logger.info("=" * 60)

        app = create_app()

        with app.app_context():
            # Call the reminder email function
            app.send_equipment_reminder_emails()

        logger.info("=" * 60)
        logger.info("Equipment reminder email process completed.")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"Error in equipment reminder script: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return 1


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
