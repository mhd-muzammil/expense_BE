"""Bank statement ↔ Expenses: rows pair by transaction reference before date + amount."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from expenses.models import BankStatementEntry, Branch, Expense
from expenses.views import _bank_match_map, _bank_refs

DAY = date(2026, 10, 12)
PERUMAL = 'IMPS/P2A/628500979175/XXXXXXXXXX8269/PerumalSEPSa'
PRASATH = 'IMPS/P2A/628500733605/XXXXXXXXXX5084/PrasathSEPSa'
VISHNU = 'EBANK:WIB/1529973425/Vishnu SEP Salary Advance'


class BankMatchTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(location='VELLORE')
        self.rows = [
            BankStatementEntry.objects.create(bank='bob', txn_date=DAY, narration=n, debit=Decimal('8000'))
            for n in (PERUMAL, PRASATH, VISHNU)
        ]

    def expense(self, remark, person, day=DAY):
        return Expense.objects.create(
            date=day, category='SALARY ADVANCE', branch=self.branch,
            debited_amount=Decimal('8000'), debit_remark=remark,
            debit_person=person, debit_payment_mode='Bank of Baroda',
        )

    def matched(self):
        mm, _ = _bank_match_map('bob', self.rows)
        return {be_id: d['id'] for be_id, d in mm.items()}

    def test_refs_are_read_from_narrations(self):
        self.assertEqual(_bank_refs(PRASATH), {'628500733605'})
        self.assertEqual(_bank_refs(VISHNU), {'1529973425'})
        self.assertEqual(_bank_refs('NEFT-BARBT26118913199-HARI PANDURANGAN-INDIAN BANK'), {'BARBT26118913199'})
        self.assertEqual(_bank_refs('UPI/657696265785/13:06:46/UPI/7200407008@ybl'), {'657696265785'})
        self.assertEqual(_bank_refs('Petrol advance'), set())

    def test_each_payment_pairs_with_its_own_row(self):
        vishnu = self.expense(VISHNU, 'Vishnu')
        prasath = self.expense(PRASATH, 'Prasath')
        perumal = self.expense(PERUMAL, 'Perumal')
        self.assertEqual(self.matched(), {
            self.rows[0].id: perumal.id, self.rows[1].id: prasath.id, self.rows[2].id: vishnu.id,
        })

    def test_entering_vishnu_marks_vishnus_row_not_the_first_8000(self):
        vishnu = self.expense(VISHNU, 'Vishnu')
        self.assertEqual(self.matched(), {self.rows[2].id: vishnu.id})

    def test_a_copied_remark_does_not_cover_someone_elses_payment(self):
        first = self.expense(VISHNU, 'Vishnu')
        self.expense(VISHNU, 'VISHNU')
        self.expense(VISHNU, 'VISHNU')
        # Perumal's and Prasath's rows stay missing; the copies stay unmatched.
        self.assertEqual(self.matched(), {self.rows[2].id: first.id})

    def test_reference_matches_even_when_the_date_differs(self):
        vishnu = self.expense(VISHNU, 'Vishnu', day=date(2026, 10, 13))
        self.assertEqual(self.matched(), {self.rows[2].id: vishnu.id})

    def test_reference_needs_the_same_amount(self):
        e = self.expense(VISHNU, 'Vishnu')
        e.debited_amount = Decimal('800')
        e.save()
        self.assertEqual(self.matched(), {})

    def test_entries_without_a_reference_still_match_by_date_and_amount(self):
        a = self.expense('salary advance', 'Perumal')
        b = self.expense('', 'Prasath')
        self.assertEqual(self.matched(), {self.rows[0].id: a.id, self.rows[1].id: b.id})
