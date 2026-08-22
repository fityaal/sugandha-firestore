from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app import db
from firebase_db import cached_docs_list, invalidate_cache
from datetime import datetime, timezone

items_bp = Blueprint('items', __name__)


def _all_items():
    items = cached_docs_list('items')
    items = sorted(items, key=lambda x: (x.get('item_name') or '').upper())
    return items


@items_bp.route('/')
def index():
    q = request.args.get('q', '')
    items = _all_items()
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i.get('item_name') or '').lower()]
    return render_template('items/index.html', items=items, q=q)


@items_bp.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        f = request.form
        name = f['item_name'].strip().upper()
        db.collection('items').add({
            'item_name': name,
            'hsn': f.get('hsn', ''),
            'gst_percent': float(f.get('gst_percent', 12)),
            'created_at': datetime.now(timezone.utc),
        })
        invalidate_cache('items')
        flash(f"Item '{name}' added.", 'success')
        return redirect(url_for('items.index'))
    return render_template('items/form.html', item=None, action='Add')


@items_bp.route('/edit/<id>', methods=['GET', 'POST'])
def edit(id):
    doc_ref = db.collection('items').document(id)
    if request.method == 'POST':
        f = request.form
        doc_ref.update({
            'item_name': f['item_name'].strip().upper(),
            'hsn': f.get('hsn', ''),
            'gst_percent': float(f.get('gst_percent', 12)),
        })
        invalidate_cache('items')
        flash('Item updated.', 'success')
        return redirect(url_for('items.index'))
    doc = doc_ref.get()
    item = {**doc.to_dict(), 'id': doc.id} if doc.exists else None
    return render_template('items/form.html', item=item, action='Edit')


@items_bp.route('/delete/<id>', methods=['POST'])
def delete(id):
    db.collection('items').document(id).delete()
    invalidate_cache('items')
    flash('Item deleted.', 'success')
    return redirect(url_for('items.index'))


@items_bp.route('/api/all')
def api_all():
    items = _all_items()
    return jsonify([
        {'item_name': i['item_name'], 'hsn': i.get('hsn', ''), 'gst_percent': i.get('gst_percent', 12)}
        for i in items
    ])
