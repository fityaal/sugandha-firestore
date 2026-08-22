from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from firebase_db import docs_to_list, cached_docs_list, invalidate_cache
from datetime import date, datetime, timezone

purchase_bp = Blueprint('purchase', __name__)


def _all_purchases():
    """
    Firestore has no server-side LIKE/GROUP BY, and this app's report filters
    are highly combinatorial (free-text + date range + firm + status), so we
    pull the collection once and filter/sort/paginate in Python -- simplest
    and most robust for a shop-scale dataset. Cached for a short TTL (see
    firebase_db.py) since this collection can be large and this function is
    called on every dashboard/report/stock page load.
    """
    return cached_docs_list('purchases')


@purchase_bp.route('/')
def index():
    items = docs_to_list(db.collection('items').stream())
    items.sort(key=lambda x: (x.get('item_name') or '').upper())

    firms = docs_to_list(db.collection('firms').stream())
    firms.sort(key=lambda x: (x.get('firm_name') or '').upper())

    today = date.today().strftime('%Y-%m-%d')
    return render_template('purchase/entry.html', items=items, firms=firms, today=today, edit_data=None)


@purchase_bp.route('/edit/<id>')
def edit(id):
    doc = db.collection('purchases').document(id).get()
    if not doc.exists:
        flash('Purchase not found', 'danger')
        return redirect(url_for('purchase.report'))
    p = {**doc.to_dict(), 'id': doc.id}

    items = docs_to_list(db.collection('items').stream())
    items.sort(key=lambda x: (x.get('item_name') or '').upper())

    firms = docs_to_list(db.collection('firms').stream())
    firms.sort(key=lambda x: (x.get('firm_name') or '').upper())

    return render_template('purchase/entry.html', items=items, firms=firms, today=p.get('purchase_date', date.today().strftime('%Y-%m-%d')), edit_data=p)


@purchase_bp.route('/save', methods=['POST'])
def save():
    f = request.form
    bill_no = (f.get('bill_no') or '').strip()
    if not bill_no:
        flash('Bill No. is required.', 'danger')
        return redirect(url_for('purchase.index'))

    # Resolve item_id
    item_matches = db.collection('items').where('item_name', '==', f['item_name']).limit(1).get()
    item_id = item_matches[0].id if item_matches else None

    # Resolve firm_id
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

    invalidate_cache('purchases')
    flash(f'Purchase bill {bill_no} saved successfully!', 'success')
    return redirect(url_for('purchase.print_bill', id=doc_ref.id))


@purchase_bp.route('/report')
def report():
    q = request.args.get('q', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    firm_filter = request.args.get('firm', '')
    status_filter = request.args.get('status', '')
    page = int(request.args.get('page', 1))
    per_page = 25

    rows = _all_purchases()

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
    purchases = rows[offset:offset + per_page]

    all_firms = sorted({r.get('firm_name') for r in _all_purchases() if r.get('firm_name')})

    return render_template('purchase/report.html',
        purchases=purchases, total=total, page=page, pages=pages,
        q=q, from_date=from_date, to_date=to_date,
        firm_filter=firm_filter, status_filter=status_filter,
        firms=all_firms, grand_total=grand_total)


@purchase_bp.route('/print/<id>')
def print_bill(id):
    doc = db.collection('purchases').document(id).get()
    if not doc.exists:
        flash('Purchase not found', 'danger')
        return redirect(url_for('purchase.report'))
    p = {**doc.to_dict(), 'id': doc.id}

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    return render_template('purchase/print.html', p=p, shop=shop)


@purchase_bp.route('/update/<id>', methods=['POST'])
def update(id):
    doc_ref = db.collection('purchases').document(id)
    doc = doc_ref.get()
    if not doc.exists:
        flash('Purchase not found', 'danger')
        return redirect(url_for('purchase.report'))

    f = request.form
    bill_no = (f.get('bill_no') or '').strip()
    if not bill_no:
        flash('Bill No. is required.', 'danger')
        return redirect(url_for('purchase.edit', id=id))

    item_matches = db.collection('items').where('item_name', '==', f['item_name']).limit(1).get()
    item_id = item_matches[0].id if item_matches else None

    firm_id = f.get('firm_id') or None

    unit_price = float(f.get('unit_price', 0))
    qty = int(f.get('qty', 1))
    gst_pct = float(f.get('gst_percent', 12))

    total_price = unit_price * qty
    price_without_gst = round(total_price / (1 + gst_pct / 100), 2)
    gst_amount = round(total_price - price_without_gst, 2)

    existing = doc.to_dict() or {}
    doc_ref.update({
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
        'updated_at': datetime.now(timezone.utc),
    })

    invalidate_cache('purchases')
    flash(f'Purchase bill {bill_no} updated successfully!', 'success')
    return redirect(url_for('purchase.print_bill', id=id))


@purchase_bp.route('/delete/<id>', methods=['POST'])
def delete(id):
    db.collection('purchases').document(id).delete()
    invalidate_cache('purchases')
    flash('Purchase record deleted.', 'success')
    return redirect(url_for('purchase.report'))


@purchase_bp.route('/api/firm-details')
def firm_details():
    firm_name = request.args.get('name', '')
    matches = db.collection('firms').where('firm_name', '==', firm_name).limit(1).get()
    if matches:
        doc = matches[0]
        firm = doc.to_dict()
        return jsonify({'success': True, 'firm': {
            'id': doc.id,
            'address': firm.get('firm_address', ''),
            'gst': firm.get('firm_gst_no', ''),
            'mobile': firm.get('firm_mobile', ''),
        }})
    return jsonify({'success': False})
