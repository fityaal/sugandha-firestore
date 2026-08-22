"""
api_auth.py
Simple bearer-token auth for the JSON API, separate from the web portal's
cookie session. A mobile app can't easily ride Flask's session cookie, so
POST /api/v1/auth/login issues an opaque token stored in the `api_tokens`
collection (doc id = token itself, for O(1) lookup), valid for 30 days.
"""
import secrets
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, g

TOKEN_TTL_DAYS = 30


def issue_token(db, username):
    token = secrets.token_hex(32)
    db.collection('api_tokens').document(token).set({
        'username': username,
        'created_at': datetime.now(timezone.utc),
        'expires_at': datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS),
    })
    return token


def revoke_token(db, token):
    db.collection('api_tokens').document(token).delete()


def _extract_token():
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return request.args.get('token', '')  # fallback, not preferred


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        from app import db
        token = _extract_token()
        if not token:
            return jsonify({'success': False, 'error': 'Missing bearer token'}), 401

        doc = db.collection('api_tokens').document(token).get()
        if not doc.exists:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401

        data = doc.to_dict()
        expires_at = data.get('expires_at')
        if expires_at is not None:
            if expires_at.tzinfo is None:
                # Defensive: Firestore normally returns timezone-aware datetimes,
                # but normalize just in case older data predates this fix.
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                revoke_token(db, token)
                return jsonify({'success': False, 'error': 'Token expired, please login again'}), 401

        g.api_username = data.get('username')
        return view(*args, **kwargs)
    return wrapped
