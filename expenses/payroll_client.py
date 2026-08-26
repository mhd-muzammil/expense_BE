"""Read-only client for the Payroll system's employee API.

Pulls each employee's salary from ``GET /api/employees/`` so the Engineer P&L
can use the real payroll salary instead of a manual figure. Credentials / URL
come from the environment (nothing hardcoded, no other config touched):

    PAYROLL_API_URL   base URL, no trailing slash (default http://localhost:8001)
    PAYROLL_USERNAME  an admin / superadmin account (sees all branches + salary)
    PAYROLL_PASSWORD

Auth is SimpleJWT: POST /api/auth/login/ {"username","password"} -> {"access"};
send ``Authorization: Bearer <access>``. Token cached in memory, re-login once
on HTTP 401.

Everything here reads except one call: ``decide_request`` posts an approve or
reject to Payroll's own endpoint. It does not write a decision locally — the
outcome, the reviewer and the timestamp are recorded by Payroll, so the two
systems can never disagree about who allowed what. Salary is read-only.
"""
import os
import threading
import time

import requests


_TIMEOUT = 15
_token = {'value': None}
_lock = threading.Lock()

# The board asks for the employee list on every load to link engineers that still
# have no email. Where a name never resolves, that is a full paginated walk of
# Payroll forever, on a request that already walks it once for salaries. Salary
# itself is never served from here — get_salaries stays uncached — so a stale entry
# can only delay a NEW employee becoming pickable, never pay anyone a stale figure.
_EMPLOYEE_TTL = 300  # seconds
_employee_cache = {'at': None, 'rows': None}


class PayrollError(Exception):
    """Raised when Payroll is unreachable, unauthenticated or misconfigured."""


def _api_url():
    return os.environ.get('PAYROLL_API_URL', 'http://localhost:8001').rstrip('/')


def _user():
    return os.environ.get('PAYROLL_USERNAME', '')


def _password():
    return os.environ.get('PAYROLL_PASSWORD', '')


def is_configured():
    return bool(_user() and _password())


def _login():
    resp = requests.post(
        f"{_api_url()}/api/auth/login/",
        json={'username': _user(), 'password': _password()},
        headers={'Content-Type': 'application/json'},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise PayrollError(f"Payroll login failed (HTTP {resp.status_code}).")
    token = (resp.json() or {}).get('access')
    if not token:
        raise PayrollError("Payroll login returned no access token.")
    _token['value'] = token
    return token


def _get(path):
    """Authenticated GET with a single re-login retry on 401."""
    with _lock:
        token = _token['value'] or _login()
    url = f"{_api_url()}{path}"
    resp = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=_TIMEOUT)
    if resp.status_code == 401:
        with _lock:
            token = _login()
        resp = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise PayrollError(f"Payroll GET {path} failed (HTTP {resp.status_code}).")
    return resp.json()


def _post(path, payload=None):
    """Authenticated POST with a single re-login retry on 401.

    Raises PayrollError carrying Payroll's own message on a 4xx, so a refusal it
    makes deliberately — "this request was already approved" — reaches the caller
    as that sentence rather than a generic failure.
    """
    with _lock:
        token = _token['value'] or _login()
    url = f"{_api_url()}{path}"
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    resp = requests.post(url, json=payload or {}, headers=headers, timeout=_TIMEOUT)
    if resp.status_code == 401:
        with _lock:
            token = _login()
        headers['Authorization'] = f'Bearer {token}'
        resp = requests.post(url, json=payload or {}, headers=headers, timeout=_TIMEOUT)
    if resp.status_code not in (200, 201):
        detail = ''
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = str(body.get('detail') or '')
        except Exception:
            pass
        raise PayrollError(detail or f"Payroll POST {path} failed (HTTP {resp.status_code}).")
    return resp.json()


def decide_request(request_id, decision, note=''):
    """Approve or reject one employee request, in Payroll.

    The decision is made BY PAYROLL: it sets the status, stamps the reviewer and
    the time, and drops the outcome into the request's own conversation so the
    employee sees it. Nothing about the decision is stored on this side, which is
    what keeps the two systems from telling different stories.

    Payroll refuses a request that is not still Pending, so two people deciding
    the same one cannot both succeed — the second gets Payroll's own refusal.

    ``note`` should say who decided it here: Payroll records the reviewer as the
    service account this client logs in with, so without it the trail would stop
    at a shared login instead of a person.
    """
    if decision not in ('approve', 'reject'):
        raise PayrollError("Decision must be 'approve' or 'reject'.")
    if not is_configured():
        raise PayrollError(
            "Payroll credentials are not set. Configure PAYROLL_USERNAME and "
            "PAYROLL_PASSWORD (an admin/superadmin account)."
        )
    try:
        rid = int(request_id)
    except (TypeError, ValueError):
        raise PayrollError("Invalid request id.")
    return _post(f"/api/requests/{rid}/{decision}/", {'note': (note or '').strip()})


def get_salaries():
    """Return salary lookups from Payroll: ``{'by_email': {email_lower: salary},
    'by_name': {name_lower: salary}}`` (salary as float). Follows DRF pagination.
    Raises PayrollError on failure."""
    if not is_configured():
        raise PayrollError(
            "Payroll credentials are not set. Configure PAYROLL_USERNAME and "
            "PAYROLL_PASSWORD (an admin/superadmin account)."
        )
    by_email, by_name = {}, {}
    path = "/api/employees/?page_size=1000"
    seen_pages = 0
    while path and seen_pages < 50:
        payload = _get(path)
        seen_pages += 1
        if isinstance(payload, dict) and 'results' in payload:
            rows = payload.get('results') or []
            nxt = payload.get('next')
        else:
            rows = payload if isinstance(payload, list) else []
            nxt = None
        for e in rows:
            if not isinstance(e, dict):
                continue
            sal = e.get('salary')
            if sal in (None, ''):
                continue
            try:
                sal = float(sal)
            except (TypeError, ValueError):
                continue
            name = str(e.get('employee_name') or '').strip().lower()
            email = str(e.get('email') or '').strip().lower()
            if name:
                by_name[name] = sal
            if email:
                by_email[email] = sal
        # Follow absolute `next` links by stripping the base URL to a path.
        if nxt:
            base = _api_url()
            path = nxt[len(base):] if nxt.startswith(base) else None
        else:
            path = None
    return {'by_email': by_email, 'by_name': by_name}


def get_employees(fresh=False):
    """Every Payroll employee as ``{'name', 'email', 'salary'}``, name and email
    kept together. Cached for a few minutes; pass ``fresh=True`` to bypass that.

    ``get_salaries`` deliberately returns lookups keyed for matching and so cannot
    say which email belongs to which person. This reader exists so the Engineer P&L
    edit form can offer the actual Payroll people to pick from — an engineer's email
    is what salary matches on, and typing it by hand is where it goes wrong.

    Employees with no email are still listed (so the absence is visible, rather than
    the person appearing to be missing from Payroll). Follows DRF pagination; raises
    PayrollError on failure.
    """
    if not is_configured():
        raise PayrollError(
            "Payroll credentials are not set. Configure PAYROLL_USERNAME and "
            "PAYROLL_PASSWORD (an admin/superadmin account)."
        )
    if not fresh:
        cached = _employee_cache['rows']
        at = _employee_cache['at']
        if cached is not None and at is not None and (time.monotonic() - at) < _EMPLOYEE_TTL:
            return list(cached)
    out = []
    seen_emails = set()
    path = "/api/employees/?page_size=1000"
    seen_pages = 0
    while path and seen_pages < 50:
        payload = _get(path)
        seen_pages += 1
        if isinstance(payload, dict) and 'results' in payload:
            rows = payload.get('results') or []
            nxt = payload.get('next')
        else:
            rows = payload if isinstance(payload, list) else []
            nxt = None
        for e in rows:
            if not isinstance(e, dict):
                continue
            name = str(e.get('employee_name') or '').strip()
            email = str(e.get('email') or '').strip()
            if not name and not email:
                continue
            key = email.lower()
            if key and key in seen_emails:
                continue
            if key:
                seen_emails.add(key)
            sal = e.get('salary')
            try:
                sal = float(sal) if sal not in (None, '') else None
            except (TypeError, ValueError):
                sal = None
            out.append({'name': name, 'email': email, 'salary': sal})
        if nxt:
            base = _api_url()
            path = nxt[len(base):] if nxt.startswith(base) else None
        else:
            path = None
    out.sort(key=lambda r: (r['name'] or '').lower())
    _employee_cache['rows'] = out
    _employee_cache['at'] = time.monotonic()
    return list(out)


def get_employee_requests(status=None, request_type=None):
    """What employees have asked the office for, from Payroll's request section.

    Returns a list of dicts: name, branch, type (and its label), amount, reason,
    status, who reviewed it and when, and when it was raised.

    Read-only, like everything else here. Approving or rejecting stays in Payroll,
    where the decision is recorded against the reviewer — a second place to press
    approve would leave two systems disagreeing about who allowed what.

    Follows DRF pagination; raises PayrollError on failure.
    """
    if not is_configured():
        raise PayrollError(
            "Payroll credentials are not set. Configure PAYROLL_USERNAME and "
            "PAYROLL_PASSWORD (an admin/superadmin account)."
        )
    qs = ["page_size=500"]
    if status:
        qs.append(f"status={status}")
    if request_type:
        qs.append(f"request_type={request_type}")
    path = "/api/requests/?" + "&".join(qs)

    out = []
    seen_pages = 0
    while path and seen_pages < 50:
        payload = _get(path)
        seen_pages += 1
        if isinstance(payload, dict) and 'results' in payload:
            rows = payload.get('results') or []
            nxt = payload.get('next')
        else:
            rows = payload if isinstance(payload, list) else []
            nxt = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            amt = r.get('amount')
            try:
                amt = float(amt) if amt not in (None, '') else None
            except (TypeError, ValueError):
                amt = None
            out.append({
                'id': r.get('id'),
                'employee_name': str(r.get('employee_name') or '').strip(),
                'branch': str(r.get('branch') or '').strip(),
                'request_type': str(r.get('request_type') or '').strip(),
                'request_type_label': str(r.get('request_type_label') or r.get('request_type') or '').strip(),
                # None for a report: it is raised to be read, not paid.
                'amount': amt,
                'reason': str(r.get('reason') or '').strip(),
                'status': str(r.get('status') or '').strip(),
                'reviewed_by': str(r.get('reviewed_by_name') or '').strip(),
                'reviewed_at': r.get('reviewed_at') or '',
                'created_at': r.get('created_at') or '',
                # The conversation itself stays in Payroll; only its size is useful here.
                'message_count': len(r.get('messages') or []),
            })
        if nxt:
            base = _api_url()
            path = nxt[len(base):] if nxt.startswith(base) else None
        else:
            path = None
    return out
