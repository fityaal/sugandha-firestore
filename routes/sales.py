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
    master_items = cached_docs_list('items')
    master_items = [i for i in master_items if i.get('item_name')]
    master_items.sort(key=lambda x: (x.get('item_name') or '').upper())

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    today = date.today().strftime('%Y-%m-%d')
    return render_template('sales/entry.html', master_items=master_items, shop=shop, today=today,
                           edit_data=None, invoice_items=[{}])


def _normalize_items(sale_dict):
    """Return a list of items from a sale doc, using the items array or building one from legacy single-item fields."""
    if isinstance(sale_dict.get('items'), list) and len(sale_dict['items']) > 0:
        return sale_dict['items']
    return [{
        'item_id': sale_dict.get('item_id'),
        'item_name': sale_dict.get('item_name', ''),
        'hsn': sale_dict.get('hsn', ''),
        'color': sale_dict.get('color', ''),
        'imei': sale_dict.get('imei', ''),
        'sell_qty': sale_dict.get('sell_qty', 1),
        'unit_price': sale_dict.get('unit_price', 0),
        'gst_percent': sale_dict.get('gst_percent', 12),
        'price_without_gst': sale_dict.get('price_without_gst', 0),
        'gst_amount': sale_dict.get('gst_amount', 0),
        'total_price': sale_dict.get('total_price', 0),
    }]


@sales_bp.route('/edit/<id>')
def edit(id):
    doc = db.collection('sales').document(id).get()
    if not doc.exists:
        flash('Sale not found', 'danger')
        return redirect(url_for('sales.report'))
    s = {**doc.to_dict(), 'id': doc.id}
    s['items'] = _normalize_items(s)

    master_items = cached_docs_list('items')
    master_items = [i for i in master_items if i.get('item_name')]
    master_items.sort(key=lambda x: (x.get('item_name') or '').upper())

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    return render_template('sales/entry.html', master_items=master_items, shop=shop, today=s.get('sell_date', date.today().strftime('%Y-%m-%d')),
                           edit_data=s, invoice_items=s['items'])


def _parse_items_from_form(f):
    """
    Parse multiple items from the submitted form.
    Legacy single-item fields (item_name, hsn, color, imei, sell_qty, unit_price, gst_percent)
    are also accepted for backward compatibility.
    Returns a list of item dicts, plus aggregated totals.
    """
    items = []
    names = f.getlist('items[item_name][]')
    if names:
        hsns = f.getlist('items[hsn][]')
        colors = f.getlist('items[color][]')
        imeis = f.getlist('items[imei][]')
        qtys = f.getlist('items[sell_qty][]')
        prices = f.getlist('items[unit_price][]')
        gsts = f.getlist('items[gst_percent][]')
        n = len(names)
        for i in range(n):
            name = (names[i] or '').strip()
            if not name:
                continue
            qty = int(qtys[i] or 1)
            up = float(prices[i] or 0)
            gst = float(gsts[i] or 12)
            total = up * qty
            ex = round(total / (1 + gst / 100), 2)
            ga = round(total - ex, 2)
            item_matches = db.collection('items').where('item_name', '==', name).limit(1).get()
            item_id = item_matches[0].id if item_matches else None
            items.append({
                'item_id': item_id,
                'item_name': name,
                'hsn': hsns[i] if i < len(hsns) else '',
                'color': colors[i] if i < len(colors) else '',
                'imei': (imeis[i] if i < len(imeis) else '').strip(),
                'sell_qty': qty,
                'unit_price': up,
                'gst_percent': gst,
                'price_without_gst': ex,
                'gst_amount': ga,
                'total_price': total,
            })
    else:
        name = (f.get('item_name') or '').strip()
        if name:
            qty = int(f.get('sell_qty', 1))
            up = float(f.get('unit_price', 0))
            gst = float(f.get('gst_percent', 12))
            total = up * qty
            ex = round(total / (1 + gst / 100), 2)
            ga = round(total - ex, 2)
            item_matches = db.collection('items').where('item_name', '==', name).limit(1).get()
            item_id = item_matches[0].id if item_matches else None
            items.append({
                'item_id': item_id,
                'item_name': name,
                'hsn': f.get('hsn', ''),
                'color': f.get('color', ''),
                'imei': f.get('imei', '').strip(),
                'sell_qty': qty,
                'unit_price': up,
                'gst_percent': gst,
                'price_without_gst': ex,
                'gst_amount': ga,
                'total_price': total,
            })
    grand_total = sum(i['total_price'] for i in items)
    grand_ex = sum(i['price_without_gst'] for i in items)
    grand_gst = sum(i['gst_amount'] for i in items)
    total_qty = sum(i['sell_qty'] for i in items)
    return items, {
        'total_price': grand_total,
        'price_without_gst': grand_ex,
        'gst_amount': grand_gst,
        'sell_qty': total_qty,
        'item_count': len(items),
    }


@sales_bp.route('/save', methods=['POST'])
def save():
    f = request.form
    bill_no = next_bill_no()

    items, agg = _parse_items_from_form(f)
    if not items:
        flash('Please add at least one item.', 'danger')
        return redirect(url_for('sales.index'))

    first = items[0]
    imeis_sold = []

    doc_ref = db.collection('sales').document()
    doc_ref.set({
        'items': items,
        'item_id': first.get('item_id'),
        'item_name': first['item_name'],
        'sell_date': f['sell_date'],
        'bill_no': bill_no,
        'sell_qty': agg['sell_qty'],
        'unit_price': first.get('unit_price', 0),
        'price_without_gst': agg['price_without_gst'],
        'gst_percent': first.get('gst_percent', 12),
        'gst_amount': agg['gst_amount'],
        'total_price': agg['total_price'],
        'item_count': agg['item_count'],
        'customer_name': f.get('customer_name', ''),
        'customer_address': f.get('customer_address', ''),
        'customer_mobile': f.get('customer_mobile', ''),
        'imei': first.get('imei', ''),
        'hsn': first.get('hsn', ''),
        'color': first.get('color', ''),
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

    for item in items:
        imei = item.get('imei', '')
        if imei and imei not in imeis_sold:
            matches = db.collection('purchases') \
                .where('imei', '==', imei).where('sold', '==', False).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': True})
                imeis_sold.append(imei)
    if imeis_sold:
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
    s['items'] = _normalize_items(s)
    # Ensure top-level totals reflect items array
    s['total_price'] = sum(float(i.get('total_price') or 0) for i in s['items'])
    s['price_without_gst'] = sum(float(i.get('price_without_gst') or 0) for i in s['items'])
    s['gst_amount'] = sum(float(i.get('gst_amount') or 0) for i in s['items'])

    shop_doc = db.collection('shop_settings').document('main').get()
    shop = shop_doc.to_dict() if shop_doc.exists else {}

    return render_template('sales/print.html', s=s, shop=shop)


def _collect_imeis(sale_dict):
    """Collect all IMEIs from a sale doc — items array or legacy single imei field."""
    imeis = []
    if isinstance(sale_dict.get('items'), list):
        for it in sale_dict['items']:
            im = (it.get('imei') or '').strip()
            if im and im not in imeis:
                imeis.append(im)
    else:
        im = (sale_dict.get('imei') or '').strip()
        if im:
            imeis.append(im)
    return imeis


@sales_bp.route('/update/<id>', methods=['POST'])
def update(id):
    doc_ref = db.collection('sales').document(id)
    doc = doc_ref.get()
    if not doc.exists:
        flash('Sale not found', 'danger')
        return redirect(url_for('sales.report'))

    existing = doc.to_dict() or {}
    f = request.form

    items, agg = _parse_items_from_form(f)
    if not items:
        flash('Please add at least one item.', 'danger')
        return redirect(url_for('sales.edit', id=id))

    first = items[0]
    old_imeis = _collect_imeis(existing)
    new_imeis = []
    for it in items:
        im = (it.get('imei') or '').strip()
        if im and im not in new_imeis:
            new_imeis.append(im)

    for im in old_imeis:
        if im not in new_imeis:
            matches = db.collection('purchases').where('imei', '==', im).where('sold', '==', True).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': False})

    for im in new_imeis:
        if im not in old_imeis:
            matches = db.collection('purchases').where('imei', '==', im).where('sold', '==', False).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': True})

    if old_imeis != new_imeis:
        invalidate_cache('purchases')

    doc_ref.update({
        'items': items,
        'item_id': first.get('item_id'),
        'item_name': first['item_name'],
        'sell_date': f['sell_date'],
        'sell_qty': agg['sell_qty'],
        'unit_price': first.get('unit_price', 0),
        'price_without_gst': agg['price_without_gst'],
        'gst_percent': first.get('gst_percent', 12),
        'gst_amount': agg['gst_amount'],
        'total_price': agg['total_price'],
        'item_count': agg['item_count'],
        'customer_name': f.get('customer_name', ''),
        'customer_address': f.get('customer_address', ''),
        'customer_mobile': f.get('customer_mobile', ''),
        'imei': first.get('imei', ''),
        'hsn': first.get('hsn', ''),
        'color': first.get('color', ''),
        'gst_no': f.get('gst_no', ''),
        'payment_type': f.get('payment_type', 'CASH'),
        'loan_no': f.get('loan_no', ''),
        'downpayment': float(f.get('downpayment', 0) or 0),
        'emi_amount': float(f.get('emi_amount', 0) or 0),
        'loan_from': f.get('loan_from', ''),
        'sold_by': f.get('sold_by', 'MR.'),
        'reverse_charge': f.get('reverse_charge', 'N'),
        'updated_at': datetime.now(timezone.utc),
    })

    invalidate_cache('sales')
    flash(f'Invoice No. {existing.get("bill_no","")} updated!', 'success')
    return redirect(url_for('sales.print_bill', id=id))


@sales_bp.route('/delete/<id>', methods=['POST'])
def delete(id):
    doc_ref = db.collection('sales').document(id)
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict() or {}
        imeis = _collect_imeis(data)
        for im in imeis:
            matches = db.collection('purchases').where('imei', '==', im).where('sold', '==', True).limit(1).get()
            if matches:
                matches[0].reference.update({'sold': False})
        if imeis:
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
