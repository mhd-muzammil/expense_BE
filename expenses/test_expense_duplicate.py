"""Expenses: an entry identical to an existing one needs explicit confirmation."""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.models import Expense

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
