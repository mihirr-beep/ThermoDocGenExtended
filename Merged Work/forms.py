"""
Forms for authentication and user management.
"""

import re

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, EmailField
from wtforms.validators import DataRequired, ValidationError
from models import User


class LoginForm(FlaskForm):
    """Login form."""
    username = StringField('Username', validators=[
        DataRequired(message='Username or email is required')
    ])
    password = PasswordField('Password', validators=[
        DataRequired(message='Password is required')
    ])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In')

    def validate_username(self, username):
        """Custom username/email validation."""
        if not username.data:
            raise ValidationError('Username or email is required')

        identifier = username.data.strip()
        if len(identifier) < 3:
            raise ValidationError(
                'Username or email must be at least 3 characters long')

        if len(identifier) > 100:
            raise ValidationError(
                'Username or email must be no more than 100 characters')

        # Email login support
        if '@' in identifier:
            email_str = identifier.lower()
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email_str):
                raise ValidationError('Please enter a valid email address')
            return

        # Username login support
        if len(identifier) > 50:
            raise ValidationError(
                'Username must be no more than 50 characters')

        if not re.match(r'^[a-zA-Z0-9 ._-]+$', identifier):
            raise ValidationError(
                'Username can only contain letters, numbers, spaces, dots, underscores, and hyphens')

    def validate_password(self, password):
        """Custom password validation."""
        if not password.data:
            raise ValidationError('Password is required')

        if len(password.data) < 1:
            raise ValidationError('Password cannot be empty')


class RegistrationForm(FlaskForm):
    """Registration form."""
    username = StringField('Username', validators=[
        DataRequired(message='Username is required')
    ])
    email = EmailField('Email', validators=[
        DataRequired(message='Email is required')
    ])
    password = PasswordField('Password', validators=[
        DataRequired(message='Password is required')
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(message='Please confirm your password')
    ])
    submit = SubmitField('Register')

    def validate_username(self, username):
        """Custom username validation."""
        if not username.data:
            raise ValidationError('Username is required')

        username_str = username.data.strip()
        if len(username_str) < 3:
            raise ValidationError(
                'Username must be at least 3 characters long')

        if len(username_str) > 50:
            raise ValidationError(
                'Username must be no more than 50 characters')

        # Check for valid characters (alphanumeric, space, dot, underscore, hyphen)
        if not re.match(r'^[a-zA-Z0-9 ._-]+$', username_str):
            raise ValidationError(
                'Username can only contain letters, numbers, spaces, dots, underscores, and hyphens')

        # Check for reserved words
        reserved_words = ['admin', 'root', 'system', 'user', 'test', 'guest']
        if username_str.lower() in reserved_words:
            raise ValidationError(
                'This username is reserved and cannot be used')

        # Check uniqueness
        user = User.query.filter_by(username=username_str).first()
        if user:
            raise ValidationError(
                'Username already exists. Please choose a different one.')

    def validate_email(self, email):
        """Custom email validation."""
        if not email.data:
            raise ValidationError('Email is required')

        email_str = email.data.strip().lower()

        # Basic email format validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email_str):
            raise ValidationError('Please enter a valid email address')

        # Check if email is from thermofisher.com domain
        if not email_str.endswith('@thermofisher.com'):
            raise ValidationError(
                'Only @thermofisher.com email addresses are allowed')

        if len(email_str) > 100:
            raise ValidationError('Email must be no more than 100 characters')

        # Check uniqueness
        user = User.query.filter_by(email=email_str).first()
        if user:
            raise ValidationError(
                'Email already registered. Please use a different email.')

    def validate_password(self, password):
        """Custom password validation."""
        if not password.data:
            raise ValidationError('Password is required')

        if len(password.data) < 8:
            raise ValidationError(
                'Password must be at least 8 characters long')

        if len(password.data) > 128:
            raise ValidationError(
                'Password must be no more than 128 characters')

        # Check for password strength
        if not re.search(r'[A-Z]', password.data):
            raise ValidationError(
                'Password must contain at least one uppercase letter')

        if not re.search(r'[a-z]', password.data):
            raise ValidationError(
                'Password must contain at least one lowercase letter')

        if not re.search(r'\d', password.data):
            raise ValidationError('Password must contain at least one number')

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password.data):
            raise ValidationError(
                'Password must contain at least one special character (!@#$%^&*(),.?":{}|<>)')

    def validate_confirm_password(self, confirm_password):
        """Custom confirm password validation."""
        if not confirm_password.data:
            raise ValidationError('Please confirm your password')

        if hasattr(self, 'password') and self.password.data != confirm_password.data:
            raise ValidationError('Passwords must match')


class ForgotPasswordForm(FlaskForm):
    """Forgot password form."""
    email = EmailField('Email', validators=[
        DataRequired(message='Email is required')
    ])
    submit = SubmitField('Reset Password')

    def validate_email(self, email):
        """Custom email validation."""
        if not email.data:
            raise ValidationError('Email is required')

        email_str = email.data.strip().lower()

        # Basic email format validation
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email_str):
            raise ValidationError('Please enter a valid email address')

        # Check if email is from thermofisher.com domain
        if not email_str.endswith('@thermofisher.com'):
            raise ValidationError(
                'Only @thermofisher.com email addresses are allowed')

        if len(email_str) > 100:
            raise ValidationError('Email must be no more than 100 characters')

        # Check if email exists in database
        user = User.query.filter_by(email=email_str).first()
        if not user:
            raise ValidationError('No account found with this email address')


class ResetPasswordForm(FlaskForm):
    """Reset password form."""
    password = PasswordField('New Password', validators=[
        DataRequired(message='Password is required')
    ])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(message='Please confirm your password')
    ])
    submit = SubmitField('Reset Password')

    def validate_password(self, password):
        """Custom password validation."""
        if not password.data:
            raise ValidationError('Password is required')

        if len(password.data) < 8:
            raise ValidationError(
                'Password must be at least 8 characters long')

        if len(password.data) > 128:
            raise ValidationError(
                'Password must be no more than 128 characters')

        # Check for password strength
        if not re.search(r'[A-Z]', password.data):
            raise ValidationError(
                'Password must contain at least one uppercase letter')

        if not re.search(r'[a-z]', password.data):
            raise ValidationError(
                'Password must contain at least one lowercase letter')

        if not re.search(r'\d', password.data):
            raise ValidationError('Password must contain at least one number')

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password.data):
            raise ValidationError(
                'Password must contain at least one special character (!@#$%^&*(),.?":{}|<>)')

    def validate_confirm_password(self, confirm_password):
        """Custom confirm password validation."""
        if not confirm_password.data:
            raise ValidationError('Please confirm your password')

        if hasattr(self, 'password') and self.password.data != confirm_password.data:
            raise ValidationError('Passwords must match')


class ChangePasswordForm(FlaskForm):
    """Change password form."""
    current_password = PasswordField('Current Password', validators=[
        DataRequired(message='Current password is required')
    ])
    new_password = PasswordField('New Password', validators=[
        DataRequired(message='New password is required')
    ])
    confirm_new_password = PasswordField('Confirm New Password', validators=[
        DataRequired(message='Please confirm your new password')
    ])
    submit = SubmitField('Change Password')

    def validate_current_password(self, current_password):
        """Custom current password validation."""
        if not current_password.data:
            raise ValidationError('Current password is required')

    def validate_new_password(self, new_password):
        """Custom new password validation."""
        if not new_password.data:
            raise ValidationError('New password is required')

        if len(new_password.data) < 8:
            raise ValidationError(
                'Password must be at least 8 characters long')

        if len(new_password.data) > 128:
            raise ValidationError(
                'Password must be no more than 128 characters')

        # Check for password strength
        if not re.search(r'[A-Z]', new_password.data):
            raise ValidationError(
                'Password must contain at least one uppercase letter')

        if not re.search(r'[a-z]', new_password.data):
            raise ValidationError(
                'Password must contain at least one lowercase letter')

        if not re.search(r'\d', new_password.data):
            raise ValidationError('Password must contain at least one number')

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_password.data):
            raise ValidationError(
                'Password must contain at least one special character (!@#$%^&*(),.?":{}|<>)')

        # Check if new password is different from current password
        if hasattr(self, 'current_password') and self.current_password.data == new_password.data:
            raise ValidationError(
                'New password must be different from current password')

    def validate_confirm_new_password(self, confirm_new_password):
        """Custom confirm new password validation."""
        if not confirm_new_password.data:
            raise ValidationError('Please confirm your new password')

        if hasattr(self, 'new_password') and self.new_password.data != confirm_new_password.data:
            raise ValidationError('Passwords must match')
