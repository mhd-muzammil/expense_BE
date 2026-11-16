"""Personal / Office sorting of bank rows, and what it does to the two P&Ls.

A bank row marked Personal must take the Expenses entry it reconciles to out of
the company P&L, and show up in the Personal P&L instead. Rows start unsorted.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.models import BankStatementEntry, UserProfile
from expenses.test_bank_scenarios import IDFC, TAG, _payload, d, exp


class PersonalPnlTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser('boss', 'b@x.in', 'x'))
        # Two IDFC payments in January 2030 (FY 2029-30), each with its own
        # Expenses entry, plus one money-in row with no entry.
        self.shop = BankStatementEntry.objects.create(
            bank='idfc', txn_date=d(10), narration='UPI/990000000101/Shop/UPI',
            debit=Decimal('1500'), source_file=TAG)
        self.rent = BankStatementEntry.objects.create(
            bank='idfc', txn_date=d(11), narration='IMPS/P2A/990000000102/Office Rent',
            debit=Decimal('20000'), source_file=TAG)
        self.refund = BankStatementEntry.objects.create(
            bank='idfc', txn_date=d(12), narration='UPI/990000000103/Refund',
            credit=Decimal('300'), source_file=TAG)
        for e in (exp('s', 10, 1500, 'SHOP', '990000000101', mode=IDFC),
                  exp('r', 11, 20000, 'LANDLORD', '990000000102', mode=IDFC)):
            res = self.client.post('/api/expenses/', _payload(e), format='json')
            self.assertEqual(res.status_code, 201, res.data)

    def _sort(self, row, kind):
        return self.client.patch(f'/api/idfc-statements/{row.id}/', {'expense_type': kind}, format='json')

    def _company_expense(self):
        return Decimal(self.client.get('/api/profit-loss/?fy=2029').data['total_expense'])

    def test_rows_start_unsorted_and_can_be_moved_back_and_forth(self):
        rows = {r['id']: r for r in self.client.get('/api/idfc-statements/').data['results']}
        self.assertEqual({r['expense_type'] for r in rows.values()}, {''})

        self.assertEqual(self._sort(self.shop, 'personal').status_code, 200)
        self.assertEqual(self._sort(self.rent, 'office').status_code, 200)
        rows = {r['id']: r for r in self.client.get('/api/idfc-statements/').data['results']}
        self.assertEqual(rows[self.shop.id]['expense_type'], 'personal')
        self.assertEqual(rows[self.rent.id]['expense_type'], 'office')
        self.assertEqual(rows[self.refund.id]['expense_type'], '')

        self.assertEqual(self._sort(self.shop, 'office').status_code, 200)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.expense_type, 'office')
        # Sorting must not touch the row's money or the reconciliation.
        self.assertEqual(self.shop.debit, Decimal('1500'))
        self.assertEqual(rows[self.shop.id]['expense_status'], 'matched')

    def test_unknown_type_is_refused(self):
        self.assertEqual(self._sort(self.shop, 'family').status_code, 400)

    def test_personal_row_leaves_the_company_pnl(self):
        self.assertEqual(self._company_expense(), Decimal('21500'))
        self._sort(self.shop, 'personal')
        self.assertEqual(self._company_expense(), Decimal('20000'))
        self._sort(self.rent, 'office')  # Office changes nothing
        self.assertEqual(self._company_expense(), Decimal('20000'))
        self._sort(self.shop, '')        # back to unsorted -> counted again
        self.assertEqual(self._company_expense(), Decimal('21500'))

    def test_personal_pnl_is_month_wise_from_personal_rows(self):
        self._sort(self.shop, 'personal')
        self._sort(self.refund, 'personal')
        self._sort(self.rent, 'office')
        data = self.client.get('/api/personal-pnl/').data
        self.assertEqual(data['fy_start'], 2029)
        self.assertEqual(data['fy_label'], 'FY 2029-30')
        self.assertEqual(len(data['months']), 12)
        jan = next(m for m in data['months'] if m['month'] == '2030-01')
        self.assertEqual((Decimal(jan['money_out']), Decimal(jan['money_in']), Decimal(jan['net'])),
                         (Decimal('1500'), Decimal('300'), Decimal('-1200')))
        self.assertEqual([t['id'] for t in jan['transactions']], [self.shop.id, self.refund.id])
        self.assertEqual((Decimal(data['total_out']), Decimal(data['total_in']), Decimal(data['net'])),
                         (Decimal('1500'), Decimal('300'), Decimal('-1200')))
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['unsorted_count'], 0)
        others = [m for m in data['months'] if m['month'] != '2030-01']
        self.assertTrue(all(m['count'] == 0 and Decimal(m['money_out']) == 0 for m in others))

    def test_personal_pnl_goes_with_the_idfc_statement(self):
        def user_with(sections, name):
            u = User.objects.create_user(name, password='x')
            UserProfile.objects.create(user=u, allowed_sections=sections)
            c = APIClient()
            c.force_authenticate(u)
            return c
        self.assertEqual(user_with(['pnl', 'expenses'], 'acct').get('/api/personal-pnl/').status_code, 403)
        self.assertEqual(user_with(['idfc'], 'owner').get('/api/personal-pnl/').status_code, 200)
