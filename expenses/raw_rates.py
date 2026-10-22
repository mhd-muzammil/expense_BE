"""Price closed calls with HP's own Flex raw data.

A closed call is priced in this order:

1. ``actual``    — the ticket is in the raw export and HP issued (or is about to
                   issue) a PO for it: that PO value.
2. ``rejected``  — the ticket is in the export and HP rejected or cancelled it:
                   nothing was paid, so 0.
3. ``estimated`` — anything else: the call is newer than the last export, or HP
                   has not decided it yet. Priced at the median HP paid for
                   comparable calls, narrowest match first:
                     product + WO OTC code → product → WO OTC code → everything.
                   A band needs a few paid calls before it counts as a rate,
                   and only the band's most recent ~3 months are used: HP's
                   rates move (a Smart Tank install paid 401 in 2025, 350 in
                   2026), so the reference is what it pays now, not on average.

The medians are built from rows HP priced (PO ISSUED, or PO TO BE ISSUED —
approved but not yet paid, which is where the newest month always sits), so
they are HP's pricing and not ours.
"""
import datetime
import os
import re
import threading
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import FlexRawRecord, FlexRawSync


# ASP code → region. Same map the Flex dashboard and OpenCall use.
ASP_REGIONS = {
    'ASPS01461': 'CHENNAI',
    'ASPS01463': 'VELLORE',
    'ASPS01465': 'SALEM',
    'ASPS01489': 'KANCHIPURAM',
    'ASPS01511': 'HOSUR',
}

MIN_PRODUCT_SAMPLES = 3
MIN_OTC_SAMPLES = 5
# How far back from a band's latest paid call its reference rate looks.
RECENT_DAYS = 92

_PAID = ('PO ISSUED', 'PO TO BE ISSUED')
_ZEROED = ('REJECTED', 'CALL CANCELLED')


def norm(value):
    """Loose key for names typed by two different systems."""
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def region_for(asp_code, fallback_name=''):
    code = str(asp_code or '').strip().upper()
    return ASP_REGIONS.get(code) or str(fallback_name or '').strip().upper() or 'UNKNOWN'


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
_REQUIRED = {'Ticket No', 'Product Name', 'TOTAL PO VALUE'}


def _excel_date(v):
    if v in (None, ''):
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)):
        if 20000 < v < 80000:  # Excel serial day
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))
        return None
    s = str(v).strip()[:19]
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y', '%d/%m/%Y', '%d-%b-%y', '%d-%b-%Y', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _money(v):
    try:
        return Decimal(str(v or 0)).quantize(Decimal('0.01'))
    except Exception:
        return Decimal('0.00')


def _text(v, limit):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()[:limit]


def _records(header, rows, source_name):
    """Turn a header + row iterator (Excel sheet or the dashboard's JSON) into
    FlexRawRecord objects keyed by ticket. Rows without a Ticket No, or without a
    product (the month-end ARC / accessories lines HP appends), are not calls."""
    col = {}
    for i, h in enumerate(header):
        col.setdefault(h, i)  # the export repeats some headers; the first wins

    def g(row, name):
        i = col.get(name)
        return row[i] if i is not None and i < len(row) else None

    parsed = {}
    skipped = 0
    for row in rows:
        ticket = _text(g(row, 'Ticket No'), 120).upper()
        product = _text(g(row, 'Product Name'), 300)
        if not ticket or not product:
            skipped += 1
            continue
        parsed[ticket] = FlexRawRecord(
            ticket_no=ticket,
            case_id=_text(g(row, 'Case Id'), 40),
            product_name=product,
            wo_otc_code=_text(g(row, 'WO OTC Code'), 120),
            business_segment=_text(g(row, 'Business Segment'), 60),
            call_classification=_text(g(row, 'Call Classification'), 40).upper(),
            work_location=_text(g(row, 'Work Location'), 40).upper(),
            asp_city=_text(g(row, 'ASP City'), 80),
            closed_on=_excel_date(g(row, 'WO Closed')),
            po_value=_money(g(row, 'TOTAL PO VALUE')),
            po_remark=_text(g(row, 'PO REMARK'), 120).upper(),
            source_file=(source_name or '')[:255],
        )
    return parsed, skipped


def _upsert(parsed, skipped):
    fields = ['case_id', 'product_name', 'wo_otc_code', 'business_segment',
              'call_classification', 'work_location', 'asp_city', 'closed_on',
              'po_value', 'po_remark']
    with transaction.atomic():
        # Each export repeats every earlier call, so most rows are already here
        # unchanged. Writing only the new and the changed ones keeps an import to
        # seconds; rewriting all ~14k took close to a minute, near the worker timeout.
        existing = {
            row[0]: row[1:]
            for row in FlexRawRecord.objects.values_list('ticket_no', 'id', *fields)
        }
        to_update, to_create = [], []
        for ticket, rec in parsed.items():
            old = existing.get(ticket)
            if old is None:
                to_create.append(rec)
            elif tuple(old[1:]) != tuple(getattr(rec, f) for f in fields):
                rec.id = old[0]
                to_update.append(rec)
        FlexRawRecord.objects.bulk_create(to_create, batch_size=1000)
        # bulk_update skips auto_now, so stamp the import time here.
        FlexRawRecord.objects.bulk_update(to_update, fields + ['source_file'], batch_size=500)
        if to_update:
            FlexRawRecord.objects.filter(id__in=[r.id for r in to_update]).update(imported_at=timezone.now())
    _cache['key'] = None
    return {
        'rows': len(parsed), 'created': len(to_create), 'updated': len(to_update),
        'unchanged': len(parsed) - len(to_create) - len(to_update), 'skipped': skipped,
    }


def import_workbook(file_obj, source_name=''):
    """Upsert every work order in a Flex raw export (.xlsx). Returns a summary dict."""
    import openpyxl

    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    sheet, header = None, None
    for ws in wb.worksheets:
        first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
        names = [str(h).strip() if h is not None else '' for h in (first or [])]
        if _REQUIRED.issubset(names) and (header is None or len(names) > len(header)):
            sheet, header = ws, names
    if sheet is None:
        wb.close()
        raise ValueError('This is not a Flex raw export: Ticket No / Product Name / TOTAL PO VALUE columns not found.')
    parsed, skipped = _records(header, sheet.iter_rows(min_row=2, values_only=True), source_name)
    wb.close()
    return _upsert(parsed, skipped)


def import_table(payload, source_name=''):
    """Upsert from the raw-data dashboard's work_orders.json: {cols: [...], rows: [[...]]}.
    The dashboard keeps HP's own column names, so the same mapping applies."""
    cols = [str(c).strip() for c in (payload or {}).get('cols') or []]
    if not _REQUIRED.issubset(cols):
        raise ValueError('The raw-data feed has no Ticket No / Product Name / TOTAL PO VALUE columns.')
    parsed, skipped = _records(cols, (payload or {}).get('rows') or [], source_name)
    return _upsert(parsed, skipped)


# ---------------------------------------------------------------------------
# Sync from the raw-data dashboard (rawdata.systimus.in)
# ---------------------------------------------------------------------------
# The dashboard republishes work_orders.json each time a new HP export is added
# there, so the board picks the data up on its own instead of needing an upload.
SYNC_EVERY = datetime.timedelta(minutes=30)
_SYNC_TIMEOUT = 45


def raw_data_url():
    return os.environ.get('RAW_DATA_URL', 'https://rawdata.systimus.in/work_orders.json').strip()


def sync_from_dashboard(force=False):
    """Pull work_orders.json if it changed since the last sync. Returns a summary.

    Uses the response ETag, so an unchanged file costs one small request. Only
    one caller at a time does the work (claimed atomically in the database, so
    the gunicorn workers never run it twice); the rest return straight away."""
    import requests

    url = raw_data_url()
    if not url:
        return {'ok': False, 'error': 'RAW_DATA_URL is empty'}
    FlexRawSync.objects.get_or_create(pk=1)
    now = timezone.now()
    claim = FlexRawSync.objects.filter(pk=1)
    if not force:
        claim = claim.filter(Q(checked_at__isnull=True) | Q(checked_at__lt=now - SYNC_EVERY))
    # A crashed sync must not block forever: a claim older than 10 min is stale.
    claim = claim.filter(Q(running_since__isnull=True) | Q(running_since__lt=now - datetime.timedelta(minutes=10)))
    if not claim.update(checked_at=now, running_since=now):
        return {'ok': True, 'busy': True}

    state = FlexRawSync.objects.get(pk=1)
    try:
        headers = {} if force or not state.etag else {'If-None-Match': state.etag}
        resp = requests.get(url, headers=headers, timeout=_SYNC_TIMEOUT)
        if resp.status_code == 304:
            FlexRawSync.objects.filter(pk=1).update(running_since=None, last_error='')
            return {'ok': True, 'unchanged': True}
        if resp.status_code != 200:
            raise ValueError(f'{url} answered HTTP {resp.status_code}')
        payload = resp.json()
        generated = str(payload.get('generated') or '')[:64]
        result = import_table(payload, 'rawdata.systimus.in' + (f' ({generated})' if generated else ''))
        FlexRawSync.objects.filter(pk=1).update(
            running_since=None, synced_at=timezone.now(), etag=resp.headers.get('ETag', '')[:200],
            generated=generated, last_error='',
            last_result=f"{result['created']} new, {result['updated']} updated, {result['unchanged']} unchanged",
        )
        return {'ok': True, **result}
    except Exception as e:
        FlexRawSync.objects.filter(pk=1).update(running_since=None, last_error=str(e)[:500])
        return {'ok': False, 'error': str(e)}


def sync_in_background():
    """Fire-and-forget sync for a page load: never slows or breaks the board."""
    state = FlexRawSync.objects.filter(pk=1).values('checked_at', 'running_since').first()
    now = timezone.now()
    if state and (state['running_since'] or (state['checked_at'] and now - state['checked_at'] < SYNC_EVERY)):
        return

    def run():
        from django.db import connection
        try:
            sync_from_dashboard()
        except Exception:
            pass
        finally:
            connection.close()
    threading.Thread(target=run, daemon=True).start()


def _sync_status():
    st = FlexRawSync.objects.filter(pk=1).first()
    if not st:
        return {'synced_at': None, 'checked_at': None, 'generated': '', 'sync_error': '', 'sync_result': ''}
    return {
        'synced_at': st.synced_at.isoformat() if st.synced_at else None,
        'checked_at': st.checked_at.isoformat() if st.checked_at else None,
        'generated': st.generated,
        'sync_error': st.last_error,
        'sync_result': st.last_result,
    }


def status():
    agg = FlexRawRecord.objects.aggregate(
        count=Count('id'), last_import=Max('imported_at'), closed_to=Max('closed_on'),
    )
    first = (FlexRawRecord.objects.exclude(closed_on=None).order_by('closed_on')
             .values_list('closed_on', flat=True).first())
    return {
        'count': agg['count'],
        'closed_from': first.isoformat() if first else None,
        'closed_to': agg['closed_to'].isoformat() if agg['closed_to'] else None,
        'last_import': agg['last_import'].isoformat() if agg['last_import'] else None,
        'source_url': raw_data_url(),
        **_sync_status(),
    }


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------
_cache = {'key': None, 'card': None}
_lock = threading.Lock()


def _month_span(a, b):
    if not a or not b:
        return ''
    fa, fb = a.strftime('%b %Y'), b.strftime('%b %Y')
    return fa if fa == fb else f"{a.strftime('%b')}–{fb}" if a.year == b.year else f'{fa}–{fb}'


def _recent_rate(items, min_n):
    """``(rate, basis)`` from a band's paid ``(closed_on, value)`` items, using
    only its latest RECENT_DAYS; None when there are too few to be a rate."""
    dated = [i for i in items if i[0]]
    if dated:
        latest = max(d for d, _ in dated)
        window = [i for i in dated if (latest - i[0]).days <= RECENT_DAYS]
        if len(window) < min_n:      # a thin recent window: widen to all of it
            window = dated
    else:
        window = items
    if len(window) < min_n:
        return None
    dates = [d for d, _ in window if d]
    span = _month_span(min(dates), max(dates)) if dates else ''
    return _median([v for _, v in window]), f"typical of {len(window)} paid{', ' + span if span else ''}"


def _median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return Decimal('0.00')
    mid = n // 2
    m = s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
    return Decimal(m).quantize(Decimal('0.01'))


class RateCard:
    def __init__(self, records):
        self.by_ticket = {}
        self.by_case = {}
        prod_otc, prod, otc, every = {}, {}, {}, []
        for r in records:
            self.by_ticket[r['ticket_no']] = r
            if r['case_id']:
                # A case can appear twice (a rejected "already paid" twin next to
                # the paid row). Prefer the row that carries money.
                prev = self.by_case.get(r['case_id'])
                if prev is None or r['po_value'] > prev['po_value']:
                    self.by_case[r['case_id']] = r
            if r['po_remark'].startswith(_PAID) and r['po_value'] > 0:
                p, o = norm(r['product_name']), norm(r['wo_otc_code'])
                item = (r.get('closed_on'), r['po_value'])
                prod_otc.setdefault((p, o), []).append(item)
                prod.setdefault(p, []).append(item)
                otc.setdefault(o, []).append(item)
                every.append(item)

        def bands(groups, min_n):
            out = {}
            for k, items in groups.items():
                rate = _recent_rate(items, min_n)
                if rate:
                    out[k] = rate
            return out
        self.prod_otc = bands(prod_otc, MIN_PRODUCT_SAMPLES)
        self.prod = bands(prod, MIN_PRODUCT_SAMPLES)
        self.otc = bands(otc, MIN_OTC_SAMPLES)
        self.fallback = _recent_rate(every, 1) or (Decimal('0.00'), 'no paid calls')
        self.size = len(self.by_ticket)

    def estimate(self, product, otc):
        p, o = norm(product), norm(otc)
        if (p, o) in self.prod_otc:
            rate, basis = self.prod_otc[(p, o)]
            return rate, f'same product and call type, {basis}'
        if p in self.prod:
            rate, basis = self.prod[p]
            return rate, f'same product, {basis}'
        if o in self.otc:
            rate, basis = self.otc[o]
            return rate, f'new product; same call type, {basis}'
        rate, basis = self.fallback
        return rate, f'new product; all calls, {basis}'

    def record(self, call):
        """The HP raw-data row for one OpenCall closed call: by ticket, else by case."""
        rec = self.by_ticket.get(str(call.get('ticket_id') or '').strip().upper())
        if rec is None:
            case = str(call.get('case_id') or '').strip()
            rec = self.by_case.get(case) if case else None
        return rec

    def price(self, call):
        """``(value, source, basis)`` for one OpenCall closed call."""
        rec = self.record(call)
        if rec is not None:
            remark = rec['po_remark']
            if remark.startswith(_PAID) and rec['po_value'] > 0:
                return rec['po_value'], 'actual', remark
            if remark.startswith(_ZEROED):
                return Decimal('0.00'), 'rejected', remark
            value, basis = self.estimate(rec['product_name'], rec['wo_otc_code'])
            return value, 'estimated', f'{remark or "PENDING"} at HP · {basis}'
        value, basis = self.estimate(call.get('product_name'), call.get('wo_otc_code'))
        return value, 'estimated', f'Not in raw data yet · {basis}'


def rate_card():
    """The current rate card, rebuilt only when the raw data changes."""
    agg = FlexRawRecord.objects.aggregate(n=Count('id'), at=Max('imported_at'))
    key = (agg['n'], agg['at'])
    with _lock:
        if _cache['key'] != key:
            rows = FlexRawRecord.objects.values(
                'ticket_no', 'case_id', 'product_name', 'wo_otc_code', 'po_value', 'po_remark', 'closed_on',
                'call_classification', 'business_segment',
            )
            _cache['card'] = RateCard(rows)
            _cache['key'] = key
        return _cache['card']


def hp_details(rec):
    """What HP's raw data says about a call, for showing why it is paid, pending
    or rejected; None when the call is not in the raw data yet."""
    if rec is None:
        return None
    kind = (rec.get('call_classification') or '').strip()
    return {
        'ticket_no': rec['ticket_no'],
        'remark': rec['po_remark'],
        # Some export rows carry a stray Excel number here instead of BREAKFIX/INSTALL.
        'call_type': kind if kind.isalpha() else '',
        'segment': rec.get('business_segment') or '',
        'po_value': str(rec['po_value']),
        'closed_on': rec['closed_on'].isoformat() if rec.get('closed_on') else None,
    }


def price_calls(calls, card=None):
    """Annotate each call dict in place with raw_value / raw_source / raw_basis / region,
    and hp: the raw-data row behind it (see ``hp_details``)."""
    card = card or rate_card()
    for c in calls:
        value, source, basis = card.price(c)
        c['hp'] = hp_details(card.record(c))
        c['raw_value'] = value
        c['raw_source'] = source
        c['raw_basis'] = basis
        c['region'] = region_for(c.get('work_location'), c.get('work_location_name'))
    return calls


def summarize(calls):
    """Totals over priced calls: value, call count and how many of each source."""
    out = {'calls': 0, 'value': Decimal('0.00'), 'actual': 0, 'rejected': 0, 'estimated': 0,
           'actual_value': Decimal('0.00'), 'estimated_value': Decimal('0.00')}
    for c in calls:
        out['calls'] += 1
        out['value'] += c['raw_value']
        out[c['raw_source']] += 1
        if c['raw_source'] == 'actual':
            out['actual_value'] += c['raw_value']
        elif c['raw_source'] == 'estimated':
            out['estimated_value'] += c['raw_value']
    return out


def by_region(calls):
    groups = {}
    for c in calls:
        groups.setdefault(c['region'], []).append(c)
    rows = []
    for region, items in groups.items():
        s = summarize(items)
        # Who earned the region's total, so a region's figure can be traced to people.
        per_eng = {}
        for c in items:
            name = (c.get('engineer_name') or c.get('engineer') or '').strip() or 'Unassigned'
            per_eng.setdefault(name, []).append(c)
        engineers = [
            {'engineer_name': n, 'calls': len(cs), 'value': str(sum((c['raw_value'] for c in cs), Decimal('0.00')))}
            for n, cs in per_eng.items()
        ]
        engineers.sort(key=lambda e: -Decimal(e['value']))
        rows.append({
            'region': region,
            **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in s.items()},
            'engineers': engineers,
        })
    rows.sort(key=lambda r: -Decimal(r['value']))
    return rows
