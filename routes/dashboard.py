from flask import Blueprint, render_template
from app import db
from firebase_db import cached_docs_list
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
import calendar

dashboard_bp = Blueprint('dashboard', __name__)


def _parse_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


@dashboard_bp.route('/')
def index():
    purchases = cached_docs_list('purchases')
    sales = cached_docs_list('sales')
    items = cached_docs_list('items')

    total_purchases = len(purchases)
    total_sales = len(sales)

    today = date.today()

    monthly_revenue = 0.0
    monthly_count = 0
    today_revenue = 0.0
    today_count = 0
    payment_totals = defaultdict(lambda: {'cnt': 0, 'total': 0.0})
    six_months_ago = today.replace(day=1)
    for _ in range(5):
        # step back 5 more months to get a 6-month window inclusive of this month
        prev_month = six_months_ago.month - 1 or 12
        prev_year = six_months_ago.year - 1 if six_months_ago.month == 1 else six_months_ago.year
        six_months_ago = six_months_ago.replace(year=prev_year, month=prev_month, day=1)

    chart_buckets = defaultdict(lambda: {'revenue': 0.0, 'cnt': 0})

    for s in sales:
        d = _parse_date(s.get('sell_date'))
        price = float(s.get('total_price') or 0)
        if d is None:
            continue
        if d.year == today.year and d.month == today.month:
            monthly_revenue += price
            monthly_count += 1
            pt = s.get('payment_type', 'CASH')
            payment_totals[pt]['cnt'] += 1
            payment_totals[pt]['total'] += price
        if d == today:
            today_revenue += price
            today_count += 1
        if d >= six_months_ago:
            key = (d.year, d.month)
            chart_buckets[key]['revenue'] += price
            chart_buckets[key]['cnt'] += 1

    stock_count = sum(1 for p in purchases if not p.get('sold'))

    recent_sales = sorted(sales, key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]
    recent_purchases = sorted(purchases, key=lambda r: r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:8]

    # Low stock: items with 1-3 unsold units, matched via item_id
    stock_by_item = defaultdict(int)
    for p in purchases:
        if not p.get('sold') and p.get('item_id'):
            stock_by_item[p['item_id']] += 1
    items_by_id = {i['id']: i for i in items}
    low_stock = [
        {'item_name': items_by_id[iid].get('item_name', ''), 'stock': cnt}
        for iid, cnt in stock_by_item.items()
        if 1 <= cnt <= 3 and iid in items_by_id
    ]
    low_stock.sort(key=lambda r: r['stock'])
    low_stock = low_stock[:12]

    chart_data = []
    for (yr, mo), vals in sorted(chart_buckets.items()):
        chart_data.append({
            'yr': yr, 'mo': mo,
            'month': f"{calendar.month_abbr[mo]} {yr}",
            'revenue': vals['revenue'],
            'cnt': vals['cnt'],
        })

    payment_split = [
        {'payment_type': k, 'cnt': v['cnt'], 'total': v['total']}
        for k, v in sorted(payment_totals.items(), key=lambda kv: -kv[1]['total'])
    ]

    return render_template('dashboard.html',
        total_purchases=total_purchases,
        total_sales=total_sales,
        monthly_revenue=monthly_revenue,
        monthly_count=monthly_count,
        today_revenue=today_revenue,
        today_count=today_count,
        stock_count=stock_count,
        recent_sales=recent_sales,
        recent_purchases=recent_purchases,
        low_stock=low_stock,
        chart_data=chart_data,
        payment_split=payment_split,
    )
