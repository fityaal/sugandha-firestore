from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from firebase_db import cached_docs_list, invalidate_cache
from datetime import datetime, timezone

firms_bp = Blueprint('firms', __name__)


def _all_firms():
    firms = cached_docs_list('firms')
    firms = sorted(firms, key=lambda x: (x.get('firm_name') or '').upper())
    return firms


@firms_bp.route('/')
def index():
    q = request.args.get('q', '')
    firms = _all_firms()
    if q:
        ql = q.lower()
        firms = [
            f for f in firms
            if ql in (f.get('firm_name') or '').lower() or ql in (f.get('firm_gst_no') or '').lower()
        ]
    return render_template('firms/index.html', firms=firms, q=q)


@firms_bp.route('/add', methods=['GET', 'POST'])
def add():
    if request.method == 'POST':
        f = request.form
        name = f['firm_name'].strip().upper()
        db.collection('firms').add({
            'firm_name': name,
            'firm_address': f.get('firm_address', ''),
            'firm_gst_no': f.get('firm_gst_no', ''),
            'firm_mobile': f.get('firm_mobile', ''),
            'created_at': datetime.now(timezone.utc),
        })
        invalidate_cache('firms')
        flash(f"Firm '{name}' added.", 'success')
        return redirect(url_for('firms.index'))
    return render_template('firms/form.html', firm=None, action='Add')


@firms_bp.route('/edit/<id>', methods=['GET', 'POST'])
def edit(id):
    doc_ref = db.collection('firms').document(id)
    if request.method == 'POST':
        f = request.form
        doc_ref.update({
            'firm_name': f['firm_name'].strip().upper(),
            'firm_address': f.get('firm_address', ''),
            'firm_gst_no': f.get('firm_gst_no', ''),
            'firm_mobile': f.get('firm_mobile', ''),
        })
        invalidate_cache('firms')
        flash('Firm updated.', 'success')
        return redirect(url_for('firms.index'))
    doc = doc_ref.get()
    firm = {**doc.to_dict(), 'id': doc.id} if doc.exists else None
    return render_template('firms/form.html', firm=firm, action='Edit')


@firms_bp.route('/delete/<id>', methods=['POST'])
def delete(id):
    db.collection('firms').document(id).delete()
    invalidate_cache('firms')
    flash('Firm deleted.', 'success')
    return redirect(url_for('firms.index'))
