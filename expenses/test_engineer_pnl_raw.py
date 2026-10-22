"""Engineer P&L: working days from Payroll, and calls priced from HP raw data."""
import io
from datetime import date
from decimal import Decimal
from unittest import mock

import openpyxl
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from expenses import raw_rates
from expenses.models import EngineerPnl, FlexRawRecord

HEADERS = ['File name', 'Ticket No', 'Case Id', 'WO OTC Code', 'Product Name', 'Business Segment',
           'Work Location', 'WO Closed', 'Call Classification', 'ASP City', 'TOTAL PO VALUE', 'PO REMARK']
INSTALL = '05F-Comp Field Install'
TANK = 'HP Smart Tank 585 All-in-One Printer'


def _workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _raw(ticket, value, remark='PO ISSUED', product=TANK, otc=INSTALL, case='', asp='ASPS01465', closed=45900):
    return ['f', ticket, case, otc, product, 'Printing', asp, closed, 'INSTALL', 'Salem', value, remark]


class RawRateTests(TestCase):
    def setUp(self):
        raw_rates.import_workbook(_workbook([
            _raw('WO-1', 400), _raw('WO-2', 350), _raw('WO-3', 300),
            _raw('WO-4', 0, remark='REJECTED'),
            _raw('WO-5', 0, remark='UNDER EVALUATION WITH HP'),
            _raw('WO-6', 800, product='HP Laptop 15', otc='02N-Normal Warranty', case='C6'),
            ['f', "RENDERWAYS-ChennaiARC", None, 'ARC', None, None, None, None, None, None, 400, 'PO TO BE ISSUED'],
        ]), 'test.xlsx')

    def test_import_skips_non_calls_and_parses_dates(self):
        self.assertEqual(FlexRawRecord.objects.count(), 6)
        self.assertEqual(FlexRawRecord.objects.get(ticket_no='WO-1').closed_on, date(2025, 8, 31))

    def test_reimport_updates_in_place(self):
        res = raw_rates.import_workbook(_workbook([_raw('WO-1', 450)]), 'again.xlsx')
        self.assertEqual((res['created'], res['updated']), (0, 1))
        self.assertEqual(FlexRawRecord.objects.get(ticket_no='WO-1').po_value, Decimal('450.00'))
        # The same file again changes nothing, so nothing is rewritten.
        res = raw_rates.import_workbook(_workbook([_raw('WO-1', 450), _raw('WO-2', 350)]), 'same.xlsx')
        self.assertEqual((res['created'], res['updated'], res['unchanged']), (0, 0, 2))

    def test_pricing_order(self):
        card = raw_rates.rate_card()
        self.assertEqual(card.price({'ticket_id': ' wo-1 '})[:2], (Decimal('400.00'), 'actual'))
        self.assertEqual(card.price({'ticket_id': 'WO-4'})[:2], (Decimal('0.00'), 'rejected'))
        # Pending at HP: estimated from the paid ones (median of 400/350/300).
        self.assertEqual(card.price({'ticket_id': 'WO-5'})[:2], (Decimal('350.00'), 'estimated'))
        # Not in the raw data (newer than the export): same product, same rate.
        new = {'ticket_id': 'WO-NEW', 'product_name': 'hp smart tank 585 all-in-one printer', 'wo_otc_code': INSTALL}
        self.assertEqual(card.price(new)[:2], (Decimal('350.00'), 'estimated'))
        # Matched by case id when the ticket differs.
        self.assertEqual(card.price({'ticket_id': 'X', 'case_id': 'C6'})[:2], (Decimal('800.00'), 'actual'))
        # Never seen: overall median.
        self.assertEqual(card.price({'ticket_id': 'X', 'product_name': 'Unknown'})[1], 'estimated')

    def test_estimate_uses_the_products_recent_price(self):
        # HP paid 400 a year ago and 350 lately (one approved, not yet paid):
        # a new call is priced at what it pays now.
        old = [_raw(f'WO-OLD{i}', 400, product='HP Laser 108a', closed=45900) for i in range(5)]
        new = [_raw(f'WO-NEW{i}', 350, product='HP Laser 108a', closed=46240) for i in range(2)]
        new.append(_raw('WO-NEW9', 350, product='HP Laser 108a', closed=46250, remark='PO TO BE ISSUED'))
        raw_rates.import_workbook(_workbook(old + new), 'more.xlsx')
        value, source, basis = raw_rates.rate_card().price(
            {'ticket_id': 'WO-NEXT', 'product_name': 'HP Laser 108a', 'wo_otc_code': INSTALL})
        self.assertEqual((value, source), (Decimal('350.00'), 'estimated'))
        self.assertIn('typical of 3 paid', basis)

    def test_regions(self):
        calls = raw_rates.price_calls([
            {'ticket_id': 'WO-1', 'work_location': 'ASPS01465'},
            {'ticket_id': 'WO-2', 'work_location': 'ASPS01461'},
            {'ticket_id': 'WO-3', 'work_location': '', 'work_location_name': 'Hosur'},
        ])
        regions = {r['region']: r['value'] for r in raw_rates.by_region(calls)}
        self.assertEqual(regions, {'SALEM': '400.00', 'CHENNAI': '350.00', 'HOSUR': '300.00'})

    def test_calls_carry_what_hp_says_about_them(self):
        rejected, by_case, new = raw_rates.price_calls([
            {'ticket_id': 'WO-4'},
            {'ticket_id': 'X', 'case_id': 'C6'},
            {'ticket_id': 'NOT-IN-RAW', 'product_name': TANK, 'wo_otc_code': INSTALL},
        ])
        self.assertEqual(rejected['raw_source'], 'rejected')
        self.assertEqual(rejected['hp'], {
            'ticket_no': 'WO-4', 'remark': 'REJECTED', 'call_type': 'INSTALL', 'segment': 'Printing',
            'po_value': '0.00', 'closed_on': '2025-08-31',
        })
        self.assertEqual((by_case['hp']['ticket_no'], by_case['hp']['po_value']), ('WO-6', '800.00'))
        self.assertIsNone(new['hp'])

    def test_a_stray_number_is_not_a_call_type(self):
        row = _raw('WO-9', 300)
        row[8] = '46206.77'
        raw_rates.import_workbook(_workbook([row]), 'odd.xlsx')
        self.assertEqual(raw_rates.price_calls([{'ticket_id': 'WO-9'}])[0]['hp']['call_type'], '')


class DashboardSyncTests(TestCase):
    """The raw data follows rawdata.systimus.in's work_orders.json on its own."""
    COLS = ['Category', 'Ticket No', 'Case Id', 'WO OTC Code', 'Product Name', 'Business Segment',
            'Work Location', 'WO Closed', 'Call Classification', 'ASP City', 'Total PO Value',
            'TOTAL PO VALUE', 'PO REMARK']

    def _feed(self, value=400):
        return {'generated': '2026-10-17 10:00', 'cols': self.COLS, 'rows': [
            ['Print', 'WO-1', 5144887942, INSTALL, TANK, 'Printing', 'ASPS01465', '2026-09-01', 'INSTALL', 'Salem', 1, value, 'PO ISSUED'],
            ['ARC', 'RENDERWAYS-ARC', '', 'ARC', '', '', '', '', '', '', 0, 400, 'PO TO BE ISSUED'],
        ]}

    def _resp(self, code=200, payload=None, etag='W/"abc"'):
        r = mock.Mock(status_code=code, headers={'ETag': etag})
        r.json.return_value = payload
        return r

    def test_first_sync_loads_the_feed(self):
        with mock.patch('requests.get', return_value=self._resp(payload=self._feed())) as get:
            res = raw_rates.sync_from_dashboard()
        self.assertTrue(res['ok'])
        self.assertEqual((res['created'], res['skipped']), (1, 1))
        rec = FlexRawRecord.objects.get(ticket_no='WO-1')
        # The exact TOTAL PO VALUE, not the dashboard's derived "Total PO Value".
        self.assertEqual((rec.case_id, rec.po_value, rec.closed_on), ('5144887942', Decimal('400.00'), date(2026, 9, 1)))
        self.assertEqual(get.call_args.args[0], 'https://rawdata.systimus.in/work_orders.json')
        self.assertEqual(raw_rates.status()['generated'], '2026-10-17 10:00')

    def test_unchanged_feed_is_not_downloaded_again(self):
        with mock.patch('requests.get', return_value=self._resp(payload=self._feed())):
            raw_rates.sync_from_dashboard()
        from expenses.models import FlexRawSync
        FlexRawSync.objects.filter(pk=1).update(checked_at=None)
        with mock.patch('requests.get', return_value=self._resp(code=304)) as get:
            res = raw_rates.sync_from_dashboard()
        self.assertEqual(res, {'ok': True, 'unchanged': True})
        self.assertEqual(get.call_args.kwargs['headers'], {'If-None-Match': 'W/"abc"'})

    def test_checked_at_most_every_30_minutes_unless_forced(self):
        with mock.patch('requests.get', return_value=self._resp(payload=self._feed())):
            raw_rates.sync_from_dashboard()
        with mock.patch('requests.get', return_value=self._resp(payload=self._feed(450))) as get:
            self.assertEqual(raw_rates.sync_from_dashboard(), {'ok': True, 'busy': True})
            get.assert_not_called()
            res = raw_rates.sync_from_dashboard(force=True)
        self.assertEqual(res['updated'], 1)
        self.assertEqual(FlexRawRecord.objects.get(ticket_no='WO-1').po_value, Decimal('450.00'))

    def test_a_failed_sync_is_reported_not_raised(self):
        with mock.patch('requests.get', return_value=self._resp(code=500)):
            res = raw_rates.sync_from_dashboard()
        self.assertFalse(res['ok'])
        self.assertIn('HTTP 500', raw_rates.status()['sync_error'])
        self.assertEqual(FlexRawRecord.objects.count(), 0)


class BoardTests(TestCase):
    def setUp(self):
        raw_rates.import_workbook(_workbook([_raw('WO-1', 400), _raw('WO-2', 350), _raw('WO-3', 300)]), 't.xlsx')
        self.admin = User.objects.create_superuser('boss', 'b@x.in', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        EngineerPnl.objects.create(engineer_name='Praveen', email='praveen@x.in', engg_salary=Decimal('31000'))
        EngineerPnl.objects.create(engineer_name='Jeeva', email='', engg_salary=Decimal('31000'))

    def _board(self, working_days=None, rng=('2026-08-25', '2026-09-24')):
        calls = [
            {'engineer_name': 'Praveen', 'ticket_id': 'WO-1', 'work_location': 'ASPS01465'},
            {'engineer_name': 'Praveen', 'ticket_id': 'WO-NEW', 'product_name': TANK,
             'wo_otc_code': INSTALL, 'work_location': 'ASPS01461'},
            {'engineer_name': 'Jeeva', 'ticket_id': 'WO-2', 'work_location': 'ASPS01465'},
        ]
        with mock.patch('expenses.opencall_client.get_engineers', return_value=[]), \
             mock.patch('expenses.opencall_client.get_closed_counts', return_value={
                 'counts': {'praveen': 2, 'jeeva': 1}, 'display': {}, 'meta': {}}), \
             mock.patch('expenses.opencall_client.get_closed_call_details', return_value={'calls': calls, 'meta': {}}), \
             mock.patch('expenses.raw_rates.sync_in_background'), \
             mock.patch('expenses.payroll_client.is_configured', return_value=True), \
             mock.patch('expenses.payroll_client.get_salaries', return_value={'by_email': {}, 'by_name': {}}), \
             mock.patch('expenses.payroll_client.get_employees', return_value=[]), \
             mock.patch('expenses.payroll_client.get_working_days', **(
                 {'return_value': working_days} if working_days else {'side_effect': Exception('404')})):
            res = self.client.get('/api/engineer-pnl/board/', {'from': rng[0], 'to': rng[1], 'sync': '0'})
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    @staticmethod
    def _wd(**praveen):
        """Payroll's working_days answer with one row for Praveen."""
        row = {'present_days': 0, 'absent_days': 0, 'casual_leave_days': 0, 'lop_days': 0,
               'unmarked_days': 0, 'payslip': None, **praveen}
        return {'working_days': 27, 'by_email': {'praveen@x.in': row['present_days']},
                'rows': {'praveen@x.in': row}}

    def test_only_days_marked_absent_are_unpaid(self):
        # 25 Aug - 24 Sep 2026: 31 days, 27 working. 3 marked Absent, one of them
        # covered by casual leave: 2 unpaid, so 29 of 31 days are paid.
        data = self._board(self._wd(present_days=22, absent_days=3, casual_leave_days=1, lop_days=2,
                                    unmarked_days=2))
        rows = {r['engineer_name']: r for r in data['rows']}
        p = rows['Praveen']
        self.assertEqual((p['charged_days'], p['paid_days'], p['absent_days']), (31, 29, 2))
        self.assertEqual((p['absent_marked'], p['casual_leave_days'], p['unmarked_days']), (3, 1, 2))
        self.assertEqual(p['window_salary'], '29000.00')        # 31000 / 31 x 29
        self.assertEqual(p['salary_basis'], 'attendance')
        self.assertEqual(p['nett'], str(Decimal(p['engg_earning']) - Decimal('29000.00')))
        self.assertEqual((p['total_working_days'], p['actual_working_days'], p['actual_closed_pd']), (27, 22, '0.09'))
        # Not linked to Payroll: no attendance, so every day is charged.
        j = rows['Jeeva']
        self.assertEqual((j['paid_days'], j['window_salary'], j['salary_basis']), (31, '31000.00', 'manual'))
        self.assertIsNone(j['actual_working_days'])

    def test_a_finished_cycle_uses_its_payslip(self):
        slip = {'status': 'Paid', 'paid_days': '29.00', 'lop_days': '3.00', 'casual_leave_used': '1.00',
                'earned': '30123.45'}
        data = self._board(self._wd(present_days=24, absent_days=3, lop_days=2, payslip=slip))
        p = {r['engineer_name']: r for r in data['rows']}['Praveen']
        self.assertEqual((p['window_salary'], p['salary_basis'], p['absent_days']), ('30123.45', 'payslip', 2))

    def test_salary_is_only_charged_up_to_today(self):
        from datetime import date as _date, timedelta as _td
        today = _date.today()
        data = self._board(self._wd(present_days=3),
                           rng=((today - _td(days=2)).isoformat(), (today + _td(days=10)).isoformat()))
        p = {r['engineer_name']: r for r in data['rows']}['Praveen']
        self.assertEqual((data['charged_to'], p['charged_days']), (today.isoformat(), 3))
        self.assertEqual(data['period_days'], 13)

    def test_a_future_window_charges_nothing(self):
        from datetime import date as _date, timedelta as _td
        start = _date.today() + _td(days=1)
        data = self._board(self._wd(), rng=(start.isoformat(), (start + _td(days=6)).isoformat()))
        p = {r['engineer_name']: r for r in data['rows']}['Praveen']
        self.assertEqual((p['charged_days'], p['window_salary'], data['charged_to']), (0, '0.00', None))

    def test_payroll_down_falls_back(self):
        data = self._board()
        self.assertFalse(data['working_days_ok'])
        self.assertTrue(all(r['working_days_source'] == 'manual' for r in data['rows']))

    def test_raw_columns_and_regions(self):
        data = self._board({'working_days': 27, 'by_email': {}, 'rows': {}})
        p = {r['engineer_name']: r for r in data['rows']}['Praveen']
        # WO-1 actual 400 + WO-NEW estimated 350 → 750 over 2 calls.
        self.assertEqual(p['raw_earning'], '750.00')
        self.assertEqual(p['raw_rate'], '375.00')
        self.assertEqual((p['raw_actual_calls'], p['raw_estimated_calls']), (1, 1))
        # The flat-rate P/L is untouched; raw P/L sits beside it.
        self.assertEqual(p['engg_earning'], '840.00')
        self.assertEqual(p['raw_profit_loss'], str(Decimal('750.00') - Decimal(p['window_salary'])))
        regions = {r['region']: r['value'] for r in data['regions']}
        self.assertEqual(regions, {'SALEM': '750.00', 'CHENNAI': '350.00'})
        salem = next(r for r in data['regions'] if r['region'] == 'SALEM')
        self.assertEqual(
            sorted((e['engineer_name'], e['calls'], e['value']) for e in salem['engineers']),
            [('Jeeva', 1, '350.00'), ('Praveen', 1, '400.00')],
        )


class SalaryCalcTests(TestCase):
    """The salary rules on their own, one edge case at a time."""
    from datetime import date as D

    def _seg(self, a, b, row=None, email='e@x.in'):
        from expenses import salary_calc
        segs = []
        for s_, e_, cs, ce in salary_calc.segments(a, b):
            segs.append({'start': s_, 'end': e_, 'cycle_start': cs, 'cycle_end': ce,
                         'rows': {email: row} if row is not None else {}})
        return segs

    def test_cycle_bounds(self):
        from expenses.salary_calc import cycle_bounds
        D = self.D
        self.assertEqual(cycle_bounds(D(2026, 10, 10)), (D(2026, 9, 25), D(2026, 10, 24)))
        self.assertEqual(cycle_bounds(D(2026, 10, 25)), (D(2026, 10, 25), D(2026, 11, 24)))
        self.assertEqual(cycle_bounds(D(2027, 1, 3)), (D(2026, 12, 25), D(2027, 1, 24)))   # across a year
        self.assertEqual(cycle_bounds(D(2028, 3, 1)), (D(2028, 2, 25), D(2028, 3, 24)))    # leap Feb: 29 days

    def test_a_range_across_two_cycles_uses_each_cycles_rate(self):
        from expenses.salary_calc import engineer_salary
        D = self.D
        # 20-29 Sep: 5 days of the Sep cycle (31 days) + 5 of the Oct cycle (30 days).
        segs = self._seg(D(2026, 9, 20), D(2026, 9, 29), row={'lop_days': 0})
        self.assertEqual(len(segs), 2)
        res = engineer_salary(Decimal('30000'), segs, 'e@x.in')
        expected = (Decimal('30000') * 5 / 31 + Decimal('30000') * 5 / 30).quantize(Decimal('0.01'))
        self.assertEqual(res['earned'], expected)
        self.assertEqual((res['charged_days'], res['paid_days']), (10, 10))

    def test_users_example(self):
        from expenses.salary_calc import engineer_salary
        D = self.D
        # 26,207 a month, 31-day cycle, 2 days unpaid: 29 paid days.
        segs = self._seg(D(2026, 8, 25), D(2026, 9, 24), row={'lop_days': 2})
        self.assertEqual(engineer_salary(Decimal('26207'), segs, 'e@x.in')['earned'], Decimal('24516.23'))

    def test_unpaid_days_never_exceed_the_days_charged(self):
        from expenses.salary_calc import engineer_salary
        D = self.D
        segs = self._seg(D(2026, 10, 10), D(2026, 10, 10), row={'lop_days': 5})
        res = engineer_salary(Decimal('30000'), segs, 'e@x.in')
        self.assertEqual((res['earned'], res['paid_days'], res['lop_days']), (Decimal('0.00'), 0, 1))

    def test_unlinked_engineer_is_charged_every_day(self):
        from expenses.salary_calc import engineer_salary
        D = self.D
        segs = self._seg(D(2026, 8, 25), D(2026, 9, 24), row={'lop_days': 9})
        res = engineer_salary(Decimal('31000'), segs, None)
        self.assertEqual((res['earned'], res['basis']), (Decimal('31000.00'), 'manual'))
