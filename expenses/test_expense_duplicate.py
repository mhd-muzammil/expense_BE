"""Expenses: an entry identical to an existing one needs explicit confirmation."""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from datetime import date
from decimal import Decimal

from expenses.models import BankStatementEntry, Expense

ENTRY = {
    'date': '2026-10-12', 'category': 'Salary advance', 'branch': 'KANCHIPURAM',
    'credited_amount': None, 'credit_remark': '', 'credit_person': '', 'credit_payment_mode': '',
    'debited_amount': 5000, 'debit_remark': 'IMPS/P2A/628', 'debit_person': 'VIJAY KANCHI',
    'debit_payment_mode': 'Bank of Baroda',
}


class ExpenseDuplicateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser('boss', 'b@x.in', 'x'))

    def test_identical_entry_is_refused_until_confirmed(self):
        self.assertEqual(self.client.post('/api/expenses/', ENTRY, format='json').status_code, 201)

        res = self.client.post('/api/expenses/', ENTRY, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('VIJAY KANCHI', res.data['duplicate'][0])
        self.assertEqual(Expense.objects.count(), 1)

        res = self.client.post('/api/expenses/', {**ENTRY, 'allow_duplicate': True}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Expense.objects.count(), 2)

    def test_different_amount_or_edit_is_not_a_duplicate(self):
        first = self.client.post('/api/expenses/', ENTRY, format='json').data
        res = self.client.post('/api/expenses/', {**ENTRY, 'debited_amount': 5001}, format='json')
        self.assertEqual(res.status_code, 201)

        res = self.client.put(f"/api/expenses/{first['id']}/", ENTRY, format='json')
        self.assertEqual(res.status_code, 200)

    def test_list_flags_existing_duplicates(self):
        self.client.post('/api/expenses/', ENTRY, format='json')
        self.client.post('/api/expenses/', {**ENTRY, 'allow_duplicate': True}, format='json')
        # Same transaction typed with different casing still counts.
        self.client.post('/api/expenses/', {**ENTRY, 'category': 'SALARY ADVANCE', 'allow_duplicate': True}, format='json')
        self.client.post('/api/expenses/', {**ENTRY, 'debited_amount': 150}, format='json')

        res = self.client.get('/api/expenses/')
        self.assertEqual(res.data['duplicate_total'], 3)
        counts = sorted(r['duplicate_count'] or 0 for r in res.data['results'])
        self.assertEqual(counts, [0, 3, 3, 3])

        res = self.client.get('/api/expenses/?duplicates=1')
        self.assertEqual(len(res.data['results']), 3)
        self.assertEqual(res.data['duplicate_total'], 3)

    def test_equal_amounts_to_different_people_are_not_duplicates(self):
        # Zammil and Vaseem: same day, branch and Rs 6,000 salary advance.
        zammil = {**ENTRY, 'debited_amount': 6000, 'debit_person': 'ZAMMIL',
                  'debit_remark': 'IMPS/P2A/628518762818/XXXXXXXXXX3314/ZammilSEPSal'}
        vaseem = {**zammil, 'debit_person': 'VASEEM',
                  'debit_remark': 'IMPS/P2A/628518967430/XXXXXXXXXX3911/VaseemSEPSal'}
        self.assertEqual(self.client.post('/api/expenses/', zammil, format='json').status_code, 201)
        self.assertEqual(self.client.post('/api/expenses/', vaseem, format='json').status_code, 201)

        res = self.client.get('/api/expenses/')
        self.assertEqual(res.data['duplicate_total'], 0)

        # The same bank remark entered again is still caught.
        self.assertEqual(self.client.post('/api/expenses/', zammil, format='json').status_code, 400)

    def test_twins_each_matched_to_own_bank_line_are_not_flagged(self):
        # Two customers paid Rs 590 the same day; the Excel import left both
        # entries without a person or remark.
        receipt = {**ENTRY, 'category': 'TRADE', 'debited_amount': None, 'debit_remark': '',
                   'debit_person': '', 'debit_payment_mode': '', 'credited_amount': 590,
                   'credit_payment_mode': 'Bank of Baroda', 'allow_duplicate': True}
        for _ in range(3):
            self.client.post('/api/expenses/', receipt, format='json')
        for n in ('UPI/787277107025/12:19:40/UPI/a@ybl', 'UPI/125728627025/09:29:49/UPI/b@okhdfc'):
            BankStatementEntry.objects.create(bank='bob', txn_date=date(2026, 10, 12), narration=n,
                                              credit=Decimal('590'))

        # Three entries, two bank lines: the unmatched third keeps the group flagged.
        self.assertEqual(self.client.get('/api/expenses/').data['duplicate_total'], 3)

        Expense.objects.order_by('-id').first().delete()
        self.assertEqual(self.client.get('/api/expenses/').data['duplicate_total'], 0)
