from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app import db
from datetime import datetime, timezone
import hashlib

auth_bp = Blueprint('auth', __name__)


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def login_required(f):
    """Decorator -- redirects to login if not authenticated."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login to continue.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        doc_ref = db.collection('users').document(username)
        doc = doc_ref.get()
        user = doc.to_dict() if doc.exists else None

        if user and user.get('password_hash') == hash_password(password) and user.get('is_active', True):
            doc_ref.update({'last_login': datetime.now(timezone.utc)})
            session['user_id'] = username
            session['username'] = user.get('username', username)
            session['full_name'] = user.get('full_name', '')
            session['role'] = user.get('role', 'admin')
            return redirect(url_for('dashboard.index'))

        flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('auth.login'))


@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        old_pw = request.form.get('old_password', '')
        new_pw = request.form.get('new_password', '')
        conf_pw = request.form.get('confirm_password', '')

        if new_pw != conf_pw:
            flash('New passwords do not match.', 'danger')
        elif len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        else:
            doc_ref = db.collection('users').document(session['user_id'])
            doc = doc_ref.get()
            user = doc.to_dict() if doc.exists else None
            if user and user.get('password_hash') == hash_password(old_pw):
                doc_ref.update({'password_hash': hash_password(new_pw)})
                flash('Password changed successfully!', 'success')
                return redirect(url_for('dashboard.index'))
            flash('Current password is incorrect.', 'danger')

    return render_template('auth/change_password.html')
