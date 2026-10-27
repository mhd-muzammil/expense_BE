"""End-to-end scenarios: expense entries ↔ bank statements, through the real API.

Each scenario puts a few statement rows and expense entries on its own day of
January 2030, then checks what the BOB / IDFC statement pages and the Expenses
list report. `run()` can also seed a local database for a look in the UI;
everything it creates is tagged so `clear()` removes it again: statement rows
with source_file=TAG, expenses with category=CATEGORY.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from expenses.models import BankStatementEntry, Expense

TAG = 'LOCAL-TEST'
CATEGORY = 'TEST SCENARIO'
BRANCH = 'TEST BRANCH'
BOB, IDFC = 'Bank of Baroda', 'IDFC Bank'


def d(n):
    return date(2030, 1, n)


def row(key, day, narration, debit=0, credit=0, bank='bob'):
    return {'key': key, 'bank': bank, 'day': day, 'narration': narration,
            'debit': Decimal(str(debit)), 'credit': Decimal(str(credit))}


def exp(key, day, amount, person='', remark='', mode=BOB, side='debit', allow_dup=False, expect_create=201):
    return {'key': key, 'day': day, 'amount': amount, 'person': person, 'remark': remark,
            'mode': mode, 'side': side, 'allow_dup': allow_dup, 'expect_create': expect_create}


# expect: {row_key: expense_key | None | ('any', {expense keys})}
# status: {expense_key: 'in_statement' | 'not_in_statement' | None}
SCENARIOS = [
    dict(name='Full bank remark copied into the entry',
         rows=[row('r', 1, 'IMPS/P2A/990000000001/XXXXXXXXXX1111/AlphaTest', 1111)],
         exps=[exp('e', 1, 1111, 'ALPHA', 'IMPS/P2A/990000000001/XXXXXXXXXX1111/AlphaTest')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Only the reference number in the remark',
         rows=[row('r', 2, 'IMPS/P2A/990000000002/XXXXXXXXXX2222/BravoTest', 2222)],
         exps=[exp('e', 2, 2222, 'Someone else', '990000000002')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Reference number inside a typed note',
         rows=[row('r', 3, 'IMPS/P2A/990000000003/XXXXXXXXXX3333/CharlieTest', 3333)],
         exps=[exp('e', 3, 3333, '', 'advance paid 990000000003 ok')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Entry dated a day later, reference still finds it',
         rows=[row('r', 4, 'IMPS/P2A/990000000004/XXXXXXXXXX4444/DeltaTest', 4444)],
         exps=[exp('e', 5, 4444, 'DELTA', '990000000004')],
         expect={'r': 'e'}, status={'e': 'in_statement'}, filtered=[(4, 4)]),
    dict(name='Right reference, wrong amount',
         rows=[row('r', 6, 'IMPS/P2A/990000000006/XXXXXXXXXX6666/EchoTest', 5000)],
         exps=[exp('e', 6, 500, 'ECHO', '990000000006')],
         expect={'r': None}, status={'e': 'not_in_statement'}),
    dict(name='Right reference, money in instead of out',
         rows=[row('r', 7, 'IMPS/P2A/990000000007/XXXXXXXXXX7777/FoxtrotTest', 700)],
         exps=[exp('e', 7, 700, 'FOXTROT', '990000000007', side='credit')],
         expect={'r': None}, status={'e': 'not_in_statement'}),
    dict(name="One person's remark copied onto another payment (October case)",
         rows=[row('p', 8, 'IMPS/P2A/990000000081/XXXXXXXXXX8269/PerumalSEPSa', 8000),
               row('q', 8, 'IMPS/P2A/990000000082/XXXXXXXXXX5084/PrasathSEPSa', 8000),
               row('v', 8, 'EBANK:WIB/990000000083/Vishnu SEP Salary Advance', 8000)],
         exps=[exp('v1', 8, 8000, 'VISHNU', 'EBANK:WIB/990000000083/Vishnu SEP Salary Advance'),
               exp('v2', 8, 8000, 'VISHNU', 'EBANK:WIB/990000000083/Vishnu SEP Salary Advance',
                   expect_create=400),
               exp('v2', 8, 8000, 'VISHNU', 'EBANK:WIB/990000000083/Vishnu SEP Salary Advance', allow_dup=True)],
         expect={'p': None, 'q': None, 'v': 'v1'}, status={'v1': 'in_statement', 'v2': 'not_in_statement'}),
    dict(name='No remark: names tell equal payments apart',
         rows=[row('p', 9, 'IMPS/P2A/990000000091/XXXXXXXXXX8269/PerumalSEPSa', 8000),
               row('q', 9, 'IMPS/P2A/990000000092/XXXXXXXXXX5084/PrasathSEPSa', 8000),
               row('v', 9, 'EBANK:WIB/990000000093/Vishnu SEP Salary Advance', 8000)],
         exps=[exp('ev', 9, 8000, 'VISHNU'), exp('eq', 9, 8000, 'Prasath'), exp('ep', 9, 8000, 'perumal')],
         expect={'p': 'ep', 'q': 'eq', 'v': 'ev'},
         status={'ep': 'in_statement', 'eq': 'in_statement', 'ev': 'in_statement'}),
    dict(name='Bank cut the name short (LingesSEPSal = Lingesh)',
         rows=[row('l', 10, 'IMPS/P2A/990000000101/XXXXXXXXXX3997/LingesSEPSal', 8000),
               row('s', 10, 'IMPS/P2A/990000000102/XXXXXXXXXX3069/SarwanSEPPet', 8000)],
         exps=[exp('es', 10, 8000, 'SARWAN'), exp('el', 10, 8000, 'LINGESH')],
         expect={'l': 'el', 's': 'es'}, status={'el': 'in_statement', 'es': 'in_statement'}),
    dict(name='Two-word name (JAYAKUMAR SALEM = JayakumarSEP)',
         rows=[row('j', 11, 'IMPS/P2A/990000000111/XXXXXXXXXX1929/JayakumarSEP', 8000),
               row('b', 11, 'IMPS/P2A/990000000112/XXXXXXXXXX7892/BashaSEPSala', 8000)],
         exps=[exp('eb', 11, 8000, 'Basha'), exp('ej', 11, 8000, 'JAYAKUMAR SALEM')],
         expect={'j': 'ej', 'b': 'eb'}, status={'ej': 'in_statement', 'eb': 'in_statement'}),
    dict(name='Similar names (Vijay Kanchi and Vijayananth)',
         rows=[row('k', 12, 'IMPS/P2A/990000000121/XXXXXXXXXX0480/VijayKanchiS', 5000),
               row('a', 12, 'IMPS/P2A/990000000122/XXXXXXXXXX1878/VijayananthS', 5000)],
         exps=[exp('ea', 12, 5000, 'VIJAYANANTH'), exp('ek', 12, 5000, 'vijay kanchi')],
         expect={'k': 'ek', 'a': 'ea'}, status={'ek': 'in_statement', 'ea': 'in_statement'}),
    dict(name='No remark, no name, only one payment that day',
         rows=[row('r', 13, 'IMPS/P2A/990000000131/XXXXXXXXXX1313/GolfTest', 777)],
         exps=[exp('e', 13, 777)],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='No remark, no name, two equal payments',
         rows=[row('x', 14, 'IMPS/P2A/990000000141/XXXXXXXXXX1414/HotelTest', 600),
               row('y', 14, 'IMPS/P2A/990000000142/XXXXXXXXXX1415/IndiaTest', 600)],
         exps=[exp('e1', 14, 600), exp('e2', 14, 600, allow_dup=True)],
         expect={'x': ('any', {'e1', 'e2'}), 'y': ('any', {'e1', 'e2'})},
         status={'e1': 'in_statement', 'e2': 'in_statement'}),
    dict(name="Second entry for a person whose bank line is already taken",
         rows=[row('v', 15, 'EBANK:WIB/990000000151/Vishnu SEP Salary Advance', 8000),
               row('p', 15, 'IMPS/P2A/990000000152/XXXXXXXXXX8269/PerumalSEPSa', 8000)],
         exps=[exp('v1', 15, 8000, 'VISHNU'), exp('v2', 15, 8000, 'VISHNU', expect_create=400),
               exp('v2', 15, 8000, 'VISHNU', allow_dup=True)],
         expect={'v': 'v1', 'p': None}, status={'v1': 'in_statement', 'v2': 'not_in_statement'}),
    dict(name='Cash payment with the same date and amount as a bank line',
         rows=[row('r', 16, 'IMPS/P2A/990000000161/XXXXXXXXXX1616/JulietTest', 450)],
         exps=[exp('e', 16, 450, 'JULIET', mode='Cash')],
         expect={'r': None}, status={'e': None}),
    dict(name='Bank mode spelled differently (BOB Current, bank of baroda)',
         rows=[row('r1', 17, 'IMPS/P2A/990000000171/XXXXXXXXXX1717/KiloTest', 171),
               row('r2', 17, 'IMPS/P2A/990000000172/XXXXXXXXXX1718/LimaTest', 172)],
         exps=[exp('e1', 17, 171, 'KILO', mode='BOB Current'), exp('e2', 17, 172, 'LIMA', mode='bank of baroda')],
         expect={'r1': 'e1', 'r2': 'e2'}, status={'e1': 'in_statement', 'e2': 'in_statement'}),
    dict(name='IDFC payment matches the IDFC statement',
         rows=[row('r', 18, 'UPI/DR/990000000181/TESTSHOP/YESB/paytmqr/UPI', 330, bank='idfc')],
         exps=[exp('e', 18, 330, 'Test shop', 'UPI/DR/990000000181/TESTSHOP/YESB/paytmqr/UPI', mode=IDFC)],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Entered under the wrong bank',
         rows=[row('r', 19, 'UPI/DR/990000000191/TESTCAFE/YESB/paytmqr/UPI', 340, bank='idfc')],
         exps=[exp('e', 19, 340, 'Test cafe', '990000000191', mode=BOB)],
         expect={'r': None}, status={'e': 'not_in_statement'}),
    dict(name='Money in by NEFT',
         rows=[row('r', 20, 'NEFT-BARBT99000000201-TEST CUSTOMER-INDIAN BANK', credit=25000)],
         exps=[exp('e', 20, 25000, 'Test customer', 'NEFT BARBT99000000201', side='credit')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='UPI line with no name, entry with no remark',
         rows=[row('r', 21, 'UPI/990000000211/13:06:46/UPI/7200000000@ybl/Veri', 120)],
         exps=[exp('e', 21, 120)],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Very short names (Ram, Sam) are not used for matching',
         rows=[row('r', 22, 'IMPS/P2A/990000000221/XXXXXXXXXX2201/RamPetrol', 300),
               row('s', 22, 'IMPS/P2A/990000000222/XXXXXXXXXX2202/SamPetrol', 300)],
         exps=[exp('es', 22, 300, 'Sam'), exp('er', 22, 300, 'Ram')],
         expect={'r': ('any', {'er', 'es'}), 's': ('any', {'er', 'es'})},
         status={'er': 'in_statement', 'es': 'in_statement'}),
    dict(name='Phone number in the remark is not taken for a reference',
         rows=[row('r', 23, 'IMPS/P2A/990000000231/XXXXXXXXXX2311/MikeTest', 220)],
         exps=[exp('e', 23, 220, '', 'call 9876543210')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Paise (250.50 vs 250.5)',
         rows=[row('r', 24, 'IMPS/P2A/990000000241/XXXXXXXXXX2411/NovemberTest', '250.50')],
         exps=[exp('e', 24, 250.5, 'NOVEMBER')],
         expect={'r': 'e'}, status={'e': 'in_statement'}),
    dict(name='Entry dated a day off, no remark',
         rows=[row('r', 25, 'IMPS/P2A/990000000251/XXXXXXXXXX2511/OscarTest', 900)],
         exps=[exp('e', 26, 900, 'OSCAR')],
         expect={'r': None}, status={'e': 'not_in_statement'}),
    dict(name='Statement filtered by date: an entry for another day stays put',
         rows=[row('a', 27, 'IMPS/P2A/990000000271/XXXXXXXXXX2711/PapaTest', 1000),
               row('b', 28, 'IMPS/P2A/990000000272/XXXXXXXXXX2712/QuebecTest', 1000)],
         exps=[exp('e', 28, 1000, '', '990000000271')],
         expect={'a': 'e', 'b': None}, status={'e': 'in_statement'}, filtered=[(28, 28), (27, 28)]),
]

# Duplicate check on create and the "Possible duplicate" badge (day 29).
DUP_BASE = dict(day=29, amount=6000, person='ZAMMIL', remark='IMPS/P2A/990000000291/XXXXXXXXXX3314/ZammilTest')
DUP_CASES = [
    ('First Zammil entry', {}, False, 201),
    ('Same entry again is stopped', {}, False, 400),
    ('Same entry again, "Yes, add anyway"', {}, True, 201),
    ('Same amount and day, different person (Vaseem)', {'person': 'VASEEM',
     'remark': 'IMPS/P2A/990000000292/XXXXXXXXXX3911/VaseemTest'}, False, 201),
    ('Same person, different bank remark', {'remark': 'IMPS/P2A/990000000293/XXXXXXXXXX3314/ZammilTest2'}, False, 201),
    ('Same entry typed in other letter case', {'person': 'zammil'}, False, 400),
]


def _payload(e):
    debit = e['side'] == 'debit'
    return {
        'date': d(e['day']).isoformat(), 'category': CATEGORY, 'branch': BRANCH,
        'debited_amount': e['amount'] if debit else None, 'credited_amount': None if debit else e['amount'],
        'debit_person': e['person'] if debit else '', 'credit_person': '' if debit else e['person'],
        'debit_remark': e['remark'] if debit else '', 'credit_remark': '' if debit else e['remark'],
        'debit_payment_mode': e['mode'] if debit else '', 'credit_payment_mode': '' if debit else e['mode'],
        **({'allow_duplicate': True} if e['allow_dup'] else {}),
    }


def clear():
    BankStatementEntry.objects.filter(source_file=TAG).delete()
    Expense.objects.filter(category=CATEGORY).delete()


def _statement(client, bank, frm=None, to=None):
    url = f'/api/{bank}-statements/'
    if frm:
        url += f'?from={frm}&to={to}'
    return {r['id']: r for r in client.get(url).data['results']}


def _expense_statuses(client):
    out, page = {}, 1
    while True:
        res = client.get(f'/api/expenses/?date_from=2030-01-01&date_to=2030-01-31&page={page}').data
        for r in res['results']:
            out[r['id']] = r.get('statement_status')
        if not res.get('next'):
            return out
        page += 1


def run(client):
    """Create every scenario through the API and return
    ``[(scenario, check, expected, actual, ok)]``."""
    results = []
    made = []  # (scenario, rows {key: id}, exps {key: id})
    for sc in SCENARIOS:
        rows = {}
        for r in sc['rows']:
            rows[r['key']] = BankStatementEntry.objects.create(
                bank=r['bank'], txn_date=d(r['day']), narration=r['narration'],
                debit=r['debit'], credit=r['credit'], source_file=TAG).id
        exps = {}
        for e in sc['exps']:
            res = client.post('/api/expenses/', _payload(e), format='json')
            ok = res.status_code == e['expect_create']
            if e['expect_create'] != 201 or not ok:
                results.append((sc['name'], f"saving {e['person'] or 'entry'}"
                                + (' (add anyway)' if e['allow_dup'] else ''),
                                e['expect_create'], res.status_code, ok))
            if res.status_code == 201:
                exps[e['key']] = res.data['id']
        made.append((sc, rows, exps))

    statements = {'bob': _statement(client, 'bob'), 'idfc': _statement(client, 'idfc')}
    statuses = _expense_statuses(client)
    name_of = {}
    for sc, rows, exps in made:
        name_of.update({v: k for k, v in exps.items()})

    def check_row(sc, rows, exps, key, want, data, label):
        got = (data.get(rows[key]) or {}).get('matched_expense')
        got_key = name_of.get(got['id']) if got else None
        if isinstance(want, tuple):
            ok = got_key in want[1]
        else:
            ok = got_key == want
        results.append((sc['name'], f'bank line {key}{label}', want if not isinstance(want, tuple) else 'one of ' + '/'.join(sorted(want[1])),
                        got_key, ok))

    for sc, rows, exps in made:
        bank_of = {r['key']: r['bank'] for r in sc['rows']}
        for key, want in sc['expect'].items():
            check_row(sc, rows, exps, key, want, statements[bank_of[key]], '')
        for frm, to in sc.get('filtered', []):
            for bank in set(bank_of.values()):
                view = _statement(client, bank, d(frm).isoformat(), d(to).isoformat())
                for key, want in sc['expect'].items():
                    if rows[key] in view:
                        check_row(sc, rows, exps, key, want, view, f' (page filtered {frm}-{to} Jan)')
        for key, want in sc['status'].items():
            got = statuses.get(exps.get(key))
            results.append((sc['name'], f'entry {key} label', want, got, got == want))

    # Duplicate check
    for label, change, allow, want in DUP_CASES:
        e = exp('dup', **{**DUP_BASE, **change}, allow_dup=allow)
        res = client.post('/api/expenses/', _payload(e), format='json')
        results.append(('Duplicate check', label, want, res.status_code, res.status_code == want))
    res = client.get('/api/expenses/?date_from=2030-01-29&date_to=2030-01-29&duplicates=1').data
    flagged = sorted((r['debit_person'], r['duplicate_count']) for r in res['results'])
    results.append(('Duplicate check', '"Possible duplicate" badge on the two identical Zammil entries only',
                    [('ZAMMIL', 2), ('ZAMMIL', 2)], flagged, flagged == [('ZAMMIL', 2), ('ZAMMIL', 2)]))
    return results


class BankScenarioTests(TestCase):
    def test_all_scenarios(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_superuser('boss', 'b@x.in', 'x'))
        failed = [r for r in run(client) if not r[4]]
        self.assertEqual(failed, [], '\n'.join(f'{s} | {c}: expected {w}, got {g}' for s, c, w, g, _ in failed))
