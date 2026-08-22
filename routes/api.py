"""
routes/api.py
JSON API for the Android (and any future iOS/PC) client. Deliberately reuses
the exact same helper functions the web portal's routes already use
(next_bill_no, stock/GST math, IMEI sold-flag flip) so bill numbering and
stock never drift between the web portal and the mobile app.
"""
from flask import Blueprint, request, jsonify, g
from datetime import datetime, date, timezone
import hashlib

from app import db
from firebase_db import cached_docs_list
from api_auth import api_login_required, issue_token, revoke_token
from firebase_db import invalidate_cache as _invalidate_cache

import routes.purchase as purchase_logic
import routes.sales as sales_logic

api_bp = Blueprint('api', __name__)


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


# ---------------------------------------------------------------- auth ----
@api_bp.route('/auth/login', methods=['POST'])
def login():
    body = request.get_json(silent=True) or request.form
    username = (body.get('username') or '').strip()
    password = (body.get('password') or '').strip()

    doc_ref = db.collection('users').document(username)
    doc = doc_ref.get()
    user = doc.to_dict() if doc.exists else None

    if not user or user.get('password_hash') != hash_password(password) or not user.get('is_active', True):
        return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

    doc_ref.update({'last_login': datetime.now(timezone.utc)})
    token = issue_token(db, username)
    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'username': username,
            'full_name': user.get('full_name', ''),
            'role': user.get('role', 'admin'),
        }
    })


@api_bp.route('/auth/logout', methods=['POST'])
@api_login_required
def logout():
    from api_auth import _extract_token
    revoke_token(db, _extract_token())
    return jsonify({'success': True})


@api_bp.route('/auth/change-password', methods=['POST'])
@api_login_required
def change_password():
    body = request.get_json(silent=True) or request.form
    old_pw = body.get('old_password', '')
    new_pw = body.get('new_password', '')
    if len(new_pw) < 6:
        return jsonify({'success': False, 'error': 'New password must be at least 6 characters'}), 400

    doc_ref = db.collection('users').document(g.api_username)
    doc = doc_ref.get()
    user = doc.to_dict() if doc.exists else None
    if not user or user.get('password_hash') != hash_password(old_pw):
        return jsonify({'success': False, 'error': 'Current password is incorrect'}), 400

    doc_ref.update({'password_hash': hash_password(new_pw)})
    return jsonify({'success': True})


# ------------------------------------------------------------- items ------
@api_bp.route('/items', methods=['GET'])
@api_login_required
def items_list():
    q = request.args.get('q', '')
    items = cached_docs_list('items')
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i.get('item_name') or '').lower()]
    items.sort(key=lambda x: (x.get('item_name') or '').upper())
    return jsonify({'success': True, 'items': items})


@api_bp.route('/items', methods=['POST'])
@api_login_required
def items_create():
    f = request.get_json(silent=True) or request.form
    name = (f.get('item_name') or '').strip().upper()
    if not name:
        return jsonify({'success': False, 'error': 'item_name is required'}), 400
    ref = db.collection('items').document()
    ref.set({
        'item_name': name,
        'hsn': f.get('hsn', ''),
        'gst_percent': float(f.get('gst_percent', 12)),
        'created_at': datetime.now(timezone.utc),
    })
    _invalidate_cache('items')
    return jsonify({'success': True, 'id': ref.id})


@api_bp.route('/items/<id>', methods=['PUT'])
@api_login_required
def items_update(id):
    f = request.get_json(silent=True) or request.form
    db.collection('items').document(id).update({
        'item_name': (f.get('item_name') or '').strip().upper(),
        'hsn': f.get('hsn', ''),
        'gst_percent': float(f.get('gst_percent', 12)),
    })
    _invalidate_cache('items')
    return jsonify({'success': True})


@api_bp.route('/items/<id>', methods=['DELETE'])
@api_login_required
def items_delete(id):
    db.collection('items').document(id).delete()
    _invalidate_cache('items')
    return jsonify({'success': True})


# ------------------------------------------------------------- firms ------
@api_bp.route('/firms', methods=['GET'])
@api_login_required
def firms_list():
    q = request.args.get('q', '')
    firms = cached_docs_list('firms')
    if q:
        ql = q.lower()
        firms = [f for f in firms if ql in (f.get('firm_name') or '').lower()
                 or ql in (f.get('firm_gst_no') or '').lower()]
    firms.sort(key=lambda x: (x.get('firm_name') or '').upper())
    return jsonify({'success': True, 'firms': firms})


@api_bp.route('/firms', methods=['POST'])
@api_login_required
def firms_create():
    f = request.get_json(silent=True) or request.form
    name = (f.get('firm_name') or '').strip().upper()
    if not name:
        return jsonify({'success': False, 'error': 'firm_name is required'}), 400
    ref = db.collection('firms').document()
    ref.set({
        'firm_name': name,
        'firm_address': f.get('firm_address', ''),
        'firm_gst_no': f.get('firm_gst_no', ''),
        'firm_mobile': f.get('firm_mobile', ''),
        'created_at': datetime.now(timezone.utc),
    })
    _invalidate_cache('firms')
    return jsonify({'success': True, 'id': ref.id})


@api_bp.route('/firms/<id>', methods=['PUT'])
@api_login_required
def firms_update(id):
    f = request.get_json(silent=True) or request.form
    db.collection('firms').document(id).update({
        'firm_name': (f.get('firm_name') or '').strip().upper(),
        'firm_address': f.get('firm_address', ''),
        'firm_gst_no': f.get('firm_gst_no', ''),
        'firm_mobile': f.get('firm_mobile', ''),
    })
    _invalidate_cache('firms')
    return jsonify({'success': True})


@api_bp.route('/firms/<id>', methods=['DELETE'])
@api_login_required
def firms_delete(id):
    db.collection('firms').document(id).delete()
    _invalidate_cache('firms')
    return jsonify({'success': True})


# ----------------------------------------------------------- purchase -----
@api_bp.route('/purchase', methods=['POST'])
@api_login_required
def purchase_save():
    f = request.get_json(silent=True) or request.form
    bill_no = (f.get('bill_no') or '').strip()
    if not bill_no:
        return jsonify({'success': False, 'error': 'bill_no is required'}), 400

    item_matches = db.collection('items').where('item_name', '==', f['item_name']).limit(1).get()
    item_id = item_matches[0].id if item_matches else None
    firm_id = f.get('firm_id') or None

    unit_price = float(f.get('unit_price', 0))
    qty = int(f.get('qty', 1))
    gst_pct = float(f.get('gst_percent', 12))
    total_price = unit_price * qty
    price_without_gst = round(total_price / (1 + gst_pct / 100), 2)
    gst_amount = round(total_price - price_without_gst, 2)

    doc_ref = db.collection('purchases').document()
    doc_ref.set({
        'item_id': item_id,
        'item_name': f['item_name'],
        'purchase_date': f['purchase_date'],
        'bill_no': bill_no,
        'qty': qty,
        'unit_price': unit_price,
        'price_without_gst': price_without_gst,
        'gst_percent': gst_pct,
        'gst_amount': gst_amount,
        'total_price': total_price,
        'firm_id': firm_id,
        'firm_name': f.get('firm_name', ''),
        'firm_address': f.get('firm_address', ''),
        'firm_gst_no': f.get('firm_gst_no', ''),
        'firm_mobile': f.get('firm_mobile', ''),
        'hsn': f.get('hsn', ''),
        'imei': f.get('imei', ''),
        'color': f.get('color', ''),
        'ram': f.get('ram', ''),
        'memory': f.get('memory', ''),
        'sold': False,
        'created_at': datetime.now(timezone.utc),
    })
    _invalidate_cache('purchases')
    return jsonify({'success': True, 'id': doc_ref.id, 'bill_no': bill_no})


@api_bp.route('/purchase/report', methods=['GET'])
@api_login_required
def purchase_report():
    q = request.args.get('q', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    firm_filter = request.args.get('firm', '')
    status_filter = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 25))

    rows = purchase_logic._all_purchases()

    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r.get('item_name') or '').lower()
                or ql in (r.get('bill_no') or '').lower()
                or ql in (r.get('firm_name') or '').lower()
                or ql in (r.get('imei') or '').lower()]
    if from_date:
        rows = [r for r in rows if (r.get('purchase_date') or '') >= from_date]
    if to_date:
        rows = [r for r in rows if (r.get('purchase_date') or '') <= to_date]
    if firm_filter:
        ffl = firm_filter.lower()
        rows = [r for r in rows if ffl in (r.get('firm_name') or '').lower()]
    if status_filter != '':
        want_sold = bool(int(status_filter))
        rows = [r for r in rows if bool(r.get('sold')) == want_sold]

    rows.sort(key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    total = len(rows)
    grand_total = sum(float(r.get('total_price') or 0) for r in rows)
    pages = max((total + per_page - 1) // per_page, 1)
    offset = (page - 1) * per_page
    page_rows = rows[offset:offset + per_page]

    return jsonify({'success': True, 'purchases': _jsonable(page_rows), 'total': total,
                     'page': page, 'pages': pages, 'grand_total': grand_total})


@api_bp.route('/purchase/<id>', methods=['GET'])
@api_login_required
def purchase_get(id):
    doc = db.collection('purchases').document(id).get()
    if not doc.exists:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return jsonify({'success': True, 'purchase': _jsonable({**doc.to_dict(), 'id': doc.id})})


@api_bp.route('/purchase/<id>', methods=['DELETE'])
@api_login_required
def purchase_delete(id):
    db.collection('purchases').document(id).delete()
    _invalidate_cache('purchases')
    return jsonify({'success': True})


@api_bp.route('/purchase/firm-details', methods=['GET'])
@api_login_required
def purchase_firm_details():
    firm_name = request.args.get('name', '')
    matches = db.collection('firms').where('firm_name', '==', firm_name).limit(1).get()
    if matches:
        doc = matches[0]
        firm = doc.to_dict()
        return jsonify({'success': True, 'firm': {
            'id': doc.id, 'address': firm.get('firm_address', ''),
            'gst': firm.get('firm_gst_no', ''), 'mobile': firm.get('firm_mobile', ''),
        }})
    return jsonify({'success': False})


# ------------------------------------------------------------- sales ------
@api_bp.route('/sales/item-stock', methods=['GET'])
@api_login_required
def sales_item_stock():
    item_name = request.args.get('name', '')
    matches = db.collection('purchases') \
        .where('item_name', '==', item_name).where('sold', '==', False).get()
    rows = []
    for doc in matches:
        d = doc.to_dict()
        rows.append({'id': doc.id, 'imei': d.get('imei', ''), 'color': d.get('color', ''),
                     'unit_price': d.get('unit_price', 0), 'hsn': d.get('hsn', ''),
                     'ram': d.get('ram', ''), 'memory': d.get('memory', '')})
    rows.sort(key=lambda r: r['id'], reverse=True)

    item_matches = db.collection('items').where('item_name', '==', item_name).limit(1).get()
    master = item_matches[0].to_dict() if item_matches else {}
    master_out = {'hsn': master.get('hsn', ''), 'gst_percent': master.get('gst_percent', 12)} if master else {}
    return jsonify({'success': True, 'items': rows, 'master': master_out})


@api_bp.route('/sales', methods=['POST'])
@api_login_required
def sales_save():
    f = request.get_json(silent=True) or request.form
    bill_no = sales_logic.next_bill_no()

    item_matches = db.collection('items').where('item_name', '==', f['item_name']).limit(1).get()
    item_id = item_matches[0].id if item_matches else None

    qty = int(f.get('sell_qty', 1))
    unit_price = float(f.get('unit_price', 0))
    gst_pct = float(f.get('gst_percent', 12))
    total_price = unit_price * qty
    price_without_gst = round(total_price / (1 + gst_pct / 100), 2)
    gst_amount = round(total_price - price_without_gst, 2)
    imei = (f.get('imei') or '').strip()

    doc_ref = db.collection('sales').document()
    doc_ref.set({
        'item_id': item_id, 'item_name': f['item_name'], 'sell_date': f['sell_date'],
        'bill_no': bill_no, 'sell_qty': qty, 'unit_price': unit_price,
        'price_without_gst': price_without_gst, 'gst_percent': gst_pct, 'gst_amount': gst_amount,
        'total_price': total_price, 'customer_name': f.get('customer_name', ''),
        'customer_address': f.get('customer_address', ''), 'customer_mobile': f.get('customer_mobile', ''),
        'imei': imei, 'hsn': f.get('hsn', ''), 'color': f.get('color', ''), 'gst_no': f.get('gst_no', ''),
        'payment_type': f.get('payment_type', 'CASH'), 'loan_no': f.get('loan_no', ''),
        'downpayment': float(f.get('downpayment', 0) or 0), 'emi_amount': float(f.get('emi_amount', 0) or 0),
        'loan_from': f.get('loan_from', ''), 'sold_by': f.get('sold_by', 'MR.'),
        'reverse_charge': f.get('reverse_charge', 'N'), 'created_at': datetime.now(timezone.utc),
    })

    if imei:
        matches = db.collection('purchases') \
            .where('imei', '==', imei).where('sold', '==', False).limit(1).get()
        if matches:
            matches[0].reference.update({'sold': True})
            _invalidate_cache('purchases')

    _invalidate_cache('sales')
    return jsonify({'success': True, 'id': doc_ref.id, 'bill_no': bill_no})


@api_bp.route('/sales/report', methods=['GET'])
@api_login_required
def sales_report():
    q = request.args.get('q', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    pay_filter = request.args.get('pay', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))

    rows = sales_logic._all_sales()

    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r.get('item_name') or '').lower()
                or ql in (r.get('bill_no') or '').lower()
                or ql in (r.get('customer_name') or '').lower()
                or ql in (r.get('customer_mobile') or '').lower()
                or ql in (r.get('imei') or '').lower()]
    if from_date:
        rows = [r for r in rows if (r.get('sell_date') or '') >= from_date]
    if to_date:
        rows = [r for r in rows if (r.get('sell_date') or '') <= to_date]
    if pay_filter:
        rows = [r for r in rows if (r.get('payment_type') or '') == pay_filter]

    rows.sort(key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    total = len(rows)
    grand_total = sum(float(r.get('total_price') or 0) for r in rows)
    pages = max((total + per_page - 1) // per_page, 1)
    offset = (page - 1) * per_page
    page_rows = rows[offset:offset + per_page]

    return jsonify({'success': True, 'sales': _jsonable(page_rows), 'total': total,
                     'page': page, 'pages': pages, 'grand_total': grand_total})


@api_bp.route('/sales/<id>', methods=['GET'])
@api_login_required
def sales_get(id):
    doc = db.collection('sales').document(id).get()
    if not doc.exists:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    return jsonify({'success': True, 'sale': _jsonable({**doc.to_dict(), 'id': doc.id})})


@api_bp.route('/sales/<id>', methods=['DELETE'])
@api_login_required
def sales_delete(id):
    doc_ref = db.collection('sales').document(id)
    doc = doc_ref.get()
    if doc.exists:
        imei = (doc.to_dict() or {}).get('imei')
        if imei:
            matches = db.collection('purchases').where('imei', '==', imei).where('sold', '==', True).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': False})
                _invalidate_cache('purchases')
        doc_ref.delete()
        _invalidate_cache('sales')
    return jsonify({'success': True})


# ------------------------------------------------------------- stock ------
@api_bp.route('/stock', methods=['GET'])
@api_login_required
def stock_overview():
    q = request.args.get('q', '')
    show = request.args.get('show', 'all')

    items = cached_docs_list('items')
    purchases = cached_docs_list('purchases')

    agg = {}
    for p in purchases:
        iid = p.get('item_id')
        if iid is None:
            continue
        a = agg.setdefault(iid, {'total_purchased': 0, 'total_sold': 0, 'current_stock': 0})
        a['total_purchased'] += int(p.get('qty') or 0)
        if p.get('sold'):
            a['total_sold'] += 1
        else:
            a['current_stock'] += 1

    rows = []
    for item in items:
        a = agg.get(item['id'], {'total_purchased': 0, 'total_sold': 0, 'current_stock': 0})
        rows.append({'id': item['id'], 'item_name': item.get('item_name', ''), **a})

    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r.get('item_name') or '').lower()]
    if show == 'instock':
        rows = [r for r in rows if r['current_stock'] > 0]
    elif show == 'zero':
        rows = [r for r in rows if r['current_stock'] == 0]
    elif show == 'low':
        rows = [r for r in rows if 1 <= r['current_stock'] <= 3]

    rows.sort(key=lambda r: (-r['current_stock'], (r.get('item_name') or '').upper()))
    summary = {'cnt': len(purchases), 'total_stock': sum(1 for p in purchases if not p.get('sold'))}
    return jsonify({'success': True, 'items': rows, 'summary': summary})


# ---------------------------------------------------------- dashboard -----
@api_bp.route('/dashboard', methods=['GET'])
@api_login_required
def dashboard():
    import routes.dashboard as dash_logic
    from flask import render_template_string  # unused, placeholder import safety

    purchases = cached_docs_list('purchases')
    sales = cached_docs_list('sales')
    items = cached_docs_list('items')

    today = date.today()
    monthly_revenue, monthly_count = 0.0, 0
    today_revenue, today_count = 0.0, 0
    payment_totals = {}
    six_months_ago = today.replace(day=1)
    for _ in range(5):
        prev_month = six_months_ago.month - 1 or 12
        prev_year = six_months_ago.year - 1 if six_months_ago.month == 1 else six_months_ago.year
        six_months_ago = six_months_ago.replace(year=prev_year, month=prev_month, day=1)

    chart_buckets = {}
    for s in sales:
        d = dash_logic._parse_date(s.get('sell_date'))
        price = float(s.get('total_price') or 0)
        if d is None:
            continue
        if d.year == today.year and d.month == today.month:
            monthly_revenue += price
            monthly_count += 1
            pt = s.get('payment_type', 'CASH')
            payment_totals.setdefault(pt, {'cnt': 0, 'total': 0.0})
            payment_totals[pt]['cnt'] += 1
            payment_totals[pt]['total'] += price
        if d == today:
            today_revenue += price
            today_count += 1
        if d >= six_months_ago:
            key = f"{d.year}-{d.month:02d}"
            chart_buckets.setdefault(key, {'revenue': 0.0, 'cnt': 0})
            chart_buckets[key]['revenue'] += price
            chart_buckets[key]['cnt'] += 1

    stock_by_item = {}
    for p in purchases:
        if not p.get('sold') and p.get('item_id'):
            stock_by_item[p['item_id']] = stock_by_item.get(p['item_id'], 0) + 1
    items_by_id = {i['id']: i for i in items}
    low_stock = [{'item_name': items_by_id[iid].get('item_name', ''), 'stock': cnt}
                 for iid, cnt in stock_by_item.items() if 1 <= cnt <= 3 and iid in items_by_id]
    low_stock.sort(key=lambda r: r['stock'])
    low_stock = low_stock[:12]

    return jsonify({
        'success': True,
        'total_purchases': len(purchases),
        'total_sales': len(sales),
        'monthly_revenue': monthly_revenue, 'monthly_count': monthly_count,
        'today_revenue': today_revenue, 'today_count': today_count,
        'stock_count': sum(1 for p in purchases if not p.get('sold')),
        'low_stock': low_stock,
        'chart_data': [{'month': k, **v} for k, v in sorted(chart_buckets.items())],
        'payment_split': [{'payment_type': k, **v} for k, v in payment_totals.items()],
        'recent_sales': _jsonable(sorted(sales, key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]),
        'recent_purchases': _jsonable(sorted(purchases, key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:8]),
    })


# ------------------------------------------------------------ settings ----
@api_bp.route('/settings', methods=['GET'])
@api_login_required
def settings_get():
    doc = db.collection('shop_settings').document('main').get()
    shop = {**doc.to_dict(), 'id': doc.id} if doc.exists else {}
    return jsonify({'success': True, 'settings': shop})


@api_bp.route('/settings', methods=['PUT'])
@api_login_required
def settings_update():
    f = request.get_json(silent=True) or request.form
    db.collection('shop_settings').document('main').set({
        'shop_name': f.get('shop_name', ''), 'shop_address': f.get('shop_address', ''),
        'shop_gst_no': f.get('shop_gst_no', ''), 'shop_mobile': f.get('shop_mobile', ''),
        'shop_email': f.get('shop_email', ''), 'state_code': f.get('state_code', '27'),
        'updated_at': datetime.now(timezone.utc),
    }, merge=True)
    return jsonify({'success': True})


# --------------------------------------------------------------- utils ----
def _jsonable(obj):
    """Recursively convert datetimes to ISO strings so jsonify doesn't choke."""
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj
