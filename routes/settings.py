from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from datetime import datetime, timezone

settings_bp = Blueprint('settings', __name__)


@settings_bp.route('/', methods=['GET', 'POST'])
def index():
    doc_ref = db.collection('shop_settings').document('main')

    if request.method == 'POST':
        f = request.form
        doc_ref.set({
            'shop_name': f['shop_name'],
            'shop_address': f['shop_address'],
            'shop_gst_no': f['shop_gst_no'],
            'shop_mobile': f['shop_mobile'],
            'shop_email': f.get('shop_email', ''),
            'state_code': f.get('state_code', '27'),
            'updated_at': datetime.now(timezone.utc),
        }, merge=True)
        flash('Shop settings updated successfully!', 'success')
        return redirect(url_for('settings.index'))

    doc = doc_ref.get()
    shop = {**doc.to_dict(), 'id': doc.id} if doc.exists else {}
    return render_template('settings/index.html', shop=shop)
