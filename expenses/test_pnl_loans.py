"""The P&L with and without loans.

A loan received is not income and a loan repaid is not an expense, so the
"without loans" P&L leaves loan categories out of income, expense and the net,
and reports them in a separate loan summary instead.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.views import _pnl_canonical, _pnl_is_loan


class LoanCategoryTests(TestCase):
    def test_which_categories_are_loans(self):
        for name in ('LOAN', 'Loan Return', 'GST LOAN RETURN', 'laon', 'EMI', 'bank loan', 'Loans'):
            self.assertTrue(_pnl_is_loan(_pnl_canonical(name)), name)
        for name in ('LAPTOP EMI', 'SALARY', 'SALARY ADVANCE', 'TRADE', 'SERVICE', 'GST', 'PETROL ADVANCE'):
            self.assertFalse(_pnl_is_loan(_pnl_canonical(name)), name)


class PnlLoanTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser('boss', 'b@x.in', 'x'))
        # October 2030 = FY 2030-31.
        for day, category, credit, debit in [
            (1, 'SERVICE', 100000, 0),     # real income
            (2, 'LOAN', 500000, 0),        # loan received
            (3, 'SALARY', 0, 40000),       # real expense
            (4, 'LOAN RETURN', 0, 200000), # loan repaid
            (5, 'EMI', 0, 10000),          # loan instalment
            (6, 'LAPTOP EMI', 0, 5000),    # laptop bought in instalments: a real cost
        ]:
            res = self.client.post('/api/expenses/', {
                'date': f'2030-10-{day:02d}', 'branch': 'TEST BRANCH', 'category': category,
                'credited_amount': credit or None, 'debited_amount': debit or None,
                'credit_payment_mode': 'Cash' if credit else '', 'debit_payment_mode': 'Cash' if debit else '',
                'credit_person': 'P' if credit else '', 'debit_person': 'P' if debit else '',
            }, format='json')
            self.assertEqual(res.status_code, 201, res.data)

    def pnl(self, extra=''):
        return self.client.get(f'/api/profit-loss/?fy=2030{extra}').data

    def test_with_loans_is_unchanged(self):
        d = self.pnl()
        self.assertFalse(d['loans_excluded'])
        self.assertEqual(Decimal(d['total_income']), Decimal('600000'))
        self.assertEqual(Decimal(d['total_expense']), Decimal('255000'))
        self.assertEqual(Decimal(d['net_profit']), Decimal('345000'))

    def test_without_loans_leaves_them_out(self):
        d = self.pnl('&exclude_loans=1')
        self.assertTrue(d['loans_excluded'])
        self.assertEqual(Decimal(d['total_income']), Decimal('100000'))
        self.assertEqual(Decimal(d['total_expense']), Decimal('45000'))
        self.assertEqual(Decimal(d['net_profit']), Decimal('55000'))
        names = {r['category'].upper() for r in d['income'] + d['expense']}
        self.assertNotIn('LOAN', names)
        self.assertNotIn('LOAN RETURN', names)
        self.assertNotIn('EMI', names)
        self.assertIn('LAPTOP EMI', names)
        self.assertEqual(Decimal(d['net_by_month']['2030-10']), Decimal('55000'))

    def test_loan_summary(self):
        for extra in ('', '&exclude_loans=1'):
            loans = self.pnl(extra)['loans']
            self.assertEqual(Decimal(loans['total_received']), Decimal('500000'))
            self.assertEqual(Decimal(loans['total_repaid']), Decimal('210000'))
            self.assertEqual(Decimal(loans['net']), Decimal('290000'))
            self.assertEqual(Decimal(loans['received_by_month']['2030-10']), Decimal('500000'))
            self.assertEqual(Decimal(loans['repaid_by_month']['2030-10']), Decimal('210000'))
            self.assertEqual(Decimal(loans['net_by_month']['2030-11']), Decimal('0'))
            self.assertEqual({c['category'].upper() for c in loans['categories']}, {'LOAN', 'LOAN RETURN', 'EMI'})
