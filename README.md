# EMC Test Plan Form Application

## Version: v0.10

A comprehensive web application for creating and managing EMC (Electromagnetic Compatibility) test plans with advanced form features, validation, and equipment management with automated reminder notifications.

## Features

### Core Functionality
- **Dynamic Test Plan Form**: Comprehensive form for EMC testing requirements
- **TCO Lookup**: Auto-fill form fields from previously submitted TCO numbers
- **Form Validation**: Advanced client-side and server-side validation
- **Progress Tracking**: Real-time form completion progress indicator

### User Interface Enhancements
- **Quick Navigation Panel**: Floating navigation with auto-hide functionality
- **Responsive Design**: Optimized for desktop, tablet, and mobile devices
- **Info Icons**: Contextual help with equipment classification definitions
- **Form Progress Indicator**: Visual progress bar with completion details

### Test Specifications
- **Custom Test Options**: Advanced configuration for various EMC tests
- **Conditional Fields**: Dynamic field requirements based on user selections
- **Multiple Test Types**: Support for CE, RE, ESD, RS/RI, EFT, Surge, CRF, and more
- **Custom Specifications**: Flexible input options for specialized test requirements

### Data Management
- **User Authentication**: Secure login and registration system
- **Database Integration**: MySQL database for data persistence
- **File Generation**: Automated test plan document generation
- **Export Options**: Multiple output formats for test plans

### Equipment Management
- **Equipment Tracking**: Comprehensive equipment database with calibration, IC, and maintenance records
- **EOU Status**: Track Export Oriented Unit (EOU) status for equipment
- **Automated Reminders**: Email reminders for calibration, IC, and maintenance due dates
- **Reminder Schedule**: 
  - EOU equipment: Reminders at 2 months, 1 month, 15 days, and 1 week before due date
  - Non-EOU equipment: Reminders at 1 month, 15 days, and 1 week before due date
- **Email Notifications**: Automatic emails to lab engineers with CC to admins

## Technical Stack

- **Backend**: Python Flask
- **Frontend**: HTML5, CSS3 (Tailwind CSS), JavaScript
- **Database**: MySQL
- **Authentication**: Flask-Login with session management
- **Document Generation**: Python-docx for Word document creation

## Installation

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Configure database settings in `mysql_config.py`
4. Run database migrations (if needed)
5. Run the application: `python app.py`

## Equipment Reminder Emails Setup

The application includes an automated email reminder system for equipment due dates. To enable automatic reminders:

### Windows Task Scheduler
1. Run `setup_equipment_reminders_task.ps1` as Administrator, or
2. Manually create a scheduled task to run `send_equipment_reminders.py` daily at 9:00 AM

### Linux/Unix (Cron)
Add to crontab:
```bash
0 9 * * * cd /path/to/Test\ Workflow && /path/to/venv/bin/python send_equipment_reminders.py
```

The reminder system will automatically:
- Check equipment for calibration, IC, and maintenance due dates
- Send email reminders based on EOU status and reminder schedule
- Email all active lab engineers (To) and admins (CC)

## Recent Updates

### Version 0.10
- **Equipment Management Enhancements:**
  - Added EOU Status field to equipment form (EOU/Non EOU options)
  - Added "Safety" and "General" options to Test Name field in equipment form
  - Implemented automated email reminder system for equipment due dates
  - Reminders sent to all lab engineers with CC to admins
  - Reminder schedule based on EOU status (EOU: 60/30/15/7 days, Non-EOU: 30/15/7 days)
  - Separate email notifications for Calibration, IC, and Maintenance reminders
- **JavaScript Fixes:**
  - Fixed JavaScript syntax errors and missing closing braces
  - Improved form toggle functionality for test request creation
  - Enhanced signature pad initialization with better error handling
  - Fixed conformity statement element references
  - Improved error handling for missing DOM elements
  - Added graceful fallbacks for form initialization
  - Enhanced console logging for debugging

### Version 0.0.9
- Added info icons with equipment classification definitions
- Implemented Quick Navigation panel with auto-hide functionality
- Enhanced form validation with conditional mandatory fields
- Added TCO lookup functionality for auto-filling forms
- Improved test specification options with custom inputs
- Added comprehensive form progress tracking
- Enhanced responsive design for better mobile experience

## Project Structure

```
├── app.py                 # Main Flask application
├── auth_routes.py         # Authentication routes
├── models.py             # Database models
├── requirements.txt      # Python dependencies
├── templates/            # HTML templates
│   ├── index.html        # Main test request form
│   ├── base.html         # Base template
│   ├── admin_approval.html
│   ├── equipment.html
│   ├── planner.html
│   ├── review.html
│   └── users.html
├── utils/               # Utility functions
│   └── document_processor.py
├── doc/                 # Documentation
│   └── VERSION          # Version information
├── outputs/             # Generated documents
├── send_equipment_reminders.py  # Scheduled task for equipment reminders
└── setup_equipment_reminders_task.ps1  # Windows Task Scheduler setup script
```

## Contributing

Please ensure all changes are tested and documented before submitting pull requests.

## License

This project is proprietary software developed for internal use.
