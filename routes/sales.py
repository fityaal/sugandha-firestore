from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from firebase_db import docs_to_list, next_sequence, cached_docs_list, invalidate_cache
from datetime import date, datetime, timezone

sales_bp = Blueprint('sales', __name__)


def next_bill_no():
    n = next_sequence('sale', start_at=5526)
    return str(n)


def _all_sales():
    """See note in purchase.py: filtered/sorted in Python at shop scale, cached briefly."""
    return cached_docs_list('sales')


@sales_bp.route('/')
def index():
    items = cached_docs_list('items')
    items = [i for i in items if i.get('item_name')]
    items.sort(key=lambda x: (x.get('item_name') or '').upper())

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    today = date.today().strftime('%Y-%m-%d')
    return render_template('sales/entry.html', items=items, shop=shop, today=today)


@sales_bp.route('/save', methods=['POST'])
def save():
    f = request.form
    bill_no = next_bill_no()

    item_matches = db.collection('items').where('item_name', '==', f['item_name']).limit(1).get()
    item_id = item_matches[0].id if item_matches else None

    qty = int(f.get('sell_qty', 1))
    unit_price = float(f.get('unit_price', 0))
    gst_pct = float(f.get('gst_percent', 12))
    total_price = unit_price * qty
    price_without_gst = round(total_price / (1 + gst_pct / 100), 2)
    gst_amount = round(total_price - price_without_gst, 2)
    imei = f.get('imei', '').strip()

    doc_ref = db.collection('sales').document()
    doc_ref.set({
        'item_id': item_id,
        'item_name': f['item_name'],
        'sell_date': f['sell_date'],
        'bill_no': bill_no,
        'sell_qty': qty,
        'unit_price': unit_price,
        'price_without_gst': price_without_gst,
        'gst_percent': gst_pct,
        'gst_amount': gst_amount,
        'total_price': total_price,
        'customer_name': f.get('customer_name', ''),
        'customer_address': f.get('customer_address', ''),
        'customer_mobile': f.get('customer_mobile', ''),
        'imei': imei,
        'hsn': f.get('hsn', ''),
        'color': f.get('color', ''),
        'gst_no': f.get('gst_no', ''),
        'payment_type': f.get('payment_type', 'CASH'),
        'loan_no': f.get('loan_no', ''),
        'downpayment': float(f.get('downpayment', 0) or 0),
        'emi_amount': float(f.get('emi_amount', 0) or 0),
        'loan_from': f.get('loan_from', ''),
        'sold_by': f.get('sold_by', 'MR.'),
        'reverse_charge': f.get('reverse_charge', 'N'),
        'created_at': datetime.now(timezone.utc),
    })

    if imei:
        matches = db.collection('purchases') \
            .where('imei', '==', imei).where('sold', '==', False).limit(1).get()
        if matches:
            matches[0].reference.update({'sold': True})
            invalidate_cache('purchases')

    invalidate_cache('sales')
    flash(f'Invoice No. {bill_no} saved!', 'success')
    return redirect(url_for('sales.print_bill', id=doc_ref.id))


@sales_bp.route('/report')
def report():
    q = request.args.get('q', '')
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')
    pay_filter = request.args.get('pay', '')
    page = int(request.args.get('page', 1))
    per_page = 30

    rows = _all_sales()

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
    sales = rows[offset:offset + per_page]

    return render_template('sales/report.html', sales=sales, total=total, page=page, pages=pages,
        q=q, from_date=from_date, to_date=to_date, pay_filter=pay_filter, grand_total=grand_total)


@sales_bp.route('/print/<id>')
def print_bill(id):
    doc = db.collection('sales').document(id).get()
    if not doc.exists:
        flash('Sale not found', 'danger')
        return redirect(url_for('sales.report'))
    s = {**doc.to_dict(), 'id': doc.id}

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    return render_template('sales/print.html', s=s, shop=shop)


@sales_bp.route('/delete/<id>', methods=['POST'])
def delete(id):
    doc_ref = db.collection('sales').document(id)
    doc = doc_ref.get()
    if doc.exists:
        imei = (doc.to_dict() or {}).get('imei')
        if imei:
            matches = db.collection('purchases').where('imei', '==', imei).where('sold', '==', True).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': False})
                invalidate_cache('purchases')
        doc_ref.delete()
        invalidate_cache('sales')
    flash('Sale record deleted.', 'success')
    return redirect(url_for('sales.report'))


@sales_bp.route('/api/item-stock')
def item_stock():
    item_name = request.args.get('name', '')
    matches = db.collection('purchases') \
        .where('item_name', '==', item_name).where('sold', '==', False).get()
    rows = []
    for doc in matches:
        d = doc.to_dict()
        rows.append({
            'id': doc.id,
            'imei': d.get('imei', ''),
            'color': d.get('color', ''),
            'unit_price': d.get('unit_price', 0),
            'hsn': d.get('hsn', ''),
            'ram': d.get('ram', ''),
            'memory': d.get('memory', ''),
        })
    rows.sort(key=lambda r: r['id'], reverse=True)

    item_matches = db.collection('items').where('item_name', '==', item_name).limit(1).get()
    master = item_matches[0].to_dict() if item_matches else {}
    master_out = {'hsn': master.get('hsn', ''), 'gst_percent': master.get('gst_percent', 12)} if master else {}

    return jsonify({'items': rows, 'master': master_out})
