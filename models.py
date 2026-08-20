"""
Database models for the Test Request Generator application.

This module contains SQLAlchemy models for users, equipment, test requests,
test plans, test datasheets, and audit logging.
"""

import ast
import json
import re
from datetime import datetime, timezone, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now():
    """Get current datetime in IST (Indian Standard Time)."""
    return datetime.now(IST)


class User(db.Model, UserMixin):
    """User model for authentication and authorization."""

    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True,
                         nullable=False, index=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum('user', 'lab_engineer', 'admin'), default='user',
                     nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=get_ist_now)
    last_login = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    reset_token = db.Column(db.String(100), unique=True, nullable=True)
    reset_token_expires = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<User {self.username}>'

    def set_password(self, password: str) -> None:
        """Set password hash."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Check password against hash."""
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        """Convert user object to dictionary."""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'created_at': self.created_at.isoformat(),
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'is_active': self.is_active
        }

    def update_last_login(self) -> None:
        """Update last login timestamp."""
        self.last_login = get_ist_now()


class Equipment(db.Model):
    """Equipment model for storing test equipment information."""

    __tablename__ = 'equipment'

    id = db.Column(db.Integer, primary_key=True)
    sl_no = db.Column(db.Integer, nullable=True,
                      index=True)  # SL No (optional)
    asset_id = db.Column(db.String(100), unique=True,
                         nullable=False, index=True)  # Asset ID
    # Type (Instrument/Equipment/Accessory)
    type = db.Column(db.String(50), index=True)
    calibration_status_col = db.Column(
        db.String(50), index=True)  # Calibration Status
    name = db.Column(db.String(200), nullable=False,
                     index=True)  # Equipment_Name
    make = db.Column(db.String(100), index=True)  # Make (Manufacturer)
    model_no = db.Column(db.String(200), index=True)  # Model No.
    serial_no = db.Column(db.String(100), index=True)  # Serial_No
    location = db.Column(db.String(100), index=True)  # Location
    test_type = db.Column(db.String(100), index=True)  # EMC/Safety
    # EOU Status (EOU/Non EOU)
    eou_status = db.Column(db.String(20), index=True)
    # Calibration Required (Yes/No)
    calibration_required = db.Column(db.String(10), index=True)
    calibration_frequency = db.Column(
        db.String(50), index=True)  # Calibration Frequency
    calibration_date = db.Column(db.Date, index=True)  # Calibration Date
    calibration_due_date = db.Column(
        db.Date, index=True)  # Calibration Due Date
    # Intermediated Check(IC) Required
    ic_required = db.Column(db.String(10), index=True)
    ic_date = db.Column(db.Date, index=True)  # IC Date
    ic_due_date = db.Column(db.Date, index=True)  # IC Due Date
    maintenance_required = db.Column(
        db.String(10), index=True)  # Maintenance Required
    maintenance_date = db.Column(db.Date, index=True)  # Maintenance Date
    maintenance_due_date = db.Column(
        db.Date, index=True)  # Maintenance Due Date
    manufacturer_calibration_params = db.Column(
        db.Text)  # Manufacturers Calibration Parameters
    calibration_agency_params = db.Column(
        db.Text)  # Calibration Agency Parameters
    document_link = db.Column(db.String(500), nullable=True)  # Document Link
    # Test Name (comma-separated values or single value)
    test_name = db.Column(db.String(500), nullable=True)
    # Status (derived from calibration_status)
    status = db.Column(db.String(20), default='Active', index=True)
    created_at = db.Column(db.DateTime, default=get_ist_now)
    updated_at = db.Column(
        db.DateTime, default=get_ist_now, onupdate=get_ist_now)

    # Relationships
    maintenance_records = db.relationship(
        'Maintenance',
        back_populates='equipment',
        lazy=True,
        cascade='all, delete-orphan'
    )

    def __init__(self, sl_no=None, asset_id=None, type=None, calibration_status_col=None,
                 name=None, make=None, model_no=None, serial_no=None, location=None,
                 test_type=None, eou_status=None, calibration_required=None, calibration_frequency=None, calibration_date=None,
                 calibration_due_date=None, ic_required=None, ic_date=None, ic_due_date=None,
                 maintenance_required=None, maintenance_date=None, maintenance_due_date=None,
                 manufacturer_calibration_params=None, calibration_agency_params=None,
                 document_link=None, test_name=None, status='Active', **kwargs):
        super().__init__(**kwargs)
        if sl_no is not None:
            self.sl_no = sl_no
        if asset_id is not None:
            self.asset_id = asset_id
        if type is not None:
            self.type = type
        if calibration_status_col is not None:
            self.calibration_status_col = calibration_status_col
        if name is not None:
            self.name = name
        if make is not None:
            self.make = make
        if model_no is not None:
            self.model_no = model_no
        if serial_no is not None:
            self.serial_no = serial_no
        if location is not None:
            self.location = location
        if test_type is not None:
            self.test_type = test_type
        if eou_status is not None:
            self.eou_status = eou_status
        if calibration_required is not None:
            self.calibration_required = calibration_required
        if calibration_frequency is not None:
            self.calibration_frequency = calibration_frequency
        if calibration_date is not None:
            self.calibration_date = calibration_date
        if calibration_due_date is not None:
            self.calibration_due_date = calibration_due_date
        if ic_required is not None:
            self.ic_required = ic_required
        if ic_date is not None:
            self.ic_date = ic_date
        if ic_due_date is not None:
            self.ic_due_date = ic_due_date
        if maintenance_required is not None:
            self.maintenance_required = maintenance_required
        if maintenance_date is not None:
            self.maintenance_date = maintenance_date
        if maintenance_due_date is not None:
            self.maintenance_due_date = maintenance_due_date
        if manufacturer_calibration_params is not None:
            self.manufacturer_calibration_params = manufacturer_calibration_params
        if calibration_agency_params is not None:
            self.calibration_agency_params = calibration_agency_params
        if document_link is not None:
            self.document_link = document_link
        if test_name is not None:
            self.test_name = test_name
        if status is not None:
            self.status = status

    def __repr__(self):
        return f'<Equipment {self.asset_id} - {self.name}>'

    def to_dict(self):
        """Convert equipment object to dictionary."""
        return {
            'id': self.id,
            'sl_no': self.sl_no,
            'asset_id': self.asset_id,
            'type': self.type,
            'calibration_status': self.calibration_status_col,
            'calibration_status_col': self.calibration_status_col,
            'name': self.name,
            'make': self.make,
            'model_no': self.model_no,
            'serial_no': self.serial_no,
            'location': self.location,
            'test_type': self.test_type,
            'eou_status': getattr(self, 'eou_status', None),
            'calibration_required': self.calibration_required,
            'calibration_frequency': self.calibration_frequency,
            'calibration_date': (
                self.calibration_date.isoformat()
                if self.calibration_date else None
            ),
            'calibration_due_date': (
                self.calibration_due_date.isoformat()
                if self.calibration_due_date else None
            ),
            'ic_required': self.ic_required,
            'ic_date': (
                self.ic_date.isoformat()
                if self.ic_date else None
            ),
            'ic_due_date': (
                self.ic_due_date.isoformat()
                if self.ic_due_date else None
            ),
            'maintenance_required': self.maintenance_required,
            'maintenance_date': (
                self.maintenance_date.isoformat()
                if self.maintenance_date else None
            ),
            'maintenance_due_date': (
                self.maintenance_due_date.isoformat()
                if self.maintenance_due_date else None
            ),
            'maintenance_records': [
                record.to_dict() for record in getattr(self, 'maintenance_records', [])
            ],
            'manufacturer_calibration_params': self.manufacturer_calibration_params,
            'calibration_agency_params': self.calibration_agency_params,
            'document_link': self.document_link,
            'test_name': self.test_name,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

    @hybrid_property
    def is_calibrated(self):
        """Check if equipment is currently calibrated."""
        if not self.calibration_due_date:
            return False
        return get_ist_now().date() <= self.calibration_due_date

    @hybrid_property
    def calibration_status(self):
        """Get calibration status description."""
        if self.calibration_status_col:
            return self.calibration_status_col
        if not self.calibration_due_date:
            return 'Not Calibrated'
        elif self.is_calibrated:
            days_remaining = (self.calibration_due_date -
                              get_ist_now().date()).days
            if days_remaining > 30:
                return 'Calibrated'
            elif days_remaining > 0:
                return f'Expires in {days_remaining} days'
            else:
                return 'Calibration Expired'
        else:
            return 'Calibration Expired'


class Maintenance(db.Model):
    """Maintenance model for storing multiple maintenance records per equipment."""

    __tablename__ = 'maintenance'

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey(
        'equipment.id'), nullable=False, index=True)
    maintenance_required = db.Column(
        db.String(10), index=True)  # Maintenance Required
    maintenance_date = db.Column(db.Date, index=True)  # Maintenance Date
    maintenance_due_date = db.Column(
        db.Date, index=True)  # Maintenance Due Date
    created_at = db.Column(db.DateTime, default=get_ist_now)
    updated_at = db.Column(
        db.DateTime, default=get_ist_now, onupdate=get_ist_now)

    # Relationship
    equipment = db.relationship(
        'Equipment',
        back_populates='maintenance_records',
        lazy=True
    )

    def __init__(self, equipment_id=None, maintenance_required=None, maintenance_date=None,
                 maintenance_due_date=None, **kwargs):
        super().__init__(**kwargs)
        if equipment_id is not None:
            self.equipment_id = equipment_id
        if maintenance_required is not None:
            self.maintenance_required = maintenance_required
        if maintenance_date is not None:
            self.maintenance_date = maintenance_date
        if maintenance_due_date is not None:
            self.maintenance_due_date = maintenance_due_date

    def __repr__(self):
        return f'<Maintenance {self.id} - Equipment {self.equipment_id}>'

    def to_dict(self):
        """Convert maintenance object to dictionary."""
        return {
            'id': self.id,
            'equipment_id': self.equipment_id,
            'maintenance_required': self.maintenance_required,
            'maintenance_date': (
                self.maintenance_date.isoformat()
                if self.maintenance_date else None
            ),
            'maintenance_due_date': (
                self.maintenance_due_date.isoformat()
                if self.maintenance_due_date else None
            ),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class EquipmentHistory(db.Model):
    """Equipment history model for tracking all changes to equipment."""

    __tablename__ = 'equipment_history'

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey(
        'equipment.id'), nullable=False, index=True)
    # 'created', 'updated', 'deleted'
    action_type = db.Column(db.String(20), nullable=False, index=True)
    changes = db.Column(db.Text)  # JSON string of changes
    notes = db.Column(db.Text)  # Optional notes about the change
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey(
        'users.id'), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime, default=get_ist_now, nullable=False, index=True)

    # Relationships
    equipment = db.relationship('Equipment', backref=db.backref(
        'history_records', lazy=True, cascade='all, delete-orphan'))
    changed_by = db.relationship('User', foreign_keys=[changed_by_user_id])

    def __init__(self, equipment_id=None, action_type=None, changes=None, notes=None, changed_by_user_id=None, **kwargs):
        super().__init__(**kwargs)
        if equipment_id is not None:
            self.equipment_id = equipment_id
        if action_type is not None:
            self.action_type = action_type
        if changes is not None:
            self.set_changes(changes)
        if notes is not None:
            self.notes = notes
        if changed_by_user_id is not None:
            self.changed_by_user_id = changed_by_user_id

    def __repr__(self):
        return f'<EquipmentHistory {self.id} - Equipment {self.equipment_id} - {self.action_type}>'

    def set_changes(self, changes_dict):
        """Set changes as JSON string."""
        if changes_dict:
            self.changes = json.dumps(changes_dict, default=str)
        else:
            self.changes = None

    def get_changes(self):
        """Get changes as dictionary."""
        if self.changes:
            return json.loads(self.changes)
        return {}

    def to_dict(self):
        """Convert history object to dictionary."""
        return {
            'id': self.id,
            'equipment_id': self.equipment_id,
            'action_type': self.action_type,
            'changes': self.get_changes(),
            'notes': self.notes,
            'changed_by_user_id': self.changed_by_user_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class TestRequest(db.Model):
    """Test request model for storing test plan creation information."""

    __tablename__ = 'test_requests'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # TCO ID: IEC-EMC-001, IEC-EMC-002, etc.
    tco_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    job_id = db.Column(db.String(50), unique=True, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer)
    extracted_data = db.Column(db.Text)  # JSON string
    status = db.Column(db.String(20), default='created', index=True)
    generated_files = db.Column(db.Text)  # JSON array of generated filenames
    user_ip = db.Column(db.String(45))
    processing_time = db.Column(db.Float)  # Processing time in seconds
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=get_ist_now)
    updated_at = db.Column(
        db.DateTime, default=get_ist_now, onupdate=get_ist_now)

    def __init__(self, user_id=None, tco_id=None, filename=None, original_filename=None,
                 file_size=None, extracted_data=None, status='created',
                 generated_files=None, user_ip=None, processing_time=None,
                 error_message=None, **kwargs):
        super().__init__(**kwargs)
        if user_id is not None:
            self.user_id = user_id
        if tco_id is not None:
            self.tco_id = tco_id
        if filename is not None:
            self.filename = filename
        if original_filename is not None:
            self.original_filename = original_filename
        if file_size is not None:
            self.file_size = file_size
        if extracted_data is not None:
            self.extracted_data = extracted_data
        if status is not None:
            self.status = status
        if generated_files is not None:
            self.generated_files = generated_files
        if user_ip is not None:
            self.user_ip = user_ip
        if processing_time is not None:
            self.processing_time = processing_time
        if error_message is not None:
            self.error_message = error_message

    # Relationship
    user = db.relationship(
        'User', backref=db.backref('test_requests', lazy=True))

    def __repr__(self):
        return f'<TestRequest {self.original_filename} - {self.status}>'

    def to_dict(self):
        """Convert test request object to dictionary."""
        return {
            'id': self.id,
            'user_id': self.user_id if hasattr(self, 'user_id') else None,
            'tco_id': self.tco_id,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'file_size': self.file_size,
            'created_at': self.created_at.isoformat(),
            'extracted_data': json.loads(self.extracted_data) if self.extracted_data else None,
            'status': self.status,
            'generated_files': json.loads(self.generated_files) if self.generated_files else [],
            'user_ip': self.user_ip,
            'processing_time': self.processing_time,
            'error_message': self.error_message,
            'updated_at': self.updated_at.isoformat()
        }

    def get_extracted_data(self):
        """Get parsed extracted data."""
        if self.extracted_data:
            return json.loads(self.extracted_data)
        return {}

    def set_extracted_data(self, data):
        """Set extracted data as JSON string."""
        self.extracted_data = json.dumps(data, default=str)

    def get_generated_files(self):
        """Get list of generated files."""
        if self.generated_files:
            return json.loads(self.generated_files)
        return []

    def add_generated_file(self, filename):
        """Add a generated file to the list."""
        files = self.get_generated_files()
        files.append(filename)
        self.generated_files = json.dumps(files)

    @hybrid_property
    def file_size_mb(self):
        """Get file size in megabytes."""
        if self.file_size:
            return round(self.file_size / (1024 * 1024), 2)
        return 0


class TestPlan(db.Model):
    """Test plan model for storing generated test plan information."""

    __tablename__ = 'test_plans'

    id = db.Column(db.Integer, primary_key=True)
    test_request_id = db.Column(db.Integer, db.ForeignKey(
        'test_requests.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(200))
    test_objective = db.Column(db.Text)
    test_scope = db.Column(db.Text)
    equipment_used = db.Column(db.Text)  # JSON array of equipment IDs
    test_methodology = db.Column(db.Text)
    safety_requirements = db.Column(db.Text)
    file_size = db.Column(db.Integer)

    def __init__(self, test_request_id=None, filename=None, title=None,
                 test_objective=None, test_scope=None, equipment_used=None,
                 test_methodology=None, safety_requirements=None, file_size=None, **kwargs):
        super().__init__(**kwargs)
        if test_request_id is not None:
            self.test_request_id = test_request_id
        if filename is not None:
            self.filename = filename
        if title is not None:
            self.title = title
        if test_objective is not None:
            self.test_objective = test_objective
        if test_scope is not None:
            self.test_scope = test_scope
        if equipment_used is not None:
            self.equipment_used = equipment_used
        if test_methodology is not None:
            self.test_methodology = test_methodology
        if safety_requirements is not None:
            self.safety_requirements = safety_requirements
        if file_size is not None:
            self.file_size = file_size

    # Relationship
    test_request = db.relationship(
        'TestRequest', backref=db.backref('test_plans', lazy=True))

    def __repr__(self):
        return f'<TestPlan {self.title} - {self.filename}>'

    def to_dict(self):
        """Convert test plan object to dictionary."""
        return {
            'id': self.id,
            'test_request_id': self.test_request_id,
            'filename': self.filename,
            'title': self.title,
            'test_objective': self.test_objective,
            'test_scope': self.test_scope,
            'equipment_used': (
                json.loads(self.equipment_used) if self.equipment_used else []
            ),
            'test_methodology': self.test_methodology,
            'safety_requirements': self.safety_requirements,
            'file_size': self.file_size
        }


class TestDatasheet(db.Model):
    """Test datasheet model for storing generated test datasheet information."""

    __tablename__ = 'test_datasheets'

    id = db.Column(db.Integer, primary_key=True)
    test_request_id = db.Column(db.Integer, db.ForeignKey(
        'test_requests.id'), nullable=False)
    test_plan_id = db.Column(db.Integer, db.ForeignKey(
        'test_plans.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    test_name = db.Column(db.String(200), nullable=False)
    test_description = db.Column(db.Text)
    test_parameters = db.Column(db.Text)  # JSON object
    measurement_points = db.Column(db.Text)  # JSON array
    equipment_required = db.Column(db.Text)  # JSON array of equipment IDs
    test_procedure = db.Column(db.Text)
    data_recording_sections = db.Column(db.Text)  # JSON array
    file_size = db.Column(db.Integer)

    def __init__(self, test_request_id=None, test_plan_id=None, filename=None,
                 test_name=None, test_description=None, test_parameters=None,
                 measurement_points=None, equipment_required=None,
                 test_procedure=None, data_recording_sections=None, file_size=None, **kwargs):
        super().__init__(**kwargs)
        if test_request_id is not None:
            self.test_request_id = test_request_id
        if test_plan_id is not None:
            self.test_plan_id = test_plan_id
        if filename is not None:
            self.filename = filename
        if test_name is not None:
            self.test_name = test_name
        if test_description is not None:
            self.test_description = test_description
        if test_parameters is not None:
            self.test_parameters = test_parameters
        if measurement_points is not None:
            self.measurement_points = measurement_points
        if equipment_required is not None:
            self.equipment_required = equipment_required
        if test_procedure is not None:
            self.test_procedure = test_procedure
        if data_recording_sections is not None:
            self.data_recording_sections = data_recording_sections
        if file_size is not None:
            self.file_size = file_size

    # Relationships
    test_request = db.relationship(
        'TestRequest', backref=db.backref('test_datasheets', lazy=True))
    test_plan = db.relationship(
        'TestPlan', backref=db.backref('test_datasheets', lazy=True))

    def __repr__(self):
        return f'<TestDatasheet {self.test_name} - {self.filename}>'

    def to_dict(self):
        """Convert test datasheet object to dictionary."""
        return {
            'id': self.id,
            'test_request_id': self.test_request_id,
            'test_plan_id': self.test_plan_id,
            'filename': self.filename,
            'test_name': self.test_name,
            'test_description': self.test_description,
            'test_parameters': (
                json.loads(
                    self.test_parameters) if self.test_parameters else {}
            ),
            'measurement_points': (
                json.loads(
                    self.measurement_points) if self.measurement_points else []
            ),
            'equipment_required': (
                json.loads(
                    self.equipment_required) if self.equipment_required else []
            ),
            'test_procedure': self.test_procedure,
            'data_recording_sections': (
                json.loads(self.data_recording_sections)
                if self.data_recording_sections else []
            ),
            'file_size': self.file_size
        }


class PlannerEntry(db.Model):
    """Planner entries for tracking engineer schedules."""

    __tablename__ = 'planner_entries'

    id = db.Column(db.Integer, primary_key=True)
    test_request_id = db.Column(db.Integer, db.ForeignKey(
        'iec_emc_requests.id'), nullable=True, index=True)
    test_person_name = db.Column(db.String(200), nullable=False, index=True)
    engineer_user_id = db.Column(db.Integer, db.ForeignKey(
        'users.id'), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey(
        'users.id'), nullable=True, index=True)
    test_name = db.Column(db.String(200), nullable=False)
    tco_id = db.Column(db.String(50), nullable=True, index=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    total_hours = db.Column(db.Float, nullable=True)
    event_description = db.Column(db.Text, nullable=True)
    event_type = db.Column(db.String(50), nullable=True)
    recurrence = db.Column(db.String(20), nullable=True)
    recurrence_end_date = db.Column(db.Date, nullable=True)
    is_all_day = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default='in_progress',
                       nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, default=get_ist_now, nullable=False)
    updated_at = db.Column(db.DateTime, default=get_ist_now,
                           onupdate=get_ist_now, nullable=False)

    # Datasheet/Report upload fields
    datasheet_file_path = db.Column(db.String(500), nullable=True)
    datasheet_uploaded_at = db.Column(db.DateTime, nullable=True)
    datasheet_uploaded_by = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=True)
    peer_reviewer_user_id = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    peer_review_assigned_at = db.Column(db.DateTime, nullable=True)
    datasheet_comments = db.Column(db.Text, nullable=True)
    completion_date = db.Column(db.Date, nullable=True)  # <-- ADD THIS

    # Cancellation fields
    cancel_reason = db.Column(db.Text, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    cancelled_by = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=True)
    # Final Report upload fields
    report_file_path = db.Column(db.String(500), nullable=True)
    report_comments = db.Column(db.Text, nullable=True)
    report_uploaded_at = db.Column(db.DateTime, nullable=True)
    report_uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    report_access_granted = db.Column(db.Boolean, default=False, nullable=False)
    report_access_granted_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<PlannerEntry {self.test_person_name} {self.test_name} {self.start_date} - {self.end_date}>'

    def to_dict(self) -> dict:
        """Serialize planner entry."""
        job_number = None
        service_types = []
        request = None

        if self.test_request_id:
            try:
                request = db.session.get(EMCRequest, self.test_request_id)
                if request:
                    job_number = request.job_number
                    service_types = [
                        str(getattr(item, 'service_type', '') or '').strip()
                        for item in getattr(request, 'service_types', []) or []
                        if str(getattr(item, 'service_type', '') or '').strip()
                    ]
            except Exception:
                pass

        if not job_number and self.tco_id:
            try:
                request = EMCRequest.query.filter_by(tco_id=self.tco_id).first()
                if request:
                    job_number = request.job_number
                    if not service_types:
                        service_types = [
                            str(getattr(item, 'service_type', '') or '').strip()
                            for item in getattr(request, 'service_types', []) or []
                            if str(getattr(item, 'service_type', '') or '').strip()
                        ]
            except Exception:
                pass

        return {
            'id': self.id,
            'test_request_id': self.test_request_id,
            'test_person_name': self.test_person_name,
            'engineer_user_id': self.engineer_user_id,
            'created_by_user_id': self.created_by_user_id,
            'test_name': self.test_name,
            'tco_id': self.tco_id,
            'job_number': job_number,
            'service_types': service_types,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'start_time': self.start_time.strftime('%H:%M') if self.start_time else None,
            'end_time': self.end_time.strftime('%H:%M') if self.end_time else None,
            'total_hours': float(self.total_hours) if self.total_hours is not None else None,
            'event_description': self.event_description,
            'event_type': self.event_type,
            'recurrence': self.recurrence,
            'recurrence_end_date': self.recurrence_end_date.isoformat() if self.recurrence_end_date else None,
            'is_all_day': bool(self.is_all_day),
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'datasheet_file_path': self.datasheet_file_path,
            'datasheet_uploaded_at': self.datasheet_uploaded_at.isoformat() if self.datasheet_uploaded_at else None,
            'datasheet_uploaded_by': self.datasheet_uploaded_by,
            'peer_reviewer_user_id': self.peer_reviewer_user_id,
            'peer_review_assigned_at': self.peer_review_assigned_at.isoformat() if self.peer_review_assigned_at else None,
            'datasheet_comments': self.datasheet_comments,
            'completion_date': self.completion_date.isoformat() if self.completion_date else None,
            'cancel_reason': self.cancel_reason,
            'cancelled_at': self.cancelled_at.isoformat() if self.cancelled_at else None,
        }


class TimestampMixin:
    """Reusable created/updated timestamp columns."""

    created_at = db.Column(db.DateTime, default=get_ist_now, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=get_ist_now,
        onupdate=get_ist_now,
        nullable=False
    )


class EMCRequest(TimestampMixin, db.Model):
    """Normalized parent request record for IEC/EMC workflows."""

    __tablename__ = 'iec_emc_requests'

    id = db.Column(db.Integer, primary_key=True)
    legacy_request_id = db.Column(db.Integer, unique=True, nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    tco_id = db.Column(db.String(50), unique=True, nullable=True, index=True)
    job_id = db.Column(db.String(50), unique=True, nullable=True, index=True)
    status = db.Column(db.String(50), default='Draft', nullable=False, index=True)

    product_name = db.Column(db.String(200), nullable=False)
    manufacturer = db.Column(db.String(200), nullable=False)
    manufacturer_address = db.Column(db.Text, nullable=False)
    model_number = db.Column(db.String(200), nullable=False)
    serial_number = db.Column(db.String(200), nullable=True)
    test_samples = db.Column(db.Integer, nullable=False, default=1)
    samples_available_in_lab = db.Column(db.String(50), nullable=False)

    has_model_variance = db.Column(db.String(10), nullable=True)
    model_variance = db.Column(db.String(500), nullable=True)
    model_variance_document = db.Column(LONGTEXT, nullable=True)
    project_details_intent = db.Column(db.Text, nullable=True)
    has_wireless_interface = db.Column(db.String(10), nullable=True)

    dimension_unit = db.Column(db.String(10), default='mm')
    weight = db.Column(db.Float, nullable=True)
    operating_frequency = db.Column(db.String(100), nullable=True)
    length = db.Column(db.Float, nullable=True)
    width = db.Column(db.Float, nullable=True)
    height = db.Column(db.Float, nullable=True)

    product_type = db.Column(db.String(50), nullable=True)
    type_others = db.Column(db.String(200), nullable=True)
    product_description = db.Column(db.Text, nullable=True)
    test_configuration = db.Column(db.Text, nullable=True)
    operation_modes = db.Column(db.Text, nullable=True)
    monitoring_parameters = db.Column(db.Text, nullable=True)
    additional_info = db.Column(db.Text, nullable=True)
    block_diagram = db.Column(LONGTEXT, nullable=True)

    product_environment_other = db.Column(db.String(200), nullable=True)
    product_group = db.Column(db.String(50), nullable=True)
    class_type = db.Column(db.String(50), nullable=True)

    continue_testing = db.Column(db.String(10), nullable=True)
    test_report_required = db.Column(db.String(10), nullable=True)
    uncertainty_required = db.Column(db.String(10), nullable=True)
    test_witness = db.Column(db.String(10), nullable=True)
    conformity_required = db.Column(db.String(10), nullable=True)
    conformity_statement = db.Column(db.String(10), nullable=True)
    number_of_modes = db.Column(db.Integer, nullable=True)

    requester_name = db.Column(db.String(200), nullable=False)
    requester_department = db.Column(db.String(200), nullable=False)
    requester_group = db.Column(db.String(200), nullable=False)
    requester_division = db.Column(db.String(200), nullable=False)
    requester_site = db.Column(db.String(200), nullable=False)
    requester_email = db.Column(db.String(200), nullable=False)
    requester_contact = db.Column(db.String(50), nullable=False)
    requester_designation = db.Column(db.String(200), nullable=False)
    requester_date = db.Column(db.Date, nullable=False)
    requester_expected_completion_date = db.Column(db.Date, nullable=True)
    requester_status = db.Column(db.String(20), default='At Review')
    requester_signature = db.Column(LONGTEXT, nullable=True)

    job_number = db.Column(db.String(100), nullable=True)
    sample_condition = db.Column(db.String(200), nullable=True)
    capability_available = db.Column(db.String(200), nullable=True)
    sample_received_date = db.Column(db.Date, nullable=True)
    test_duration = db.Column(db.String(100), nullable=True)
    test_commencement_date = db.Column(db.Date, nullable=True)
    test_completion_date = db.Column(db.Date, nullable=True)

    lab_manager_name = db.Column(db.String(200), nullable=True)
    lab_manager_date = db.Column(db.Date, nullable=True)
    lab_manager_signature = db.Column(LONGTEXT, nullable=True)
    lab_manager_signed_at = db.Column(db.DateTime, nullable=True)

    assigned_engineer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    assigned_engineer_name = db.Column(db.String(200), nullable=True)
    assignment_priority = db.Column(db.String(20), nullable=True)
    assignment_due_date = db.Column(db.Date, nullable=True)
    assignment_notes = db.Column(db.Text, nullable=True)

    rejection_reason = db.Column(db.Text, nullable=True)
    rejected_by = db.Column(db.String(200), nullable=True)
    rejected_at = db.Column(db.DateTime, nullable=True)

    review_comments = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.String(200), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    plan_update_history = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship(
        'User',
        foreign_keys=[user_id],
        backref=db.backref('normalized_emc_requests', lazy=True)
    )
    assigned_engineer = db.relationship(
        'User',
        foreign_keys=[assigned_engineer_id],
        backref=db.backref('normalized_assigned_emc_requests', lazy=True),
        uselist=False
    )

    service_types = db.relationship(
        'EMCRequestServiceType',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestServiceType.sort_order',
        lazy=True
    )
    serial_numbers = db.relationship(
        'EMCRequestSerialNumber',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestSerialNumber.sort_order',
        lazy=True
    )
    additional_models = db.relationship(
        'EMCRequestAdditionalModel',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestAdditionalModel.sort_order',
        lazy=True
    )
    categories = db.relationship(
        'EMCRequestCategory',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestCategory.sort_order',
        lazy=True
    )
    accessories = db.relationship(
        'EMCRequestAccessory',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestAccessory.sort_order',
        lazy=True
    )
    cables = db.relationship(
        'EMCRequestCable',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestCable.sort_order',
        lazy=True
    )
    eut_specs = db.relationship(
        'EMCRequestEUTSpec',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestEUTSpec.sort_order',
        lazy=True
    )
    supply_vf_values = db.relationship(
        'EMCRequestSupplyVF',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestSupplyVF.sort_order',
        lazy=True
    )
    wireless_values = db.relationship(
        'EMCRequestWireless',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestWireless.sort_order',
        lazy=True
    )
    product_standards = db.relationship(
        'EMCRequestProductStandard',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestProductStandard.sort_order',
        lazy=True
    )
    product_environments = db.relationship(
        'EMCRequestProductEnvironment',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestProductEnvironment.sort_order',
        lazy=True
    )
    decision_rules = db.relationship(
        'EMCRequestDecisionRule',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestDecisionRule.sort_order',
        lazy=True
    )
    functional_modes = db.relationship(
        'EMCRequestFunctionalMode',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestFunctionalMode.sort_order',
        lazy=True
    )
    tests = db.relationship(
        'EMCRequestTest',
        back_populates='request',
        cascade='all, delete-orphan',
        order_by='EMCRequestTest.id',
        lazy=True
    )

    @staticmethod
    def _safe_date_format(value):
        if value is None:
            return None
        try:
            return value.isoformat()
        except Exception:
            return None

    @staticmethod
    def _safe_json_parse(value, default=None):
        if value is None:
            return {} if default is None else default
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {} if default is None else default
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return text
        return value

    @staticmethod
    def _parse_maybe_json(value):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                try:
                    parsed = ast.literal_eval(text)
                    if isinstance(parsed, (dict, list)):
                        return parsed
                except (SyntaxError, ValueError):
                    pass
                return value
        return value

    @staticmethod
    def _legacy_test_key(code):
        mapping = {
            'CE': 'CE',
            'RE': 'RE',
            'ESD': 'ESD',
            'HARMONIC': 'Harmonic',
            'FLICKER': 'VoltageFlicker',
            'RS': 'RS_RI',
            'RS_INTERIM': 'RS_RI_Interim',
            'EFT': 'EFT',
            'SURGE': 'Surge',
            'CRF': 'CRF',
            'POWER_FREQ': 'PFMF',
            'VOLTAGE_DIPS': 'VoltageDips',
        }
        return mapping.get(code, code)

    @staticmethod
    def _canonical_test_code(value):
        normalized = re.sub(r'[^A-Za-z0-9_]+', '', str(value or '').upper())
        mapping = {
            'CE': 'CE',
            'RE': 'RE',
            'ESD': 'ESD',
            'HARMONIC': 'HARMONIC',
            'HARMONICCURRENTEMISSION': 'HARMONIC',
            'FLICKER': 'FLICKER',
            'VOLTAGEFLICKER': 'FLICKER',
            'VOLTAGECHANGES': 'FLICKER',
            'RS': 'RS',
            'RI': 'RS',
            'RSRI': 'RS',
            'RS_RI': 'RS',
            'RADIATEDSUSCEPTIBILITY': 'RS',
            'RSINTERIM': 'RS_INTERIM',
            'RSRIINTERIM': 'RS_INTERIM',
            'RS_RI_INTERIM': 'RS_INTERIM',
            'EFT': 'EFT',
            'SURGE': 'SURGE',
            'CRF': 'CRF',
            'PFMF': 'POWER_FREQ',
            'POWER': 'POWER_FREQ',
            'POWERFREQUENCYMAGNETICFIELD': 'POWER_FREQ',
            'POWERFREQUENCYMAGNETICFIELDIMMUNITY': 'POWER_FREQ',
            'VOLTAGEDIP': 'VOLTAGE_DIPS',
            'VOLTAGEDIPS': 'VOLTAGE_DIPS',
            'VOLTAGEDIPSSHORTINTERRUPTIONS': 'VOLTAGE_DIPS',
        }
        return mapping.get(normalized)

    def _ordered_values(self, rows, attr_name):
        return [
            getattr(row, attr_name) for row in sorted(rows, key=lambda item: item.sort_order)
            if getattr(row, attr_name, None) not in (None, '')
        ]

    def _ordered_structured_values(self, rows, attr_name):
        values = []
        for row in sorted(rows, key=lambda item: item.sort_order):
            raw_value = getattr(row, attr_name, None)
            if raw_value in (None, ''):
                continue
            values.append(self._parse_maybe_json(raw_value))
        return values

    def _product_environment_dict(self):
        result = {}
        for row in sorted(self.product_environments, key=lambda item: item.sort_order):
            result[row.environment_key] = self._parse_maybe_json(row.environment_value)
        return result

    def _tests_by_code(self):
        return {row.test_code: row for row in self.tests}

    def _selected_tests(self, *, developmental=False):
        selected = []
        for test in self.tests:
            if developmental and test.is_developmental:
                selected.append(self._legacy_test_key(test.test_code))
            elif not developmental and test.is_selected:
                selected.append(self._legacy_test_key(test.test_code))
        return selected

    def _test_hours_map(self):
        result = {}
        for test in self.tests:
            if test.planned_hours is not None:
                result[self._legacy_test_key(test.test_code)] = test.planned_hours
        return result

    def _test_remarks_map(self):
        result = {}
        for test in self.tests:
            if test.remarks:
                result[self._legacy_test_key(test.test_code)] = test.remarks
        return result

    def _test_assignments_payload(self):
        assignments = []
        for test in self.tests:
            if not (
                test.assigned_engineer_id
                or test.assigned_engineer_name
                or test.planned_start_date
                or test.planned_end_date
            ):
                continue
            assignments.append({
                'test_name': self._legacy_test_key(test.test_code),
                'test_code': test.test_code,
                'engineer_id': test.assigned_engineer_id,
                'engineer_name': test.assigned_engineer_name,
                'status': test.workflow_status,
                'start_date': self._safe_date_format(test.planned_start_date),
                'end_date': self._safe_date_format(test.planned_end_date),
                'hours': test.planned_hours,
            })
        return assignments

    @property
    def selected_tests(self):
        return self._selected_tests(developmental=False)

    @property
    def selected_tests_for_development(self):
        return self._selected_tests(developmental=True)

    @property
    def test_hours(self):
        return self._test_hours_map()

    @property
    def test_remarks(self):
        return self._test_remarks_map()

    @property
    def test_assignments(self):
        payload = self._test_assignments_payload()
        return json.dumps(payload, default=str) if payload else None

    @test_assignments.setter
    def test_assignments(self, value):
        assignments = self._safe_json_parse(value, default=[])
        if not isinstance(assignments, list):
            assignments = []

        tests_by_code = self._tests_by_code()
        for test in tests_by_code.values():
            test.assigned_engineer_id = None
            test.assigned_engineer_name = None
            test.workflow_status = None
            test.planned_start_date = None
            test.planned_end_date = None

        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            code = self._canonical_test_code(
                assignment.get('test_code') or assignment.get('test_name')
            )
            if not code:
                continue

            test = tests_by_code.get(code)
            if test is None:
                test = EMCRequestTest(test_code=code)
                self.tests.append(test)
                tests_by_code[code] = test

            test.assigned_engineer_id = assignment.get('engineer_id')
            test.assigned_engineer_name = assignment.get('engineer_name')
            test.workflow_status = assignment.get('status')

            start_date = assignment.get('start_date')
            if start_date:
                try:
                    test.planned_start_date = datetime.strptime(
                        str(start_date), '%Y-%m-%d'
                    ).date()
                except Exception:
                    pass

            end_date = assignment.get('end_date')
            if end_date:
                try:
                    test.planned_end_date = datetime.strptime(
                        str(end_date), '%Y-%m-%d'
                    ).date()
                except Exception:
                    pass

            hours = assignment.get('hours')
            if hours in (None, ''):
                hours = assignment.get('total_hours')
            if hours not in (None, ''):
                try:
                    test.planned_hours = float(hours)
                except (TypeError, ValueError):
                    pass

    def to_legacy_dict(self):
        """Serialize normalized requests into the legacy payload shape expected by the app."""
        tests = self._tests_by_code()
        selected_tests = self._selected_tests(developmental=False)
        selected_tests_for_development = self._selected_tests(developmental=True)
        test_hours = self._test_hours_map()
        test_remarks = self._test_remarks_map()

        result = {
            'id': self.legacy_request_id or self.id,
            'normalized_request_id': self.id,
            'legacy_request_id': self.legacy_request_id,
            'user_id': self.user_id,
            'tco_id': self.tco_id,
            'job_id': self.job_id,
            'status': self.status,
            'service_types': self._ordered_values(self.service_types, 'service_type'),
            'product_name': self.product_name,
            'manufacturer': self.manufacturer,
            'manufacturer_address': self.manufacturer_address,
            'model_number': self.model_number,
            'serial_number': self.serial_number,
            'serial_numbers': self._ordered_values(self.serial_numbers, 'serial_number'),
            'additional_models': self._ordered_values(self.additional_models, 'model_number'),
            'test_samples': self.test_samples,
            'samples_available_in_lab': self.samples_available_in_lab,
            'has_model_variance': self.has_model_variance,
            'model_variance': self.model_variance,
            'project_details_intent': self.project_details_intent,
            'model_variance_document': self.model_variance_document,
            'has_wireless_interface': self.has_wireless_interface,
            'dimension_unit': self.dimension_unit,
            'weight': self.weight,
            'operating_frequency': self.operating_frequency,
            'length': self.length,
            'width': self.width,
            'height': self.height,
            'category': self._ordered_values(self.categories, 'category_name'),
            'type': self.product_type,
            'type_others': self.type_others,
            'accessories': self._ordered_structured_values(self.accessories, 'accessory_value'),
            'cables': self._ordered_structured_values(self.cables, 'cable_value'),
            'eut_specs': self._ordered_structured_values(self.eut_specs, 'spec_value'),
            'supply_vf': self._ordered_structured_values(self.supply_vf_values, 'value_text'),
            'wireless': self._ordered_structured_values(self.wireless_values, 'value_text'),
            'product_description': self.product_description,
            'test_configuration': self.test_configuration,
            'operation_modes': self.operation_modes,
            'monitoring_parameters': self.monitoring_parameters,
            'additional_info': self.additional_info,
            'block_diagram': self.block_diagram,
            'product_standards': self._ordered_values(self.product_standards, 'standard_value'),
            'product_environment': self._product_environment_dict(),
            'product_environment_other': self.product_environment_other,
            'group': self.product_group,
            'class_type': self.class_type,
            'selected_tests': selected_tests,
            'selected_tests_for_development': selected_tests_for_development,
            'test_hours': test_hours,
            'test_remarks': test_remarks,
            'testHours': test_hours,
            'testRemarks': test_remarks,
            'testSelected': {name: name for name in selected_tests},
            'continue_testing': self.continue_testing,
            'test_report_required': self.test_report_required,
            'uncertainty_required': self.uncertainty_required,
            'test_witness': self.test_witness,
            'conformity_required': self.conformity_required,
            'conformity_statement': self.conformity_statement,
            'decision_rule': self._ordered_values(self.decision_rules, 'rule_value'),
            'number_of_modes': self.number_of_modes,
            'functional_modes': self._ordered_values(self.functional_modes, 'mode_value'),
            'requester_name': self.requester_name,
            'requester_department': self.requester_department,
            'requester_group': self.requester_group,
            'requester_division': self.requester_division,
            'requester_site': self.requester_site,
            'requester_email': self.requester_email,
            'requester_contact': self.requester_contact,
            'requester_designation': self.requester_designation,
            'requester_date': self._safe_date_format(self.requester_date),
            'requester_expected_completion_date': self._safe_date_format(self.requester_expected_completion_date),
            'requester_status': self.requester_status,
            'requester_signature': self.requester_signature,
            'requesterSignature': self.requester_signature,
            'job_number': self.job_number or '',
            'sample_condition': self.sample_condition,
            'capability_available': self.capability_available,
            'sample_received_date': self._safe_date_format(self.sample_received_date),
            'test_duration': self.test_duration,
            'test_commencement_date': self._safe_date_format(self.test_commencement_date),
            'test_completion_date': self._safe_date_format(self.test_completion_date),
            'lab_manager_name': self.lab_manager_name,
            'lab_manager_date': self._safe_date_format(self.lab_manager_date),
            'lab_manager_signature': self.lab_manager_signature,
            'lab_manager_signed_at': self._safe_date_format(self.lab_manager_signed_at),
            'assigned_engineer_id': self.assigned_engineer_id,
            'assigned_engineer_name': self.assigned_engineer_name,
            'assignment_priority': self.assignment_priority,
            'assignment_due_date': self._safe_date_format(self.assignment_due_date),
            'assignment_notes': self.assignment_notes,
            'test_assignments': self._test_assignments_payload(),
            'plan_update_history': self._safe_json_parse(self.plan_update_history, default=[]),
            'rejection_reason': self.rejection_reason,
            'rejected_by': self.rejected_by,
            'rejected_at': self._safe_date_format(self.rejected_at),
            'review_comments': self.review_comments,
            'reviewed_by': self.reviewed_by,
            'reviewed_at': self._safe_date_format(self.reviewed_at),
            'created_at': self._safe_date_format(self.created_at),
            'updated_at': self._safe_date_format(self.updated_at),
            'submitted_at': self._safe_date_format(self.submitted_at),
            'report_file_path': None,
            'report_comments': None,
            'report_uploaded_at': None,
        }

        def standards_for(code):
            test = tests.get(code)
            return [row.standard_value for row in test.standards] if test else []

        ce_test = tests.get('CE')
        ce_detail = ce_test.ce_detail if ce_test else None
        ce_custom = self._safe_json_parse(ce_detail.custom_spec if ce_detail else None, default={})
        ce_signal_lines = ce_detail.ce_signal_line_values if ce_detail else []
        result.update({
            'ce': ce_custom if isinstance(ce_custom, dict) else {},
            'ce_standard': standards_for('CE'),
            'ce_voltage_freq': ce_detail.voltage_freq if ce_detail else None,
            'ce_freq_range': ce_detail.freq_range if ce_detail else None,
            'ce_cables': ce_detail.cables if ce_detail else None,
            'ce_class': ce_detail.ce_class if ce_detail else None,
            'ce_signal_lines': ce_signal_lines,
            'ce_custom_spec': ce_custom,
        })

        re_test = tests.get('RE')
        re_detail = re_test.re_detail if re_test else None
        re_custom = self._safe_json_parse(re_detail.custom_spec if re_detail else None, default={})
        result.update({
            're': re_custom if isinstance(re_custom, dict) else {},
            're_standard': standards_for('RE'),
            're_voltage_freq': re_detail.voltage_freq if re_detail else None,
            're_freq_range': re_detail.freq_range if re_detail else None,
            're_class': re_detail.re_class if re_detail else None,
            're_custom_spec': re_custom,
        })

        esd_test = tests.get('ESD')
        esd_detail = esd_test.esd_detail if esd_test else None
        esd_custom = self._safe_json_parse(esd_detail.custom_spec if esd_detail else None, default={})
        result.update({
            'esd': esd_custom if isinstance(esd_custom, dict) else {},
            'esd_standard': standards_for('ESD'),
            'esd_voltage_freq': esd_detail.voltage_freq if esd_detail else None,
            'esd_contact': esd_detail.contact_level if esd_detail else None,
            'esd_air': esd_detail.air_level if esd_detail else None,
            'esd_custom_spec': esd_custom,
        })

        harmonic_test = tests.get('HARMONIC')
        harmonic_detail = harmonic_test.harmonic_detail if harmonic_test else None
        harmonic_custom = self._safe_json_parse(harmonic_detail.custom_spec if harmonic_detail else None, default={})
        if not isinstance(harmonic_custom, dict):
            harmonic_custom = {}
        if harmonic_detail and harmonic_detail.harmonic_class and 'class' not in harmonic_custom:
            harmonic_custom['class'] = harmonic_detail.harmonic_class
        result.update({
            'harmonic': harmonic_custom,
            'harmonic_standard': standards_for('HARMONIC'),
            'harmonic_voltage_freq': harmonic_detail.voltage_freq if harmonic_detail else None,
            'harmonic_class': harmonic_detail.harmonic_class if harmonic_detail else None,
            'harmonic_custom_spec': harmonic_custom,
        })

        flicker_test = tests.get('FLICKER')
        flicker_detail = flicker_test.flicker_detail if flicker_test else None
        flicker_custom = self._safe_json_parse(flicker_detail.custom_spec if flicker_detail else None, default={})
        result.update({
            'voltageFlicker': flicker_custom if isinstance(flicker_custom, dict) else {},
            'flicker_standard': standards_for('FLICKER'),
            'flicker_voltage_freq': flicker_detail.voltage_freq if flicker_detail else None,
            'flicker_custom_specification': flicker_detail.custom_specification if flicker_detail else None,
            'flicker_custom_spec': flicker_custom,
        })

        rs_test = tests.get('RS')
        rs_detail = rs_test.rs_detail if rs_test else None
        rs_custom = self._safe_json_parse(rs_detail.custom_spec if rs_detail else None, default={})
        if not isinstance(rs_custom, dict):
            rs_custom = {}
        result.update({
            'rs_ri': rs_custom,
            'rs_standard': standards_for('RS'),
            'rs_voltage_freq': rs_detail.voltage_freq if rs_detail else None,
            'rs_freq_range': rs_detail.freq_range if rs_detail else None,
            'rs_field_strength1': rs_detail.field_strength1 if rs_detail else None,
            'rs_field_strength2': rs_detail.field_strength2 if rs_detail else None,
            'rs_field_strength3': rs_detail.field_strength3 if rs_detail else None,
            'rs_ri_custom_spec': rs_custom,
        })

        rs_interim_test = tests.get('RS_INTERIM')
        rs_interim_detail = rs_interim_test.rs_interim_detail if rs_interim_test else None
        rs_interim_custom = self._safe_json_parse(rs_interim_detail.custom_spec if rs_interim_detail else None, default={})
        if not isinstance(rs_interim_custom, dict):
            rs_interim_custom = {}
        result.update({
            'rs_ri_interim': rs_interim_custom,
            'rs_interim_standard': standards_for('RS_INTERIM'),
            'rs_interim_voltage_freq': rs_interim_detail.voltage_freq if rs_interim_detail else None,
            'rs_interim_freq_range': rs_interim_detail.freq_range if rs_interim_detail else None,
            'rs_interim_field_strength1': rs_interim_detail.field_strength1 if rs_interim_detail else None,
            'rs_interim_field_strength2': rs_interim_detail.field_strength2 if rs_interim_detail else None,
            'rs_interim_field_strength3': rs_interim_detail.field_strength3 if rs_interim_detail else None,
            'rs_ri_interim_custom_spec': rs_interim_custom,
        })

        eft_test = tests.get('EFT')
        eft_detail = eft_test.eft_detail if eft_test else None
        eft_custom = self._safe_json_parse(eft_detail.custom_spec if eft_detail else None, default={})
        result.update({
            'eft': eft_custom if isinstance(eft_custom, dict) else {},
            'eft_standard': standards_for('EFT'),
            'eft_voltage_freq': eft_detail.voltage_freq if eft_detail else None,
            'eft_cables_power': eft_detail.cables_power if eft_detail else None,
            'eft_cables_signal': eft_detail.cables_signal if eft_detail else None,
            'eft_test_level1': eft_detail.test_level1 if eft_detail else None,
            'eft_test_level2': eft_detail.test_level2 if eft_detail else None,
            'eft_test_level_custom_kv': eft_detail.test_level_custom_kv if eft_detail else None,
            'eft_custom_spec': eft_custom,
        })

        surge_test = tests.get('SURGE')
        surge_detail = surge_test.surge_detail if surge_test else None
        surge_custom = self._safe_json_parse(surge_detail.custom_spec if surge_detail else None, default={})
        result.update({
            'surge': surge_custom if isinstance(surge_custom, dict) else {},
            'surge_standard': standards_for('SURGE'),
            'surge_voltage_freq': surge_detail.voltage_freq if surge_detail else None,
            'surge_cables_power': surge_detail.cables_power if surge_detail else None,
            'surge_cables_signal': surge_detail.cables_signal if surge_detail else None,
            'surge_cm1': surge_detail.cm1 if surge_detail else None,
            'surge_cm2': surge_detail.cm2 if surge_detail else None,
            'surge_dm1': surge_detail.dm1 if surge_detail else None,
            'surge_dm2': surge_detail.dm2 if surge_detail else None,
            'surge_custom_spec': surge_custom,
        })

        crf_test = tests.get('CRF')
        crf_detail = crf_test.crf_detail if crf_test else None
        crf_custom = self._safe_json_parse(crf_detail.custom_spec if crf_detail else None, default={})
        result.update({
            'crf': crf_custom if isinstance(crf_custom, dict) else {},
            'crf_standard': standards_for('CRF'),
            'crf_voltage_freq': crf_detail.voltage_freq if crf_detail else None,
            'crf_freq_range': crf_detail.freq_range if crf_detail else None,
            'crf_cables_power': crf_detail.cables_power if crf_detail else None,
            'crf_cables_signal': crf_detail.cables_signal if crf_detail else None,
            'crf_test_level1': crf_detail.test_level1 if crf_detail else None,
            'crf_test_level2': crf_detail.test_level2 if crf_detail else None,
            'crf_custom_spec': crf_custom,
        })

        pf_test = tests.get('POWER_FREQ')
        pf_detail = pf_test.power_freq_detail if pf_test else None
        pf_custom = self._safe_json_parse(pf_detail.custom_spec if pf_detail else None, default={})
        result.update({
            'pfmf': pf_custom if isinstance(pf_custom, dict) else {},
            'power_freq_standard': standards_for('POWER_FREQ'),
            'power_freq_voltage_freq': pf_detail.voltage_freq if pf_detail else None,
            'power_freq_test_level': pf_detail.test_level if pf_detail else None,
            'power_freq_custom_spec': pf_custom,
        })

        vd_test = tests.get('VOLTAGE_DIPS')
        vd_detail = vd_test.voltage_dips_detail if vd_test else None
        vd_custom = self._safe_json_parse(vd_detail.custom_spec if vd_detail else None, default={})
        result.update({
            'voltageDips': vd_custom if isinstance(vd_custom, dict) else {},
            'voltage_dips_standard': standards_for('VOLTAGE_DIPS'),
            'voltage_dips_min': vd_detail.min_value if vd_detail else None,
            'voltage_dips_max': vd_detail.max_value if vd_detail else None,
            'voltage_dips_voltage_freq': vd_detail.voltage_freq if vd_detail else None,
            'voltage_dips_voltage_dip1': vd_detail.voltage_dip1 if vd_detail else None,
            'voltage_dips_voltage_dip2': vd_detail.voltage_dip2 if vd_detail else None,
            'voltage_dips_voltage_dip3': vd_detail.voltage_dip3 if vd_detail else None,
            'voltage_dips_interruption': vd_detail.interruption if vd_detail else None,
            'voltage_dips_time1': vd_detail.time1 if vd_detail else None,
            'voltage_dips_time2': vd_detail.time2 if vd_detail else None,
            'voltage_dips_time3': vd_detail.time3 if vd_detail else None,
            'voltage_dips_time4': vd_detail.time4 if vd_detail else None,
            'voltage_dips_custom_spec': vd_custom,
        })

        try:
            latest_entry = PlannerEntry.query.filter_by(
                test_request_id=self.id
            ).filter(
                PlannerEntry.report_file_path.isnot(None)
            ).order_by(PlannerEntry.report_uploaded_at.desc()).first()
            if latest_entry:
                result['report_file_path'] = latest_entry.report_file_path
                result['report_comments'] = latest_entry.report_comments
                result['report_uploaded_at'] = self._safe_date_format(latest_entry.report_uploaded_at)
        except Exception:
            pass

        return result

    def to_dict(self):
        """Default serializer for normalized requests."""
        return self.to_legacy_dict()


class EMCRequestServiceType(db.Model):
    __tablename__ = 'iec_emc_request_service_types'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    service_type = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='service_types')


class EMCRequestSerialNumber(db.Model):
    __tablename__ = 'iec_emc_request_serial_numbers'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    serial_number = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='serial_numbers')


class EMCRequestAdditionalModel(db.Model):
    __tablename__ = 'iec_emc_request_additional_models'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    model_number = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='additional_models')


class EMCRequestCategory(db.Model):
    __tablename__ = 'iec_emc_request_categories'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    category_name = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='categories')


class EMCRequestAccessory(db.Model):
    __tablename__ = 'iec_emc_request_accessories'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    accessory_value = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='accessories')


class EMCRequestCable(db.Model):
    __tablename__ = 'iec_emc_request_cables'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    cable_value = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='cables')


class EMCRequestEUTSpec(db.Model):
    __tablename__ = 'iec_emc_request_eut_specs'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    spec_value = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='eut_specs')


class EMCRequestSupplyVF(db.Model):
    __tablename__ = 'iec_emc_request_supply_vf'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    value_text = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='supply_vf_values')


class EMCRequestWireless(db.Model):
    __tablename__ = 'iec_emc_request_wireless'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    value_text = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='wireless_values')


class EMCRequestProductStandard(db.Model):
    __tablename__ = 'iec_emc_request_product_standards'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    standard_value = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='product_standards')


class EMCRequestProductEnvironment(db.Model):
    __tablename__ = 'iec_emc_request_product_environments'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    environment_key = db.Column(db.String(100), nullable=False)
    environment_value = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='product_environments')


class EMCRequestDecisionRule(db.Model):
    __tablename__ = 'iec_emc_request_decision_rules'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    rule_value = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='decision_rules')


class EMCRequestFunctionalMode(db.Model):
    __tablename__ = 'iec_emc_request_functional_modes'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    mode_value = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request = db.relationship('EMCRequest', back_populates='functional_modes')


class EMCRequestTest(TimestampMixin, db.Model):
    """One normalized test row for each selected test on a request."""

    __tablename__ = 'iec_emc_request_tests'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('iec_emc_requests.id'), nullable=False, index=True)
    test_code = db.Column(db.String(50), nullable=False, index=True)
    is_selected = db.Column(db.Boolean, nullable=False, default=False)
    is_developmental = db.Column(db.Boolean, nullable=False, default=False)
    planned_hours = db.Column(db.Float, nullable=True)
    remarks = db.Column(db.Text, nullable=True)
    workflow_status = db.Column(db.String(50), nullable=True)
    assigned_engineer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    assigned_engineer_name = db.Column(db.String(200), nullable=True)
    planned_start_date = db.Column(db.Date, nullable=True)
    planned_end_date = db.Column(db.Date, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('request_id', 'test_code', name='uq_iec_emc_request_test_code'),
    )

    request = db.relationship('EMCRequest', back_populates='tests')
    assigned_engineer = db.relationship(
        'User',
        foreign_keys=[assigned_engineer_id],
        backref=db.backref('normalized_emc_request_tests', lazy=True),
        uselist=False
    )
    standards = db.relationship(
        'EMCRequestTestStandard',
        back_populates='request_test',
        cascade='all, delete-orphan',
        order_by='EMCRequestTestStandard.sort_order',
        lazy=True
    )
    ce_detail = db.relationship('EMCRequestTestCE', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    re_detail = db.relationship('EMCRequestTestRE', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    esd_detail = db.relationship('EMCRequestTestESD', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    harmonic_detail = db.relationship('EMCRequestTestHarmonic', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    flicker_detail = db.relationship('EMCRequestTestFlicker', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    rs_detail = db.relationship('EMCRequestTestRS', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    rs_interim_detail = db.relationship('EMCRequestTestRSInterim', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    eft_detail = db.relationship('EMCRequestTestEFT', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    surge_detail = db.relationship('EMCRequestTestSurge', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    crf_detail = db.relationship('EMCRequestTestCRF', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    power_freq_detail = db.relationship('EMCRequestTestPowerFreq', back_populates='request_test', cascade='all, delete-orphan', uselist=False)
    voltage_dips_detail = db.relationship('EMCRequestTestVoltageDips', back_populates='request_test', cascade='all, delete-orphan', uselist=False)


class EMCRequestTestStandard(db.Model):
    __tablename__ = 'iec_emc_request_test_standards'

    id = db.Column(db.Integer, primary_key=True)
    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), nullable=False, index=True)
    standard_value = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    request_test = db.relationship('EMCRequestTest', back_populates='standards')


class EMCRequestTestCE(db.Model):
    __tablename__ = 'iec_emc_request_test_ce'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    freq_range = db.Column(db.String(100), nullable=True)
    cables = db.Column(db.String(50), nullable=True)
    ce_class = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='ce_detail')
    signal_line_rows = db.relationship(
        'EMCRequestTestCESignalLine',
        back_populates='ce_detail',
        cascade='all, delete-orphan',
        order_by='EMCRequestTestCESignalLine.sort_order',
        lazy=True,
    )

    @staticmethod
    def _normalize_signal_line_values(value):
        if value in (None, '', []):
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                value = [text]
        elif isinstance(value, tuple):
            value = list(value)

        if isinstance(value, dict):
            value = [key for key, is_selected in value.items() if is_selected]
        elif not isinstance(value, list):
            value = [value]

        normalized = []
        for item in value:
            item_text = str(item).strip() if item is not None else ''
            if item_text:
                normalized.append(item_text)
        return normalized

    @property
    def ce_signal_line_values(self):
        return [row.signal_line_value for row in self.signal_line_rows if row.signal_line_value]

    @property
    def ce_signal_lines(self):
        values = self.ce_signal_line_values
        return json.dumps(values, default=str) if values else None

    @ce_signal_lines.setter
    def ce_signal_lines(self, value):
        self.signal_line_rows = [
            EMCRequestTestCESignalLine(sort_order=index, signal_line_value=item_text)
            for index, item_text in enumerate(self._normalize_signal_line_values(value))
        ]


class EMCRequestTestCESignalLine(db.Model):
    __tablename__ = 'iec_emc_request_test_ce_signal_lines'

    id = db.Column(db.Integer, primary_key=True)
    request_test_id = db.Column(
        db.Integer,
        db.ForeignKey('iec_emc_request_test_ce.request_test_id'),
        nullable=False,
        index=True,
    )
    signal_line_value = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    ce_detail = db.relationship('EMCRequestTestCE', back_populates='signal_line_rows')


class EMCRequestTestRE(db.Model):
    __tablename__ = 'iec_emc_request_test_re'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    freq_range = db.Column(db.String(100), nullable=True)
    re_class = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='re_detail')


class EMCRequestTestESD(db.Model):
    __tablename__ = 'iec_emc_request_test_esd'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    contact_level = db.Column(db.String(50), nullable=True)
    air_level = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='esd_detail')


class EMCRequestTestHarmonic(db.Model):
    __tablename__ = 'iec_emc_request_test_harmonic'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    harmonic_class = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='harmonic_detail')


class EMCRequestTestFlicker(db.Model):
    __tablename__ = 'iec_emc_request_test_flicker'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    custom_specification = db.Column(db.Text, nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='flicker_detail')


class EMCRequestTestRS(db.Model):
    __tablename__ = 'iec_emc_request_test_rs'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    freq_range = db.Column(db.String(100), nullable=True)
    field_strength1 = db.Column(db.String(50), nullable=True)
    field_strength2 = db.Column(db.String(50), nullable=True)
    field_strength3 = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='rs_detail')


class EMCRequestTestRSInterim(db.Model):
    __tablename__ = 'iec_emc_request_test_rs_interim'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    freq_range = db.Column(db.String(100), nullable=True)
    field_strength1 = db.Column(db.String(50), nullable=True)
    field_strength2 = db.Column(db.String(50), nullable=True)
    field_strength3 = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='rs_interim_detail')


class EMCRequestTestEFT(db.Model):
    __tablename__ = 'iec_emc_request_test_eft'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    cables_power = db.Column(db.String(50), nullable=True)
    cables_signal = db.Column(db.String(50), nullable=True)
    test_level1 = db.Column(db.String(50), nullable=True)
    test_level2 = db.Column(db.String(50), nullable=True)
    test_level_custom_kv = db.Column(db.Float, nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='eft_detail')


class EMCRequestTestSurge(db.Model):
    __tablename__ = 'iec_emc_request_test_surge'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    cables_power = db.Column(db.String(50), nullable=True)
    cables_signal = db.Column(db.String(50), nullable=True)
    cm1 = db.Column(db.String(50), nullable=True)
    cm2 = db.Column(db.String(50), nullable=True)
    dm1 = db.Column(db.String(50), nullable=True)
    dm2 = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='surge_detail')


class EMCRequestTestCRF(db.Model):
    __tablename__ = 'iec_emc_request_test_crf'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    freq_range = db.Column(db.String(100), nullable=True)
    cables_power = db.Column(db.String(50), nullable=True)
    cables_signal = db.Column(db.String(50), nullable=True)
    test_level1 = db.Column(db.String(50), nullable=True)
    test_level2 = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='crf_detail')


class EMCRequestTestPowerFreq(db.Model):
    __tablename__ = 'iec_emc_request_test_power_freq'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    test_level = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='power_freq_detail')


class EMCRequestTestVoltageDips(db.Model):
    __tablename__ = 'iec_emc_request_test_voltage_dips'

    request_test_id = db.Column(db.Integer, db.ForeignKey('iec_emc_request_tests.id'), primary_key=True)
    min_value = db.Column(db.String(50), nullable=True)
    max_value = db.Column(db.String(50), nullable=True)
    voltage_freq = db.Column(db.String(50), nullable=True)
    voltage_dip1 = db.Column(db.String(50), nullable=True)
    voltage_dip2 = db.Column(db.String(50), nullable=True)
    voltage_dip3 = db.Column(db.String(50), nullable=True)
    interruption = db.Column(db.String(50), nullable=True)
    time1 = db.Column(db.String(50), nullable=True)
    time2 = db.Column(db.String(50), nullable=True)
    time3 = db.Column(db.String(50), nullable=True)
    time4 = db.Column(db.String(50), nullable=True)
    custom_spec = db.Column(db.Text, nullable=True)

    request_test = db.relationship('EMCRequestTest', back_populates='voltage_dips_detail')
