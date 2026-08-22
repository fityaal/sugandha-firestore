from flask import Blueprint, render_template, request
from app import db
from firebase_db import cached_docs_list

stock_bp = Blueprint('stock', __name__)


@stock_bp.route('/')
def index():
    q = request.args.get('q', '')
    show = request.args.get('show', 'all')

    items = cached_docs_list('items')
    purchases = cached_docs_list('purchases')

    # Aggregate purchase totals per item_id in Python (Firestore has no GROUP BY).
    agg = {}  # item_id -> {purchased, sold, stock}
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
        rows.append({
            'id': item['id'],
            'item_name': item.get('item_name', ''),
            'total_purchased': a['total_purchased'],
            'total_sold': a['total_sold'],
            'current_stock': a['current_stock'],
        })

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

    summary = {
        'cnt': len(purchases),
        'total_stock': sum(1 for p in purchases if not p.get('sold')),
    }

    return render_template('stock/index.html', items=rows, q=q, show=show, summary=summary)
