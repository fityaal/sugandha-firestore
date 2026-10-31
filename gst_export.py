"""
gst_export.py
Builds GST-filing registers (sales = outward supplies, purchase = inward
supplies) for a date range and renders them as Excel (.xlsx) or CSV, in a
layout a CA can work from directly (GSTR-1 / GSTR-3B / ITC reconciliation).

Used by both the web portal (routes/reports.py) and the mobile API
(routes/api.py) so both always produce identical numbers.

Tax split rule: if the party's GSTIN is present and its 2-digit state code
differs from the shop's state code, the supply is inter-state (IGST).
Otherwise (same state, or no GSTIN) it is intra-state (CGST + SGST, half each).
"""
import csv
import io
from collections import defaultdict
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from firebase_db import cached_docs_list

KINDS = {
    'sales': {
        'collection': 'sales',
        'title': 'Sales Register (Outward Supplies)',
        'date': 'sell_date', 'qty': 'sell_qty',
        'party': 'customer_name', 'gstin': 'gst_no',
        'party_label': 'Customer', 'number_label': 'Invoice No',
        'sheet': 'Sales Register', 'file': 'Sales',
    },
    'purchase': {
        'collection': 'purchases',
        'title': 'Purchase Register (Inward Supplies)',
        'date': 'purchase_date', 'qty': 'qty',
        'party': 'firm_name', 'gstin': 'firm_gst_no',
        'party_label': 'Supplier', 'number_label': 'Bill No',
        'sheet': 'Purchase Register', 'file': 'Purchase',
    },
}


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _sort_number(n):
    n = str(n or '').strip()
    return (0, int(n), '') if n.isdigit() else (1, 0, n)


def _state_of(gstin):
    g = (gstin or '').strip()
    return g[:2] if len(g) >= 2 and g[:2].isdigit() else ''


def build_register(kind, from_date, to_date, shop_state_code='27'):
    """Return (rows, totals). Dates are 'YYYY-MM-DD' strings (inclusive)."""
    cfg = KINDS[kind]
    raw = [r for r in cached_docs_list(cfg['collection'])
           if (not from_date or (r.get(cfg['date']) or '') >= from_date)
           and (not to_date or (r.get(cfg['date']) or '') <= to_date)]
    raw.sort(key=lambda r: ((r.get(cfg['date']) or ''), _sort_number(r.get('bill_no'))))

    # Duplicate detection: same number (and same party for purchases, since
    # different suppliers can legitimately reuse a bill number).
    def dup_key(r):
        if kind == 'purchase':
            return (str(r.get('bill_no') or '').strip(), (r.get(cfg['party']) or '').strip().upper())
        return str(r.get('bill_no') or '').strip()

    counts = defaultdict(int)
    for r in raw:
        counts[dup_key(r)] += 1

    rows = []
    for r in raw:
        rate = _num(r.get('gst_percent'))
        total = _num(r.get('total_price'))
        taxable = r.get('price_without_gst')
        taxable = _num(taxable) if taxable not in (None, '') else round(total / (1 + rate / 100), 2)
        gst = r.get('gst_amount')
        gst = _num(gst) if gst not in (None, '') else round(total - taxable, 2)

        gstin = (r.get(cfg['gstin']) or '').strip().upper()
        pstate = _state_of(gstin)
        inter = bool(pstate) and pstate != str(shop_state_code)
        if inter:
            cgst = sgst = 0.0
            igst = round(gst, 2)
        else:
            cgst = round(gst / 2, 2)
            sgst = round(gst - cgst, 2)
            igst = 0.0

        try:
            d = datetime.strptime(r.get(cfg['date']) or '', '%Y-%m-%d').date()
        except ValueError:
            d = None

        rows.append({
            'date': d,
            'number': str(r.get('bill_no') or '').strip(),
            'party': (r.get(cfg['party']) or '').strip(),
            'gstin': gstin,
            'type': ('B2B' if gstin else 'B2C') if kind == 'sales' else ('Registered' if gstin else 'Unregistered'),
            'item': r.get('item_name') or '',
            'hsn': r.get('hsn') or '',
            'qty': int(_num(r.get(cfg['qty'])) or 1),
            'rate': rate,
            'taxable': round(taxable, 2),
            'cgst': cgst, 'sgst': sgst, 'igst': igst,
            'gst': round(gst, 2),
            'total': round(total, 2),
            'payment': r.get('payment_type', '') if kind == 'sales' else '',
            'remarks': 'DUPLICATE ENTRY - CHECK' if counts[dup_key(r)] > 1 else '',
        })

    totals = {k: round(sum(x[k] for x in rows), 2) for k in ('taxable', 'cgst', 'sgst', 'igst', 'gst', 'total')}
    totals['count'] = len(rows)
    totals['duplicates'] = sum(1 for x in rows if x['remarks'])
    return rows, totals


def rate_summary(rows):
    by = defaultdict(lambda: {'count': 0, 'taxable': 0.0, 'cgst': 0.0, 'sgst': 0.0, 'igst': 0.0, 'gst': 0.0, 'total': 0.0})
    for r in rows:
        b = by[r['rate']]
        b['count'] += 1
        for k in ('taxable', 'cgst', 'sgst', 'igst', 'gst', 'total'):
            b[k] += r[k]
    return sorted(by.items())


def _columns(kind):
    cfg = KINDS[kind]
    cols = ['Sr', 'Date', cfg['number_label'], cfg['party_label'], f"{cfg['party_label']} GSTIN",
            'Type', 'Item', 'HSN', 'Qty', 'GST %', 'Taxable Value', 'CGST', 'SGST', 'IGST',
            'Total GST', 'Invoice Value']
    if kind == 'sales':
        cols.append('Payment')
    cols.append('Remarks')
    return cols


def _row_values(kind, i, r):
    vals = [i, r['date'], r['number'], r['party'], r['gstin'], r['type'], r['item'], r['hsn'],
            r['qty'], r['rate'], r['taxable'], r['cgst'], r['sgst'], r['igst'], r['gst'], r['total']]
    if kind == 'sales':
        vals.append(r['payment'])
    vals.append(r['remarks'])
    return vals


def render_csv(kind, rows, totals):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_columns(kind))
    for i, r in enumerate(rows, 1):
        vals = _row_values(kind, i, r)
        vals[1] = r['date'].strftime('%d/%m/%Y') if r['date'] else ''
        w.writerow(vals)
    n = len(_columns(kind))
    tot = [''] * n
    tot[3] = 'TOTAL'
    for key, idx in (('taxable', 10), ('cgst', 11), ('sgst', 12), ('igst', 13), ('gst', 14), ('total', 15)):
        tot[idx] = f"{totals[key]:.2f}"
    w.writerow(tot)
    return ('\ufeff' + buf.getvalue()).encode('utf-8')  # BOM so Excel reads it as UTF-8


def render_xlsx(kind, rows, totals, shop, from_date, to_date):
    cfg = KINDS[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = cfg['sheet']

    bold = Font(bold=True)
    head_fill = PatternFill('solid', fgColor='0B2545')
    head_font = Font(bold=True, color='FFFFFF')
    dup_fill = PatternFill('solid', fgColor='FFE0B2')
    thin = Side(style='thin', color='BBBBBB')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = '#,##0.00'

    cols = _columns(kind)
    ncols = len(cols)
    period = f"Period: {_dmy(from_date)} to {_dmy(to_date)}" if from_date or to_date else 'Period: All dates'

    ws['A1'] = shop.get('shop_name', '')
    ws['A1'].font = Font(bold=True, size=14)
    ws['A2'] = shop.get('shop_address', '')
    ws['A3'] = f"GSTIN: {shop.get('shop_gst_no', '')}"
    ws['A4'] = cfg['title']
    ws['A4'].font = Font(bold=True, size=12)
    ws['A5'] = period

    hdr = 7
    for c, name in enumerate(cols, 1):
        cell = ws.cell(row=hdr, column=c, value=name)
        cell.font, cell.fill, cell.border = head_font, head_fill, border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    money_cols = {11, 12, 13, 14, 15, 16}
    for i, r in enumerate(rows, 1):
        rr = hdr + i
        for c, v in enumerate(_row_values(kind, i, r), 1):
            cell = ws.cell(row=rr, column=c, value=v)
            cell.border = border
            if c == 2:
                cell.number_format = 'DD/MM/YYYY'
            elif c in money_cols:
                cell.number_format = money
            if r['remarks']:
                cell.fill = dup_fill

    tr = hdr + len(rows) + 1
    ws.cell(row=tr, column=4, value='TOTAL').font = bold
    for key, c in (('taxable', 11), ('cgst', 12), ('sgst', 13), ('igst', 14), ('gst', 15), ('total', 16)):
        cell = ws.cell(row=tr, column=c, value=totals[key])
        cell.font, cell.number_format = bold, money
    for c in range(1, ncols + 1):
        ws.cell(row=tr, column=c).border = border

    widths = [5, 12, 12, 32, 18, 12, 28, 10, 6, 8, 14, 12, 12, 12, 12, 14] + ([12] if kind == 'sales' else []) + [24]
    for c, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = ws.cell(row=hdr + 1, column=1)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f'{hdr}:{hdr}'

    # ---- Summary sheet (for GSTR-3B style rate-wise totals) ----
    ss = wb.create_sheet('Rate-wise Summary')
    ss['A1'] = f"{cfg['title']} - Rate-wise Summary"
    ss['A1'].font = Font(bold=True, size=12)
    ss['A2'] = period
    heads = ['GST %', 'Invoices', 'Taxable Value', 'CGST', 'SGST', 'IGST', 'Total GST', 'Invoice Value']
    for c, h in enumerate(heads, 1):
        cell = ss.cell(row=4, column=c, value=h)
        cell.font, cell.fill, cell.border = head_font, head_fill, border
        cell.alignment = Alignment(horizontal='center')
    r_i = 5
    for rate, b in rate_summary(rows):
        vals = [rate, b['count'], b['taxable'], b['cgst'], b['sgst'], b['igst'], b['gst'], b['total']]
        for c, v in enumerate(vals, 1):
            cell = ss.cell(row=r_i, column=c, value=round(v, 2) if c > 2 else v)
            cell.border = border
            if c > 2:
                cell.number_format = money
        r_i += 1
    ss.cell(row=r_i, column=1, value='TOTAL').font = bold
    ss.cell(row=r_i, column=2, value=totals['count']).font = bold
    for c, key in zip(range(3, 9), ('taxable', 'cgst', 'sgst', 'igst', 'gst', 'total')):
        cell = ss.cell(row=r_i, column=c, value=totals[key])
        cell.font, cell.number_format = bold, money
    for c in range(1, 9):
        ss.cell(row=r_i, column=c).border = border

    if kind == 'sales':
        r_i += 3
        ss.cell(row=r_i, column=1, value='B2B vs B2C (by customer GSTIN)').font = bold
        r_i += 1
        for c, h in enumerate(['Type', 'Invoices', 'Taxable Value', 'Total GST', 'Invoice Value'], 1):
            cell = ss.cell(row=r_i, column=c, value=h)
            cell.font, cell.fill, cell.border = head_font, head_fill, border
        for t in ('B2B', 'B2C'):
            sub = [x for x in rows if x['type'] == t]
            r_i += 1
            for c, v in enumerate([t, len(sub), round(sum(x['taxable'] for x in sub), 2),
                                   round(sum(x['gst'] for x in sub), 2), round(sum(x['total'] for x in sub), 2)], 1):
                cell = ss.cell(row=r_i, column=c, value=v)
                cell.border = border
                if c > 2:
                    cell.number_format = money
    for c, w in enumerate([12, 10, 16, 14, 14, 14, 14, 16], 1):
        ss.column_dimensions[get_column_letter(c)].width = w

    if totals['duplicates']:
        ss.cell(row=r_i + 3, column=1,
                value=f"NOTE: {totals['duplicates']} row(s) share a duplicate number and are highlighted "
                      f"in the register. Verify before filing.").font = Font(bold=True, color='C62828')

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _dmy(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return s or ''


def export_filename(kind, from_date, to_date, ext):
    return f"{KINDS[kind]['file']}_Register_{from_date or 'start'}_to_{to_date or 'end'}.{ext}"
