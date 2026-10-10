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


class BoardTests(TestCase):
    def setUp(self):
        raw_rates.import_workbook(_workbook([_raw('WO-1', 400), _raw('WO-2', 350), _raw('WO-3', 300)]), 't.xlsx')
        self.admin = User.objects.create_superuser('boss', 'b@x.in', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        EngineerPnl.objects.create(engineer_name='Praveen', email='praveen@x.in', engg_salary=Decimal('31000'))
        EngineerPnl.objects.create(engineer_name='Jeeva', email='', engg_salary=Decimal('31000'))

    def _board(self, working_days=None):
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
             mock.patch('expenses.payroll_client.is_configured', return_value=True), \
             mock.patch('expenses.payroll_client.get_salaries', return_value={'by_email': {}, 'by_name': {}}), \
             mock.patch('expenses.payroll_client.get_employees', return_value=[]), \
             mock.patch('expenses.payroll_client.get_working_days', **(
                 {'return_value': working_days} if working_days else {'side_effect': Exception('404')})):
            res = self.client.get('/api/engineer-pnl/board/', {'from': '2026-08-25', 'to': '2026-09-24', 'sync': '0'})
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    def test_working_days_from_payroll(self):
        data = self._board({'working_days': 27, 'by_email': {'praveen@x.in': 20}})
        rows = {r['engineer_name']: r for r in data['rows']}
        self.assertEqual(rows['Praveen']['total_working_days'], 27)
        self.assertEqual(rows['Praveen']['actual_working_days'], 20)
        self.assertEqual(rows['Praveen']['working_days_source'], 'payroll')
        self.assertEqual(rows['Praveen']['actual_closed_pd'], '0.10')
        # No email: keeps the configured figures.
        self.assertEqual(rows['Jeeva']['actual_working_days'], 25)
        self.assertEqual(rows['Jeeva']['working_days_source'], 'manual')

    def test_payroll_down_falls_back(self):
        data = self._board()
        self.assertFalse(data['working_days_ok'])
        self.assertTrue(all(r['working_days_source'] == 'manual' for r in data['rows']))

    def test_raw_columns_and_regions(self):
        data = self._board({'working_days': 27, 'by_email': {}})
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
