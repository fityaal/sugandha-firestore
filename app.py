from flask import Flask, session, redirect, url_for, request
from config import Config
from datetime import datetime, timezone
import hashlib

from firebase_db import init_firebase

db = None  # populated by create_app() -> init_firebase()


def amount_in_words(n):
    ones = ['', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
            'TEN', 'ELEVEN', 'TWELVE', 'THIRTEEN', 'FOURTEEN', 'FIFTEEN', 'SIXTEEN',
            'SEVENTEEN', 'EIGHTEEN', 'NINETEEN']
    tens = ['', '', 'TWENTY', 'THIRTY', 'FORTY', 'FIFTY', 'SIXTY', 'SEVENTY', 'EIGHTY', 'NINETY']

    def say(n):
        if n < 20:       return ones[n]
        if n < 100:      return tens[n // 10] + (' ' + ones[n % 10] if n % 10 else '')
        if n < 1000:     return ones[n // 100] + ' HUNDRED' + (' AND ' + say(n % 100) if n % 100 else '')
        if n < 100000:   return say(n // 1000) + ' THOUSAND' + (' ' + say(n % 1000) if n % 1000 else '')
        if n < 10000000: return say(n // 100000) + ' LAKH' + (' ' + say(n % 100000) if n % 100000 else '')
        return say(n // 10000000) + ' CRORE' + (' ' + say(n % 10000000) if n % 10000000 else '')

    n = int(n or 0)
    if n == 0:
        return 'ZERO'
    return 'RUPEES ' + say(n) + ' ONLY'


def format_date(value, fmt='%d - %b - %Y'):
    """Jinja2 filter: safely format a date/datetime/string."""
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime(fmt)
    return str(value)


PUBLIC_ENDPOINTS = {'auth.login', 'auth.logout', 'static'}


def ensure_seed_data():
    """
    Creates the shop_settings/main and an initial admin user if they
    don't exist yet. Firestore has no schema, so there's nothing to
    'CREATE TABLE' -- we just make sure the two singleton/seed docs exist.
    Runs once per process lifetime, safe to call on every startup.
    """
    settings_ref = db.collection('shop_settings').document('main')
    if not settings_ref.get().exists:
        settings_ref.set({
            'shop_name': 'SUGANDHA ENTERPRISES',
            'shop_address': '03, Shubhrambh Society, Shivshakti Nagar, Dattawadi, Nagpur 440023',
            'shop_gst_no': '27APXPT2290K1ZX',
            'shop_mobile': '9766773431',
            'shop_email': '',
            'state_code': '27',
            'updated_at': datetime.now(timezone.utc),
        })

    users_ref = db.collection('users')
    existing = users_ref.where('username', '==', 'admin').limit(1).get()
    if not existing:
        default_pw = hashlib.sha256('sugandha123'.encode()).hexdigest()
        users_ref.document('admin').set({
            'username': 'admin',
            'password_hash': default_pw,
            'full_name': 'Administrator',
            'role': 'admin',
            'is_active': True,
            'last_login': None,
            'created_at': datetime.now(timezone.utc),
        })

    # Seed the two bill-number sequences so they display sensibly on first run.
    counters_ref = db.collection('counters')
    if not counters_ref.document('purchase').get().exists:
        counters_ref.document('purchase').set({'last_number': 1000})
    if not counters_ref.document('sale').get().exists:
        counters_ref.document('sale').set({'last_number': 5526})

    print("[OK] Firestore seed data verified.")


def create_app():
    global db
    app = Flask(__name__)
    app.config.from_object(Config)

    db = init_firebase()
    # Make the client importable as `from app import db` for the route modules,
    # mirroring how `from app import mysql` worked before.
    import sys
    sys.modules[__name__].db = db

    _seed_checked = [False]

    @app.before_request
    def before_each_request():
        if not _seed_checked[0]:
            _seed_checked[0] = True
            try:
                ensure_seed_data()
            except Exception as e:
                print(f"[WARN] Seed check skipped: {e}")

        if request.endpoint and request.endpoint not in PUBLIC_ENDPOINTS:
            if request.endpoint.startswith('api.'):
                return  # JSON API uses its own bearer-token auth (see api_auth.py)
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))

    app.jinja_env.globals['amount_words'] = amount_in_words
    app.jinja_env.filters['format_date'] = format_date
    app.jinja_env.globals['now_year'] = datetime.now().year

    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.items import items_bp
    from routes.firms import firms_bp
    from routes.purchase import purchase_bp
    from routes.sales import sales_bp
    from routes.stock import stock_bp
    from routes.settings import settings_bp
    from routes.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(items_bp, url_prefix='/items')
    app.register_blueprint(firms_bp, url_prefix='/firms')
    app.register_blueprint(purchase_bp, url_prefix='/purchase')
    app.register_blueprint(sales_bp, url_prefix='/sales')
    app.register_blueprint(stock_bp, url_prefix='/stock')
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(api_bp, url_prefix='/api/v1')

    return app


if __name__ == '__main__':
    import sys
    # When this file is run directly (`python app.py`), it loads as module
    # '__main__'. Route files do `from app import db`, and without this line
    # Python can't find a module called 'app' yet, so it silently re-imports
    # app.py a SECOND time under that name -- and that second copy never has
    # create_app() called on it, so its `db` stays None forever. Aliasing
    # __main__ as 'app' here means routes find this already-initialized
    # module instead. (Running via `python run.py` sidesteps this
    # entirely, since app.py is only ever imported once, as 'app' -- see
    # run.py.)
    sys.modules.setdefault('app', sys.modules['__main__'])

    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5002)
