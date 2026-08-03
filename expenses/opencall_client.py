"""Read-only client for the OpenCall system's live productivity API.

Pulls the closed-call count per engineer for a date range from
``GET /api/v1/engineer-target/``. Credentials / URL come from the environment so
nothing is hardcoded and no other app config is touched:

    OPENCALL_API_URL   base URL, no trailing slash (default http://localhost:4000)
    OPENCALL_USERNAME  a SUPER_ADMIN service account (returns all regions)
    OPENCALL_PASSWORD

Mirrors the OpenCall repo's own FieldEZ worker: JWT bearer, token cached in
memory (~8h TTL), re-login once on HTTP 401. This module never writes to
OpenCall — it only reads the already-computed closed-call counts.
"""
import os
import threading

import requests


_TIMEOUT = 15
_token = {'value': None}
_lock = threading.Lock()


class OpenCallError(Exception):
    """Raised when OpenCall is unreachable, unauthenticated or misconfigured."""


def _api_url():
    return os.environ.get('OPENCALL_API_URL', 'http://localhost:4000').rstrip('/')


def _user():
    return os.environ.get('OPENCALL_USERNAME', '')


def _password():
    return os.environ.get('OPENCALL_PASSWORD', '')


def is_configured():
    """True when a service account is set (so a live pull can be attempted)."""
    return bool(_user() and _password())


def _login():
    resp = requests.post(
        f"{_api_url()}/api/v1/auth/login",
        json={'username': _user(), 'password': _password()},
        headers={'Content-Type': 'application/json'},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise OpenCallError(f"OpenCall login failed (HTTP {resp.status_code}).")
    token = ((resp.json() or {}).get('data') or {}).get('token')
    if not token:
        raise OpenCallError("OpenCall login returned no token.")
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
        raise OpenCallError(f"OpenCall GET {path} failed (HTTP {resp.status_code}).")
    return resp.json()


def get_engineers():
    """The engineer roster from OpenCall as a list of
    ``{'name', 'email', 'region', 'active'}`` dicts. Used to auto-populate the
    Engineer P&L board so engineers appear without manual entry."""
    if not is_configured():
        raise OpenCallError(
            "OpenCall credentials are not set. Configure OPENCALL_USERNAME and "
            "OPENCALL_PASSWORD (a SUPER_ADMIN service account)."
        )
    payload = _get("/api/v1/admin/engineers?limit=500") or {}
    data = payload.get('data', payload)
    items = (data.get('items') or data.get('engineers') or data.get('rows')
             or (data if isinstance(data, list) else []))
    out = []
    for e in items:
        if not isinstance(e, dict):
            continue
        name = str(e.get('engineerName') or e.get('name') or '').strip()
        if not name:
            continue
        out.append({
            'name': name,
            'email': (e.get('email') or '') or '',
            'region': e.get('regionCode') or '',
            'active': bool(e.get('isActive', True)),
        })
    return out


def get_closed_counts(date_from, date_to):
    """Closed-call counts per engineer for the [date_from, date_to] range.

    Returns ``{'counts': {name_lower: total_closed}, 'display': {name_lower: Name},
    'meta': {...}}`` — periodClosed is summed across regionCodes so an engineer
    working under two ASP codes rolls up to one per-person total. Dates are
    ISO strings (YYYY-MM-DD). Raises OpenCallError on any failure."""
    if not is_configured():
        raise OpenCallError(
            "OpenCall credentials are not set. Configure OPENCALL_USERNAME and "
            "OPENCALL_PASSWORD (a SUPER_ADMIN service account)."
        )
    payload = (_get(f"/api/v1/engineer-target/?from={date_from}&to={date_to}") or {})
    data = payload.get('data') or {}
    counts, display = {}, {}
    for row in data.get('rows', []):
        name = str(row.get('engineer', '') or '').strip()
        if not name:
            continue
        key = name.lower()
        counts[key] = counts.get(key, 0) + int(row.get('periodClosed', 0) or 0)
        display.setdefault(key, name)
    return {
        'counts': counts,
        'display': display,
        'meta': {
            'fromDate': data.get('fromDate'),
            'toDate': data.get('toDate'),
            'reportDays': data.get('reportDays'),
            'workingDaysPerMonth': data.get('workingDaysPerMonth'),
            'monthlyTarget': data.get('monthlyTarget'),
        },
    }
