"""
Authentication routes for user login, registration, and password management.
"""

import secrets
import string
import re
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func

from models import db, User
from forms import LoginForm, RegistrationForm, ForgotPasswordForm, ResetPasswordForm, ChangePasswordForm

# Create blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def generate_reset_token(length: int = 32) -> str:
    """Generate a secure reset token."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def derive_username_from_email(email: str) -> str:
    """Derive a display username from email local-part.

    Example:
        saimounika.chandavolu@thermofisher.com -> Saimounika Chandavolu
    """
    email_clean = (email or '').strip()
    local_part = email_clean.split('@', 1)[0] if '@' in email_clean else email_clean
    normalized = re.sub(r'[^a-zA-Z0-9]+', ' ', local_part).strip().lower()
    if not normalized:
        return ''
    username = ' '.join(word.capitalize() for word in normalized.split())
    return username[:50]


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """User login route."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = LoginForm()
    if form.validate_on_submit():
        login_identifier = (form.username.data or '').strip()
        if '@' in login_identifier:
            normalized_email = login_identifier.casefold()
            user = User.query.filter(
                func.lower(func.trim(User.email)) == normalized_email
            ).first()
        else:
            user = User.query.filter(
                func.lower(func.trim(User.username)) == login_identifier.casefold()
            ).first()

        if user is None:
            flash('No account found with that username or email.', 'error')
        elif not user.check_password(form.password.data):
            flash('Incorrect password.', 'error')
        elif not user.is_active:
            flash('Account is deactivated. Please contact administrator.', 'error')
        else:

            # Rotate the session at login to avoid reusing stale cookie state.
            session.clear()
            login_user(user, remember=form.remember_me.data)
            session.permanent = True
            user.update_last_login()
            db.session.commit()

            # Always redirect to index page, no next parameter
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('index'))

    return render_template('auth/login.html', form=form)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration route."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = RegistrationForm()

    # Always derive username from the submitted email on server side.
    if request.method == 'POST':
        submitted_email = (request.form.get('email') or '').strip().lower()
        form.username.data = derive_username_from_email(submitted_email)

    if form.validate_on_submit():
        try:
            email_val = (form.email.data or '').strip().lower()
            username_val = derive_username_from_email(email_val)
            password_val = form.password.data or ''

            if not username_val:
                flash('Unable to derive username from email. Please check your email address.', 'error')
                return render_template('auth/register.html', form=form)

            user = User()
            user.username = username_val
            user.email = email_val
            user.set_password(password_val)

            db.session.add(user)
            db.session.commit()

            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('auth.login'))

        except Exception as e:
            db.session.rollback()
            flash('Registration failed. Please try again.', 'error')

    return render_template('auth/register.html', form=form)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Forgot password route."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()

        if user:
            # Generate reset token
            reset_token = generate_reset_token()
            user.reset_token = reset_token
            user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)

            db.session.commit()

            # Store token in session for development (in production, send via email)
            session['reset_token'] = reset_token

            # In a real application, you would send an email here
            # For now, we'll redirect to reset password page
            flash('Password reset initiated. Please set your new password.', 'info')
            return redirect(url_for('auth.reset_password'))

        else:
            # Don't reveal if email exists or not for security
            flash('If the email exists, a password reset link has been sent.', 'info')

        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html', form=form)


@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    """Reset password route."""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    # Get token from session or form
    token = session.get('reset_token') or request.form.get('reset_token')

    if not token:
        flash('Invalid reset link. Please request a new password reset.', 'error')
        return redirect(url_for('auth.forgot_password'))

    # Ensure token is a string
    token = str(token) if token else None

    user = User.query.filter_by(reset_token=token).first()

    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        flash('Invalid or expired reset token.', 'error')
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        try:
            password = form.password.data
            if password:  # Ensure password is not None
                user.set_password(password)
                user.reset_token = None
                user.reset_token_expires = None

                db.session.commit()

                # Clear the token from session
                session.pop('reset_token', None)

                flash('Password has been reset successfully. Please log in.', 'success')
                return redirect(url_for('auth.login'))
            else:
                flash('Password is required.', 'error')

        except Exception as e:
            db.session.rollback()
            flash('Password reset failed. Please try again.', 'error')

    return render_template('auth/reset_password.html', form=form)


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Change password route for logged-in users."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            try:
                current_user.set_password(form.new_password.data)
                db.session.commit()

                flash('Password changed successfully.', 'success')
                return redirect(url_for('index'))

            except Exception as e:
                db.session.rollback()
                flash('Password change failed. Please try again.', 'error')
        else:
            flash('Current password is incorrect.', 'error')

    return render_template('auth/change_password.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    """User logout route."""
    logout_user()
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/profile')
@login_required
def profile():
    """User profile route."""
    return render_template('auth/profile.html')


# API routes for AJAX requests
@auth_bp.route('/api/check-username', methods=['POST'])
def check_username():
    """Check if username is available."""
    try:
        # Handle both form data and JSON requests
        if request.is_json:
            data = request.get_json()
            username = data.get('username')
        else:
            username = request.form.get('username')

        if not username:
            return jsonify({'error': 'Username is required'}), 400

        # Debug logging (avoid stdout issues on Windows consoles)
        try:
            current_app.logger.info(
                "Checking username availability for: '%s'", username)
        except Exception:
            pass

        # Trim whitespace and check for exact match
        username_clean = username.strip()

        if not username_clean:
            return jsonify({'error': 'Username cannot be empty'}), 400

        # Check if username exists in database
        try:
            user = User.query.filter_by(username=username_clean).first()
            is_available = user is None

            try:
                current_app.logger.info(
                    "Clean username: '%s' | user found: %s | available: %s", username_clean, bool(user), is_available)
            except Exception:
                pass

            return jsonify({
                'available': is_available,
                'username': username_clean,
                'message': 'Username is available' if is_available else 'Username is already taken'
            })

        except Exception as db_error:
            try:
                current_app.logger.exception(
                    "Database error during username check: %s", db_error)
            except Exception:
                pass
            return jsonify({'error': 'Database error occurred'}), 500

    except Exception as e:
        try:
            current_app.logger.exception(
                "General error in check_username: %s", e)
        except Exception:
            pass
        return jsonify({'error': 'Server error occurred'}), 500


@auth_bp.route('/api/check-email', methods=['POST'])
def check_email():
    """Check if email is available."""
    try:
        # Handle both form data and JSON requests
        if request.is_json:
            data = request.get_json()
            email = data.get('email')
        else:
            email = request.form.get('email')

        if not email:
            return jsonify({'error': 'Email is required'}), 400

        # Debug logging (avoid stdout issues on Windows consoles)
        try:
            current_app.logger.info(
                "Checking email availability for: '%s'", email)
        except Exception:
            pass

        # Trim whitespace and convert to lowercase
        email_clean = email.strip().lower()

        if not email_clean:
            return jsonify({'error': 'Email cannot be empty'}), 400

        # Check if email exists in database
        try:
            user = User.query.filter_by(email=email_clean).first()
            is_available = user is None

            try:
                current_app.logger.info(
                    "Clean email: '%s' | user found: %s | available: %s", email_clean, bool(user), is_available)
            except Exception:
                pass

            return jsonify({
                'available': is_available,
                'email': email_clean,
                'message': 'Email is available' if is_available else 'Email is already registered'
            })

        except Exception as db_error:
            try:
                current_app.logger.exception(
                    "Database error during email check: %s", db_error)
            except Exception:
                pass
            return jsonify({'error': 'Database error occurred'}), 500

    except Exception as e:
        try:
            current_app.logger.exception("General error in check_email: %s", e)
        except Exception:
            pass
        return jsonify({'error': 'Server error occurred'}), 500
