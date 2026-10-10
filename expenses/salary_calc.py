"""What an engineer's salary costs for a window, counted the way Payroll pays it.

Payroll's payslip rules, mirrored here so the P&L charges the salary Payroll
actually pays:

* A salary cycle runs the 25th to the 24th; one day is worth salary / days in
  that cycle (28-31). A window crossing two cycles is charged each part at its
  own cycle's rate.
* Every day is paid -- Sundays and Leave days included -- except days marked
  Absent, less one day of casual leave a cycle after six months' service.
* A finished cycle with a Generated or Paid payslip is charged what the slip
  pays (earned salary + casual leave + special work), not an estimate.
* Only days up to today are charged: the calls side of the P&L only has calls
  up to today, and the two halves must cover the same days.

Engineers not linked to Payroll have no attendance, so every day in the window
is charged at their configured salary.
"""
from datetime import date, timedelta
from decimal import Decimal

TWO = Decimal('0.01')


def cycle_bounds(d):
    """The 25th-24th salary cycle containing ``d``, as (start, end)."""
    if d.day >= 25:
        start = d.replace(day=25)
        end = (start.replace(day=1) + timedelta(days=32)).replace(day=24)
    else:
        start = (d.replace(day=1) - timedelta(days=1)).replace(day=25)
        end = d.replace(day=24)
    return start, end


def segments(first, last):
    """Split [first, last] at cycle boundaries: [(seg_start, seg_end, cyc_start, cyc_end)]."""
    out = []
    d = first
    while d <= last:
        cs, ce = cycle_bounds(d)
        seg_end = min(ce, last)
        out.append((d, seg_end, cs, ce))
        d = seg_end + timedelta(days=1)
    return out


def working_days(a, b):
    """Days in [a, b] that are not Sundays."""
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() != 6)


def charged_window(first, last, today=None):
    """The part of the window that is charged: up to today, never the future."""
    today = today or date.today()
    end = min(last, today)
    return (first, end) if first <= end else (None, None)


def engineer_salary(monthly, segs, email=None):
    """Salary for one engineer over the charged window.

    ``segs`` is a list of dicts, one per cycle segment, each with ``start``,
    ``end``, ``cycle_start``, ``cycle_end`` and ``rows`` (Payroll's
    working_days rows by lower-case email, or None when Payroll was not read).
    """
    monthly = Decimal(str(monthly or 0))
    total = Decimal('0')
    days = paid = lop = absent = casual = present = unmarked = wd = 0
    basis = set()
    for seg in segs:
        seg_days = (seg['end'] - seg['start']).days + 1
        cyc_days = (seg['cycle_end'] - seg['cycle_start']).days + 1
        days += seg_days
        wd += working_days(seg['start'], seg['end'])
        row = (seg.get('rows') or {}).get(email) if email else None
        if row is None:
            # Not linked to Payroll (or Payroll unreadable): every day is charged.
            total += monthly * seg_days / cyc_days
            paid += seg_days
            basis.add('manual')
            continue
        present += int(row.get('present_days') or 0)
        unmarked += int(row.get('unmarked_days') or 0)
        slip = row.get('payslip')
        if slip:
            # The cycle's own payslip is the final word on what it cost.
            total += Decimal(str(slip.get('earned') or 0))
            # The slip deducts its LOP days and pays casual leave back as its
            # own line, so the days actually unpaid are LOP less casual leave.
            slip_cl = Decimal(str(slip.get('casual_leave_used') or 0))
            slip_lop = max(Decimal(str(slip.get('lop_days') or 0)) - slip_cl, Decimal(0))
            slip_lop = min(int(slip_lop.to_integral_value()), seg_days)
            paid += seg_days - slip_lop
            lop += slip_lop
            casual += int(slip_cl)
            absent += int(row.get('absent_days') or 0)
            basis.add('payslip')
            continue
        seg_lop = min(int(row.get('lop_days') or 0), seg_days)
        total += monthly * (seg_days - seg_lop) / cyc_days
        paid += seg_days - seg_lop
        lop += seg_lop
        absent += int(row.get('absent_days') or 0)
        casual += int(row.get('casual_leave_days') or 0)
        basis.add('attendance')
    return {
        'earned': total.quantize(TWO),
        'charged_days': days,
        'paid_days': paid,
        'lop_days': lop,
        'absent_marked': absent,
        'casual_leave_days': casual,
        'present_days': present,
        'unmarked_days': unmarked,
        'working_days': wd,
        # payslip / attendance / manual, or "mixed" when a window spans both kinds.
        'basis': basis.pop() if len(basis) == 1 else ('mixed' if basis else 'none'),
    }
