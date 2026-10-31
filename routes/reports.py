from datetime import date, timedelta
from flask import Blueprint, render_template, request, Response, flash, redirect, url_for

from app import db
import gst_export

reports_bp = Blueprint('reports', __name__)


def _shop():
    doc = db.collection('shop_settings').document('main').get()
    return doc.to_dict() if doc.exists else {}


def default_period():
    today = date.today()
    first = today.replace(day=1)
    return first.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')


def make_export(kind, from_date, to_date, fmt):
    """Shared by web + API: returns (bytes, mimetype, filename)."""
    shop = _shop()
    rows, totals = gst_export.build_register(kind, from_date, to_date, shop.get('state_code', '27'))
    if fmt == 'csv':
        return (gst_export.render_csv(kind, rows, totals), 'text/csv; charset=utf-8',
                gst_export.export_filename(kind, from_date, to_date, 'csv'))
    return (gst_export.render_xlsx(kind, rows, totals, shop, from_date, to_date),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            gst_export.export_filename(kind, from_date, to_date, 'xlsx'))


def summaries(from_date, to_date):
    shop = _shop()
    out = {}
    for kind in ('sales', 'purchase'):
        _, totals = gst_export.build_register(kind, from_date, to_date, shop.get('state_code', '27'))
        out[kind] = totals
    return out


@reports_bp.route('/')
def index():
    d_from, d_to = default_period()
    from_date = request.args.get('from_date') or d_from
    to_date = request.args.get('to_date') or d_to
    return render_template('reports/gst.html', from_date=from_date, to_date=to_date,
                           summary=summaries(from_date, to_date))


@reports_bp.route('/download')
def download():
    kind = request.args.get('kind', 'sales')
    if kind not in gst_export.KINDS:
        flash('Unknown report type.', 'danger')
        return redirect(url_for('reports.index'))
    fmt = 'csv' if request.args.get('fmt') == 'csv' else 'xlsx'
    data, mime, filename = make_export(kind, request.args.get('from_date', ''), request.args.get('to_date', ''), fmt)
    return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
