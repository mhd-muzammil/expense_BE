import csv
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import authenticate
from django.db.models import Sum, Q, F, Window
from django.db.models.functions import Coalesce, TruncMonth
from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.permissions import AllowAny, IsAuthenticated, BasePermission
from rest_framework.response import Response

from rest_framework.pagination import PageNumberPagination

from .models import (
    Branch, Expense, PaymentModeBalance, BillingReminder, PettyCashDebit, Invoice, AppSetting,
    UserProfile, ALL_SECTIONS, SECTION_DASHBOARD, SECTION_EXPENSES, SECTION_PNL, SECTION_REGION, SECTION_INVOICE,
)
from .serializers import BranchSerializer, ExpenseSerializer, ExpenseCreateSerializer, PaymentModeBalanceSerializer, BillingReminderSerializer, PettyCashDebitSerializer, InvoiceSerializer


# ---------------------------------------------------------------------------
# Section-based access control
# ---------------------------------------------------------------------------
# Each non-admin user has a UserProfile.allowed_sections list controlling which
# app pages they may reach. Access is enforced server-side so it can't be
# bypassed by calling the API directly. Admins (is_staff / is_superuser) get
# every section implicitly. Endpoints declare which section they belong to via
# RequireSection(...).

# Legacy group used before per-user sections existed. Users still in it (with no
# profile) are treated as Dashboard + P&L, preserving old behaviour.
PNL_ONLY_GROUP = 'pnl_only'


def is_admin_user(user):
    """Admins see everything and can manage other users."""
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def get_allowed_sections(user):
    """The list of section keys this user may access (canonical order)."""
    if not user or not user.is_authenticated:
        return []
    if is_admin_user(user):
        return list(ALL_SECTIONS)
    # Per-user profile is the source of truth.
    profile = getattr(user, 'profile', None)
    if profile is not None:
        sections = profile.clean_sections()
        if sections:
            return sections
        # An empty profile list means "no restriction configured yet" only if
        # they're also not in the legacy group; otherwise fall through.
    # Legacy pnl_only group → Dashboard + P&L.
    if user.groups.filter(name=PNL_ONLY_GROUP).exists():
        return [SECTION_DASHBOARD, SECTION_PNL]
    # No profile, no group → full access (ordinary account).
    return list(ALL_SECTIONS)


def has_section(user, section):
    return section in get_allowed_sections(user)


def is_pnl_only(user):
    """Back-compat flag: user can see P&L but NOT the raw Expenses ledger."""
    if not user or not user.is_authenticated or is_admin_user(user):
        return False
    sections = get_allowed_sections(user)
    return SECTION_PNL in sections and SECTION_EXPENSES not in sections


class RequireSection(BasePermission):
    """Permission factory: only allow users whose sections include `section`.

    Usage:  permission_classes = [IsAuthenticated, RequireSection(SECTION_EXPENSES)]
    or as a decorator arg on function views.
    """
    section = None
    message = 'Your account does not have access to this section.'

    def __init__(self, section=None):
        # Allow both RequireSection(SECTION_X) instances and bare class use.
        if section is not None:
            self.section = section

    def __call__(self):
        # DRF instantiates permission classes; when we pass an instance we make
        # it callable so `RequireSection(SECTION_X)` works in permission_classes.
        return self

    def has_permission(self, request, view):
        return has_section(request.user, self.section)


class RequireAnySection(BasePermission):
    """Allow users who have AT LEAST ONE of the given sections.

    Usage: permission_classes = [IsAuthenticated, RequireAnySection(SECTION_A, SECTION_B)]
    """
    sections = ()
    message = 'Your account does not have access to this section.'

    def __init__(self, *sections):
        if sections:
            self.sections = sections

    def __call__(self):
        return self

    def has_permission(self, request, view):
        allowed = set(get_allowed_sections(request.user))
        return any(s in allowed for s in self.sections)


# Convenience: guards the raw expense ledger + all write/finance endpoints.
def _require_expenses():
    return RequireSection(SECTION_EXPENSES)


# Backwards-compatible alias — existing decorators reference BlockPnlOnly.
class BlockPnlOnly(BasePermission):
    """Deny access to endpoints that require the Expenses section."""
    message = 'Your account does not have access to this section.'

    def has_permission(self, request, view):
        return has_section(request.user, SECTION_EXPENSES)


class ExpensePagination(PageNumberPagination):
    """Pagination that exposes `page_size` in every response — lets the
    frontend compute total pages without hardcoding the size."""

    def get_paginated_response(self, data):
        return Response({
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'page_size': self.get_page_size(self.request),
            'results': data,
        })


class BranchViewSet(viewsets.ModelViewSet):
    """CRUD for branches."""
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    pagination_class = None
    permission_classes = [IsAuthenticated, BlockPnlOnly]


class ExpenseViewSet(viewsets.ModelViewSet):
    """CRUD for expenses with filtering and running balance."""

    pagination_class = ExpensePagination
    permission_classes = [IsAuthenticated, BlockPnlOnly]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ExpenseCreateSerializer
        return ExpenseSerializer

    def get_queryset(self):
        qs = Expense.objects.select_related('branch').all()

        # Filter by branch (can be ID or location name)
        branch_val = self.request.query_params.get('branch')
        if branch_val:
            if branch_val.isdigit():
                qs = qs.filter(branch_id=branch_val)
            else:
                qs = qs.filter(branch__location__icontains=branch_val)

        # Filter by category
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        # Search
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(credit_remark__icontains=search) |
                Q(debit_remark__icontains=search) |
                Q(credit_person__icontains=search) |
                Q(debit_person__icontains=search) |
                Q(category__icontains=search)
            )

        return qs.order_by('date', 'created_at')

    def list(self, request, *args, **kwargs):
        """Override list to include running balance computation."""
        queryset = self.get_queryset()

        # Calculate running balance
        expenses = list(queryset)
        initial_balances = {m.payment_mode: m.initial_balance for m in PaymentModeBalance.objects.all()}
        balances = {}
        for expense in expenses:
            credit = expense.credited_amount or Decimal('0.00')
            debit = expense.debited_amount or Decimal('0.00')
            
            if debit > 0:
                mode = expense.debit_payment_mode or 'Other'
                if mode not in balances:
                    balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                balances[mode] -= debit
                
            if credit > 0:
                mode = expense.credit_payment_mode or 'Other'
                if mode not in balances:
                    balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                balances[mode] += credit
                
            expense.running_balances = balances.copy()

        # To show newest first, reverse the list AFTER calculating running balances,
        # then paginate. This keeps correct balance cache on each object.
        expenses.reverse()

        # Pagination
        page = self.paginate_queryset(expenses)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(expenses, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['delete'], url_path='delete-all')
    def delete_all(self, request):
        """Delete all expenses. Gated by the admin-configured clear-data password
        so it can't be triggered by accident or via a direct API call."""
        setting = AppSetting.get_solo()
        if not setting.clear_password_is_set:
            return Response(
                {'detail': 'No clear-data password is configured. Ask an admin to set one in Settings first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        password = request.data.get('password') if isinstance(request.data, dict) else None
        if not password or not setting.check_clear_password(password):
            return Response({'detail': 'Incorrect password.'}, status=status.HTTP_403_FORBIDDEN)

        count, _ = Expense.objects.all().delete()
        return Response({'detail': f'Successfully deleted {count} expenses.'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, RequireAnySection(SECTION_DASHBOARD, SECTION_REGION)])
def dashboard_view(request):
    """Aggregated dashboard stats. Also feeds the Region Expense page, so users
    with either the Dashboard or Region section may read it."""
    qs = Expense.objects.all()

    # Apply same filters
    branch_val = request.query_params.get('branch')
    if branch_val:
        if branch_val.isdigit():
            qs = qs.filter(branch_id=branch_val)
        else:
            qs = qs.filter(branch__location__icontains=branch_val)

    category = request.query_params.get('category')
    if category:
        qs = qs.filter(category=category)

    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    totals = qs.aggregate(
        total_credits=Coalesce(Sum('credited_amount'), Decimal('0.00')),
        total_debits=Coalesce(Sum('debited_amount'), Decimal('0.00')),
    )
    
    # Total Balance is the sum of all Payment Mode balances (Company-wide absolute balance)
    total_initial = PaymentModeBalance.objects.aggregate(t=Coalesce(Sum('initial_balance'), Decimal('0.00')))['t']
    global_credits = Expense.objects.aggregate(t=Coalesce(Sum('credited_amount'), Decimal('0.00')))['t']
    global_debits = Expense.objects.aggregate(t=Coalesce(Sum('debited_amount'), Decimal('0.00')))['t']
    total_balance = total_initial + global_credits - global_debits

    # Category breakdown (for pie chart)
    category_data = (
        qs.values('category')
        .annotate(
            total_credit=Coalesce(Sum('credited_amount'), Decimal('0.00')),
            total_debit=Coalesce(Sum('debited_amount'), Decimal('0.00')),
        )
        .order_by('category')
    )

    # Monthly trend (for line chart)
    monthly_data = (
        qs.annotate(month=TruncMonth('date'))
        .values('month')
        .annotate(
            credits=Coalesce(Sum('credited_amount'), Decimal('0.00')),
            debits=Coalesce(Sum('debited_amount'), Decimal('0.00')),
        )
        .order_by('month')
    )

    # Branch-wise (for bar chart and detailed breakdown)
    branch_qs = (
        qs.values('branch__location')
        .annotate(
            total_credit=Coalesce(Sum('credited_amount'), Decimal('0.00')),
            total_debit=Coalesce(Sum('debited_amount'), Decimal('0.00')),
        )
        .order_by('branch__location')
    )

    branch_breakdown = []
    for item in branch_qs:
        location = item['branch__location']
        # Category breakdown for THIS branch (scoped to current filters)
        branch_cats = (
            qs.filter(branch__location=location)
            .values('category')
            .annotate(
                total_credit=Coalesce(Sum('credited_amount'), Decimal('0.00')),
                total_debit=Coalesce(Sum('debited_amount'), Decimal('0.00')),
            )
            .order_by('-total_debit')
        )

        branch_breakdown.append({
            'branch': location,
            'total_credit': str(item['total_credit']),
            'total_debit': str(item['total_debit']),
            'category_breakdown': [
                {
                    'category': c['category'],
                    'total_credit': str(c['total_credit']),
                    'total_debit': str(c['total_debit']),
                }
                for c in branch_cats
            ]
        })

    return Response({
        'total_balance': str(total_balance),
        'total_credits': str(totals['total_credits']),
        'total_debits': str(totals['total_debits']),
        'category_breakdown': [
            {
                'category': item['category'],
                'total_credit': str(item['total_credit']),
                'total_debit': str(item['total_debit']),
            }
            for item in category_data
        ],
        'monthly_trend': [
            {
                'month': item['month'].strftime('%Y-%m') if item['month'] else '',
                'credits': str(item['credits']),
                'debits': str(item['debits']),
            }
            for item in monthly_data
        ],
        'branch_breakdown': branch_breakdown,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def export_expenses(request):
    """Export expenses to CSV."""
    qs = Expense.objects.select_related('branch').all().order_by('date', 'created_at')

    # Apply filters
    branch_val = request.query_params.get('branch')
    if branch_val:
        if branch_val.isdigit():
            qs = qs.filter(branch_id=branch_val)
        else:
            qs = qs.filter(branch__location__icontains=branch_val)

    category = request.query_params.get('category')
    if category:
        qs = qs.filter(category=category)

    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)

    # Note: avoid the name `format` — DRF reserves it for content negotiation.
    fmt = request.query_params.get('type', 'csv')

    if fmt == 'excel':
        # Excel export
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = 'Expenses'

            headers = ['S.No', 'Date', 'Category', 'Branch', 'Credit Amount',
                        'Credit Remark', 'Credit Person', 'Credit Payment Mode',
                        'Debit Amount', 'Debit Remark', 'Debit Person',
                        'Debit Payment Mode', 'Running Balance']
            ws.append(headers)

            initial_balances = {m.payment_mode: m.initial_balance for m in PaymentModeBalance.objects.all()}
            balances = {}
            for idx, expense in enumerate(qs, 1):
                credit = expense.credited_amount or Decimal('0.00')
                debit = expense.debited_amount or Decimal('0.00')
                if debit > 0:
                    mode = expense.debit_payment_mode or 'Other'
                    if mode not in balances:
                        balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                    balances[mode] -= debit
                    
                if credit > 0:
                    mode = expense.credit_payment_mode or 'Other'
                    if mode not in balances:
                        balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                    balances[mode] += credit

                running_balance = " | ".join(f"{k}: {float(v)}" for k, v in balances.items() if v != Decimal('0.00'))


                ws.append([
                    idx,
                    expense.date.strftime('%Y-%m-%d'),
                    expense.category,
                    expense.branch.location,
                    float(credit),
                    expense.credit_remark,
                    expense.credit_person,
                    expense.credit_payment_mode,
                    float(debit),
                    expense.debit_remark,
                    expense.debit_person,
                    expense.debit_payment_mode,
                    running_balance,
                ])

            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            response = HttpResponse(
                buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename="expenses.xlsx"'
            return response
        except ImportError:
            return Response(
                {"error": "openpyxl not installed for Excel export"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    else:
        # CSV export
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="expenses.csv"'

        writer = csv.writer(response)
        writer.writerow(['S.No', 'Date', 'Category', 'Branch', 'Credit Amount',
                          'Credit Remark', 'Credit Person', 'Credit Payment Mode',
                          'Debit Amount', 'Debit Remark', 'Debit Person',
                          'Debit Payment Mode', 'Running Balance'])

        initial_balances = {m.payment_mode: m.initial_balance for m in PaymentModeBalance.objects.all()}
        balances = {}
        for idx, expense in enumerate(qs, 1):
            credit = expense.credited_amount or Decimal('0.00')
            debit = expense.debited_amount or Decimal('0.00')
            if debit > 0:
                mode = expense.debit_payment_mode or 'Other'
                if mode not in balances:
                    balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                balances[mode] -= debit
                
            if credit > 0:
                mode = expense.credit_payment_mode or 'Other'
                if mode not in balances:
                    balances[mode] = initial_balances.get(mode, Decimal('0.00'))
                balances[mode] += credit

            running_balance = " | ".join(f"{k}: {float(v)}" for k, v in balances.items() if v != Decimal('0.00'))


            writer.writerow([
                idx,
                expense.date.strftime('%Y-%m-%d'),
                expense.category,
                expense.branch.location,
                credit,
                expense.credit_remark,
                expense.credit_person,
                expense.credit_payment_mode,
                debit,
                expense.debit_remark,
                expense.debit_person,
                expense.debit_payment_mode,
                running_balance,
            ])

        return response


# ---------------------------------------------------------------------------
# Payment Mode Balances
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def payment_mode_balances_view(request):
    """Return all payment modes with initial + current balance, including those only present in expenses.
    
    Supports filtering:
      - fy: Financial year, e.g. '2025-2026' (April to March)
      - date_from / date_to: Custom date range
    """
    # Build expense filter based on query params
    expense_filter = Q()
    fy = request.query_params.get('fy')
    month = request.query_params.get('month')
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')

    start_date = None
    end_date = None

    if fy:
        try:
            parts = fy.split('-')
            start_year = int(parts[0])
            end_year = int(parts[1])
            
            if month:
                # month is 1-12
                m = int(month)
                # In India, FY starts in April. 
                # April (4) to Dec (12) are in start_year. 
                # Jan (1) to March (3) are in end_year.
                year = start_year if m >= 4 else end_year
                import calendar
                _, last_day = calendar.monthrange(year, m)
                start_date = f'{year}-{m:02d}-01'
                end_date = f'{year}-{m:02d}-{last_day:02d}'
            else:
                start_date = f'{start_year}-04-01'
                end_date = f'{end_year}-03-31'
            
            expense_filter &= Q(date__gte=start_date, date__lte=end_date)
        except (ValueError, IndexError):
            pass
    elif month:
        try:
            m = int(month)
            year = datetime.now().year
            import calendar
            _, last_day = calendar.monthrange(year, m)
            start_date = f'{year}-{m:02d}-01'
            end_date = f'{year}-{m:02d}-{last_day:02d}'
            expense_filter &= Q(date__gte=start_date, date__lte=end_date)
        except ValueError:
            pass
    else:
        if date_from:
            expense_filter &= Q(date__gte=date_from)
            start_date = date_from
        if date_to:
            expense_filter &= Q(date__lte=date_to)
            end_date = date_to

    balances = list(PaymentModeBalance.objects.all())
    explicit_modes = {b.payment_mode for b in balances}
    
    credit_modes = Expense.objects.exclude(credit_payment_mode='').values_list('credit_payment_mode', flat=True).distinct()
    debit_modes = Expense.objects.exclude(debit_payment_mode='').values_list('debit_payment_mode', flat=True).distinct()
    
    all_used_modes = set(credit_modes).union(set(debit_modes))
    missing_modes = all_used_modes - explicit_modes
    
    for mode in missing_modes:
        if mode:
            balances.append(PaymentModeBalance(
                id=len(balances) + 9999, # Fake ID to bypass frontend unique key warnings
                payment_mode=mode,
                initial_balance=Decimal('0.00')
            ))

    result = []
    for bal in balances:
        mode = bal.payment_mode
        
        # Calculate actual initial balance taking into account transactions before start_date
        period_initial = bal.initial_balance
        if start_date:
            past_credits = Expense.objects.filter(
                date__lt=start_date, credit_payment_mode=mode
            ).aggregate(
                total=Coalesce(Sum('credited_amount'), Decimal('0.00'))
            )['total']
            past_debits = Expense.objects.filter(
                date__lt=start_date, debit_payment_mode=mode
            ).aggregate(
                total=Coalesce(Sum('debited_amount'), Decimal('0.00'))
            )['total']
            period_initial += past_credits - past_debits

        # Credits with this payment mode (filtered)
        total_credits = Expense.objects.filter(
            expense_filter, credit_payment_mode=mode
        ).aggregate(
            total=Coalesce(Sum('credited_amount'), Decimal('0.00'))
        )['total']
        # Debits with this payment mode (filtered)
        total_debits = Expense.objects.filter(
            expense_filter, debit_payment_mode=mode
        ).aggregate(
            total=Coalesce(Sum('debited_amount'), Decimal('0.00'))
        )['total']
        
        current = period_initial + total_credits - total_debits
        period_available = total_credits - total_debits

        bal.initial_balance = period_initial
        bal.current_balance = current
        bal.total_credits = total_credits
        bal.total_debits = total_debits
        bal.period_available = period_available
        
        result.append(bal)

    serializer = PaymentModeBalanceSerializer(result, many=True)
    return Response(serializer.data)



@api_view(['POST'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def payment_mode_balance_set(request):
    """Create or update initial balance for a payment mode."""
    mode = request.data.get('payment_mode', '').strip()
    initial = request.data.get('initial_balance')

    if not mode:
        return Response(
            {'detail': 'payment_mode is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    obj, created = PaymentModeBalance.objects.update_or_create(
        payment_mode=mode,
        defaults={'initial_balance': Decimal(str(initial or 0))},
    )

    # Compute current balance
    total_credits = Expense.objects.filter(
        credit_payment_mode=mode
    ).aggregate(
        total=Coalesce(Sum('credited_amount'), Decimal('0.00'))
    )['total']
    total_debits = Expense.objects.filter(
        debit_payment_mode=mode
    ).aggregate(
        total=Coalesce(Sum('debited_amount'), Decimal('0.00'))
    )['total']
    obj.current_balance = obj.initial_balance + total_credits - total_debits

    serializer = PaymentModeBalanceSerializer(obj)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def payment_mode_balance_delete(request):
    """Delete a payment mode balance entry."""
    mode = request.data.get('payment_mode', '').strip()

    if not mode:
        return Response(
            {'detail': 'payment_mode is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        obj = PaymentModeBalance.objects.get(payment_mode=mode)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    except PaymentModeBalance.DoesNotExist:
        return Response(
            {'detail': f'Payment mode "{mode}" not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )


# ---------------------------------------------------------------------------
# Categories — expose model choices so the frontend doesn't hardcode them.
# ---------------------------------------------------------------------------
@api_view(['GET'])
def categories_view(request):
    """Single source of truth for expense categories (drawn from the model)."""
    return Response([value for value, _ in Expense.CATEGORY_CHOICES])


# ---------------------------------------------------------------------------
# Profit & Loss report — spreadsheet-style Income/Expense × Month matrix.
# ---------------------------------------------------------------------------

# Junk / aggregate "branch" values that should never appear as a real branch in
# the P&L. Compared case-insensitively after trimming.
_PNL_EXCLUDED_BRANCHES = {'', 'null', 'none', 'common', 'all location', 'all locations', 'main branch'}

# Categories that are always income even if a stray debit exists on them. These
# are the credit-driven revenue streams seen in the ledger. Matched on the
# canonical (UPPER + trimmed) category name.
_PNL_INCOME_CATEGORIES = {
    'LOAN', 'TRADE', 'SERVICE', 'EB RETURN', 'CLIENT RETURN', 'QUALITY TAX',
    'WARRANTY BILLS', 'WARRANTY BILL', 'BENCH ARC', 'ULR', 'OUT OF WARRANTY',
}

# Categories that are always expense even if a stray credit exists on them
# (e.g. a refund credited back onto SALARY/EXPENSES). Matched on canonical name.
_PNL_EXPENSE_CATEGORIES = {
    'SALARY', 'EXPENSES', 'EXPENSE', 'VENDOR', 'MISC', 'PETROL', 'PETROL ALLOWANCE',
    'RENT', 'OFFICE RENT', 'GST', 'LOAN RETURN',
}


def _pnl_canonical(name):
    """Canonical display form for a messy free-text label: trim + collapse
    internal whitespace + upper-case for matching. Returns '' for blank."""
    if not name:
        return ''
    return ' '.join(str(name).split()).upper()


def _pnl_classify(canonical_category, total_credit, total_debit):
    """Decide whether a category is 'income' or 'expense'.

    Data-driven with a hardcoded override for known ambiguous categories, so new
    categories added later are still classified automatically:
      1. Explicit income/expense sets win first.
      2. Otherwise, whichever side (credit vs debit) has the larger magnitude.
      3. Ties / all-zero fall back to 'expense' (the common case).
    """
    if canonical_category in _PNL_INCOME_CATEGORIES:
        return 'income'
    if canonical_category in _PNL_EXPENSE_CATEGORIES:
        return 'expense'
    if total_credit > total_debit:
        return 'income'
    return 'expense'


def _pnl_financial_year_bounds(fy_start_year):
    """Given a starting year (e.g. 2025) return (start_date, end_date) for the
    Indian financial year Apr 1 <start> – Mar 31 <start+1>, plus the ordered list
    of 12 month keys 'YYYY-MM' from April to March."""
    from datetime import date
    start = date(fy_start_year, 4, 1)
    end = date(fy_start_year + 1, 3, 31)
    months = []
    for i in range(12):
        m = 4 + i
        y = fy_start_year + (m - 1) // 12
        mm = (m - 1) % 12 + 1
        months.append(f'{y:04d}-{mm:02d}')
    return start, end, months


@api_view(['GET'])
@permission_classes([IsAuthenticated, RequireSection(SECTION_PNL)])
def profit_loss_view(request):
    """Profit & Loss matrix: income & expense categories (rows) × months (cols).

    Query params:
      fy       – financial-year start year, e.g. '2025' for FY 2025-26 (Apr–Mar).
                 Defaults to the FY containing the most recent expense (or today).
      branch   – branch id or (case-insensitive) location substring; junk/aggregate
                 branches are excluded from the default (all-branch) view.

    All messy real-world data is normalised in Python: branches and categories are
    merged case-insensitively, and income/expense is decided per category.
    """
    qs = Expense.objects.all()

    # ---- Branch filter (case-insensitive; digits => exact id) ----
    branch_val = request.query_params.get('branch')
    if branch_val:
        if branch_val.isdigit():
            qs = qs.filter(branch_id=branch_val)
        else:
            qs = qs.filter(branch__location__icontains=branch_val)

    # ---- Determine the financial year window ----
    fy_param = request.query_params.get('fy')
    if fy_param and fy_param.isdigit():
        fy_start = int(fy_param)
    else:
        latest = Expense.objects.order_by('-date').values_list('date', flat=True).first()
        ref = latest if latest else datetime.now().date()
        # Jan–Mar belongs to the FY that started the previous calendar year.
        fy_start = ref.year if ref.month >= 4 else ref.year - 1

    start_date, end_date, month_keys = _pnl_financial_year_bounds(fy_start)
    qs = qs.filter(date__gte=start_date, date__lte=end_date)

    # ---- Aggregate: category × month, credit & debit ----
    rows = (
        qs.annotate(month=TruncMonth('date'))
        .values('category', 'month')
        .annotate(
            credit=Coalesce(Sum('credited_amount'), Decimal('0.00')),
            debit=Coalesce(Sum('debited_amount'), Decimal('0.00')),
        )
    )

    # Merge case-variant categories together and bucket amounts by month.
    # canon -> {'display': str, 'credit_total': Decimal, 'debit_total': Decimal,
    #           'months': {month_key: {'credit': Decimal, 'debit': Decimal}}}
    cat_map = {}
    for r in rows:
        canon = _pnl_canonical(r['category'])
        if not canon:
            canon = 'UNCATEGORISED'
        month_key = r['month'].strftime('%Y-%m') if r['month'] else ''
        entry = cat_map.get(canon)
        if entry is None:
            # Title-case a nicer display label from the canonical form.
            entry = {
                'display': canon.title(),
                'credit_total': Decimal('0.00'),
                'debit_total': Decimal('0.00'),
                'months': {},
            }
            cat_map[canon] = entry
        entry['credit_total'] += r['credit']
        entry['debit_total'] += r['debit']
        mslot = entry['months'].setdefault(month_key, {'credit': Decimal('0.00'), 'debit': Decimal('0.00')})
        mslot['credit'] += r['credit']
        mslot['debit'] += r['debit']

    # Build income & expense row lists. For income rows the per-month value is the
    # credit; for expense rows it's the debit — that's what a P&L shows.
    income_rows, expense_rows = [], []
    for canon, entry in cat_map.items():
        kind = _pnl_classify(canon, entry['credit_total'], entry['debit_total'])
        if kind == 'income':
            monthly = {mk: entry['months'].get(mk, {}).get('credit', Decimal('0.00')) for mk in month_keys}
            total = sum(monthly.values(), Decimal('0.00'))
            if total == 0:
                continue  # skip empty income rows for this FY
            income_rows.append({'category': entry['display'], 'monthly': monthly, 'total': total})
        else:
            monthly = {mk: entry['months'].get(mk, {}).get('debit', Decimal('0.00')) for mk in month_keys}
            total = sum(monthly.values(), Decimal('0.00'))
            if total == 0:
                continue
            expense_rows.append({'category': entry['display'], 'monthly': monthly, 'total': total})

    # Sort each section by grand total, largest first (most material rows on top).
    income_rows.sort(key=lambda x: x['total'], reverse=True)
    expense_rows.sort(key=lambda x: x['total'], reverse=True)

    def _serialize_rows(section):
        return [
            {
                'category': row['category'],
                'monthly': {mk: str(row['monthly'][mk]) for mk in month_keys},
                'total': str(row['total']),
            }
            for row in section
        ]

    # Column (per-month) totals + net profit/loss per month.
    income_by_month = {mk: sum((row['monthly'][mk] for row in income_rows), Decimal('0.00')) for mk in month_keys}
    expense_by_month = {mk: sum((row['monthly'][mk] for row in expense_rows), Decimal('0.00')) for mk in month_keys}
    net_by_month = {mk: income_by_month[mk] - expense_by_month[mk] for mk in month_keys}

    total_income = sum(income_by_month.values(), Decimal('0.00'))
    total_expense = sum(expense_by_month.values(), Decimal('0.00'))

    # Build the branch dropdown list: canonical, de-duplicated, junk excluded.
    branch_names = {}
    for loc in Branch.objects.values_list('location', flat=True):
        canon = _pnl_canonical(loc)
        if canon.lower() in _PNL_EXCLUDED_BRANCHES:
            continue
        branch_names.setdefault(canon, canon.title())
    branches = sorted(branch_names.values())

    # Available financial years (for the FY picker), derived from data.
    first_date = Expense.objects.order_by('date').values_list('date', flat=True).first()
    last_date = Expense.objects.order_by('-date').values_list('date', flat=True).first()
    fy_list = []
    if first_date and last_date:
        lo_fy = first_date.year if first_date.month >= 4 else first_date.year - 1
        hi_fy = last_date.year if last_date.month >= 4 else last_date.year - 1
        fy_list = list(range(lo_fy, hi_fy + 1))
    if fy_start not in fy_list:
        fy_list.append(fy_start)
    fy_list = sorted(set(fy_list), reverse=True)

    return Response({
        'fy_start': fy_start,
        'fy_label': f'FY {fy_start}-{str(fy_start + 1)[-2:]}',
        'months': month_keys,
        'available_fys': fy_list,
        'branches': branches,
        'income': _serialize_rows(income_rows),
        'expense': _serialize_rows(expense_rows),
        'income_by_month': {mk: str(income_by_month[mk]) for mk in month_keys},
        'expense_by_month': {mk: str(expense_by_month[mk]) for mk in month_keys},
        'net_by_month': {mk: str(net_by_month[mk]) for mk in month_keys},
        'total_income': str(total_income),
        'total_expense': str(total_expense),
        'net_profit': str(total_income - total_expense),
    })


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """Username + password → token. Used by the SPA login form."""
    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''

    if not username or not password:
        return Response(
            {'detail': 'Username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        return Response(
            {'detail': 'Invalid credentials.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    token, _ = Token.objects.get_or_create(user=user)
    return Response({
        'token': token.key,
        'username': user.username,
        'is_staff': user.is_staff,
        'is_admin': is_admin_user(user),
        'allowed_sections': get_allowed_sections(user),
        'pnl_only': is_pnl_only(user),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """Invalidate the caller's token."""
    Token.objects.filter(user=request.user).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    """Return current user info — used to verify a stored token is still valid."""
    return Response({
        'username': request.user.username,
        'is_staff': request.user.is_staff,
        'is_admin': is_admin_user(request.user),
        'allowed_sections': get_allowed_sections(request.user),
        'pnl_only': is_pnl_only(request.user),
    })


# ---------------------------------------------------------------------------
# Admin: user management (create login accounts + control section access)
# ---------------------------------------------------------------------------
from django.contrib.auth.models import User as AuthUserModel


class IsAdminSection(BasePermission):
    """Only staff/superusers may manage users."""
    message = 'Only administrators can manage users.'

    def has_permission(self, request, view):
        return is_admin_user(request.user)


def _serialize_managed_user(user):
    """Public shape of a user row for the admin panel."""
    return {
        'id': user.id,
        'username': user.username,
        'is_admin': is_admin_user(user),
        'is_active': user.is_active,
        'allowed_sections': get_allowed_sections(user),
        'date_joined': user.date_joined.isoformat() if user.date_joined else None,
    }


def _clean_section_input(raw):
    """Validate a requested section list → canonical, de-duplicated subset."""
    if not isinstance(raw, list):
        return None
    wanted = {str(s).strip().lower() for s in raw}
    return [s for s in ALL_SECTIONS if s in wanted]


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminSection])
def admin_sections_view(request):
    """List the sections that can be granted, with labels (for the UI)."""
    from .models import SECTION_LABELS
    return Response([
        {'key': key, 'label': SECTION_LABELS.get(key, key)}
        for key in ALL_SECTIONS
    ])


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsAdminSection])
def clear_data_password_view(request):
    """Admin-only. GET reports whether a clear-data password is set; POST sets or
    changes it (hashed). This password gates the destructive Clear All Data action."""
    setting = AppSetting.get_solo()
    if request.method == 'GET':
        return Response({'is_set': setting.clear_password_is_set})

    password = (request.data.get('password') or '') if isinstance(request.data, dict) else ''
    if len(password) < 4:
        return Response({'detail': 'Password must be at least 4 characters.'}, status=status.HTTP_400_BAD_REQUEST)
    setting.set_clear_password(password)
    setting.save()
    return Response({'is_set': True})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsAdminSection])
def admin_users_view(request):
    """GET: list all users. POST: create a new login account with sections."""
    if request.method == 'GET':
        users = AuthUserModel.objects.all().order_by('-is_superuser', 'username')
        return Response([_serialize_managed_user(u) for u in users])

    # --- POST: create ---
    username = (request.data.get('username') or '').strip()
    password = request.data.get('password') or ''
    sections = _clean_section_input(request.data.get('allowed_sections', []))

    if not username or not password:
        return Response({'detail': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)
    if sections is None:
        return Response({'detail': 'allowed_sections must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
    if AuthUserModel.objects.filter(username__iexact=username).exists():
        return Response({'detail': f"A user named '{username}' already exists."}, status=status.HTTP_400_BAD_REQUEST)
    if len(password) < 6:
        return Response({'detail': 'Password must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)

    user = AuthUserModel.objects.create_user(username=username, password=password)
    user.is_staff = False
    user.is_superuser = False
    user.save()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.allowed_sections = sections
    profile.save()
    return Response(_serialize_managed_user(user), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated, IsAdminSection])
def admin_user_detail_view(request, pk):
    """PATCH: update sections / reset password. DELETE: remove the account."""
    try:
        user = AuthUserModel.objects.get(pk=pk)
    except AuthUserModel.DoesNotExist:
        return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

    # Guard: never let an admin lock themselves out or edit an admin's access.
    if request.method == 'DELETE':
        if user.id == request.user.id:
            return Response({'detail': 'You cannot delete your own account.'}, status=status.HTTP_400_BAD_REQUEST)
        if is_admin_user(user):
            return Response({'detail': 'Admin accounts cannot be deleted here.'}, status=status.HTTP_400_BAD_REQUEST)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # --- PATCH ---
    if is_admin_user(user):
        return Response({'detail': 'Admin accounts always have full access and cannot be edited here.'}, status=status.HTTP_400_BAD_REQUEST)

    # Update sections if provided.
    if 'allowed_sections' in request.data:
        sections = _clean_section_input(request.data.get('allowed_sections'))
        if sections is None:
            return Response({'detail': 'allowed_sections must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.allowed_sections = sections
        profile.save()
        # Once an explicit profile is set, drop the legacy group so it can't
        # override the per-user sections.
        user.groups.remove(*user.groups.filter(name=PNL_ONLY_GROUP))

    # Reset password if provided.
    new_password = request.data.get('password')
    if new_password:
        if len(new_password) < 6:
            return Response({'detail': 'Password must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new_password)
        user.save()
        # Invalidate existing tokens so the old session can't linger.
        Token.objects.filter(user=user).delete()

    # Toggle active state if provided.
    if 'is_active' in request.data:
        user.is_active = bool(request.data.get('is_active'))
        user.save()
        if not user.is_active:
            Token.objects.filter(user=user).delete()

    user.refresh_from_db()
    return Response(_serialize_managed_user(user))


# ---------------------------------------------------------------------------
# Billing Reminders
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def billing_reminders_list(request):
    """List all billing reminders."""
    reminders = BillingReminder.objects.all()
    serializer = BillingReminderSerializer(reminders, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def billing_reminder_create(request):
    """Create a new billing reminder."""
    serializer = BillingReminderSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PUT'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def billing_reminder_update(request, pk):
    """Update a billing reminder."""
    try:
        reminder = BillingReminder.objects.get(pk=pk)
    except BillingReminder.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = BillingReminderSerializer(reminder, data=request.data, partial=True)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def billing_reminder_toggle_paid(request, pk):
    """Toggle the is_paid status of a billing reminder."""
    try:
        reminder = BillingReminder.objects.get(pk=pk)
    except BillingReminder.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    reminder.is_paid = not reminder.is_paid
    reminder.save()
    serializer = BillingReminderSerializer(reminder)
    return Response(serializer.data)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def billing_reminder_delete(request, pk):
    """Delete a billing reminder."""
    try:
        reminder = BillingReminder.objects.get(pk=pk)
    except BillingReminder.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    reminder.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def import_expenses(request):
    """Import expenses from Excel or CSV file."""
    file_obj = request.FILES.get('file')
    if not file_obj:
        return Response({'detail': 'No file was uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    import openpyxl
    from io import BytesIO

    try:
        fname = file_obj.name.lower()
        if fname.endswith('.xlsx'):
            wb = openpyxl.load_workbook(file_obj, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
        elif fname.endswith('.csv') or fname.endswith('.txt'):
            import csv
            file_data = file_obj.read().decode('utf-8-sig').splitlines()
            reader = csv.reader(file_data)
            rows = list(reader)
        else:
            return Response({'detail': 'Only .xlsx and .csv file formats are supported.'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({'detail': f'Error reading file: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

    if not rows:
        return Response({'detail': 'The uploaded file is empty.'}, status=status.HTTP_400_BAD_REQUEST)

    # Dynamically find the header row by looking for known keywords
    header_keywords = {'date', 'category', 'branch', 'amount', 'credit', 'debit', 'type', 'remark'}
    header_idx = 0
    headers = []

    for i, row in enumerate(rows):
        curr_headers = [str(cell).strip().lower() if cell is not None else '' for cell in row]
        # If this row has multiple header keywords, it's probably our header row
        if any(k in curr_headers or any(k in h for h in curr_headers) for k in header_keywords):
            headers = curr_headers
            header_idx = i
            break
    else:
        # Fallback to first row if keywords aren't matched explicitly
        headers = [str(cell).strip().lower() if cell is not None else '' for cell in rows[0]]
        header_idx = 0

    print(f"[IMPORT DEBUG] Found headers at row {header_idx + 1}: {headers}")
    print(f"[IMPORT DEBUG] Total data rows to process: {len(rows) - header_idx - 1}")
    
    header_mapping = {
        'date': 'date',
        'category': 'category',
        'branch': 'branch',
        'credit': 'credited_amount',
        'debit': 'debited_amount',
        'credit amount': 'credited_amount',
        'credit_amount': 'credited_amount',
        'credited_amount': 'credited_amount',
        'credit remark': 'credit_remark',
        'credit_remark': 'credit_remark',
        'credit person': 'credit_person',
        'credit_person': 'credit_person',
        'credit payment mode': 'credit_payment_mode',
        'credit_payment_mode': 'credit_payment_mode',
        'debit amount': 'debited_amount',
        'debit_amount': 'debited_amount',
        'debited_amount': 'debited_amount',
        'debit remark': 'debit_remark',
        'debit_remark': 'debit_remark',
        'debit person': 'debit_person',
        'debit_person': 'debit_person',
        'debit payment mode': 'debit_payment_mode',
        'debit_payment_mode': 'debit_payment_mode',
        'remark': 'remark',
        'person': 'person',
        'mode': 'mode',
        'payment mode': 'mode',
        'amount': 'amount',
        'type': 'type',
        'expense type': 'type',
        'transaction type': 'type',
    }
 
    import_data = []
    # Start processing data rows occurring AFTER the header row
    for row_idx, row in enumerate(rows[header_idx + 1:], header_idx + 2):
        if not any(cell is not None and str(cell).strip() != '' for cell in row):
            continue
 
        row_dict = {}
        for col_idx, cell in enumerate(row):
            if col_idx < len(headers):
                header = headers[col_idx]
                field = header_mapping.get(header)
                if field:
                    if cell is None or str(cell).strip() == '':
                        if field in ['credited_amount', 'debited_amount', 'amount']:
                            row_dict[field] = None
                        else:
                            row_dict[field] = ''
                    elif field == 'date':
                        import datetime as dt
                        if isinstance(cell, (dt.datetime, dt.date)):
                            if isinstance(cell, dt.datetime):
                                row_dict[field] = cell.date().isoformat()
                            else:
                                row_dict[field] = cell.isoformat()
                        else:
                            try:
                                date_str = str(cell).strip().split(' ')[0]
                                parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                                row_dict[field] = parsed_date.date().isoformat()
                            except ValueError:
                                try:
                                    date_str = str(cell).strip().split(' ')[0]
                                    parsed_date = datetime.strptime(date_str, '%d-%m-%Y')
                                    row_dict[field] = parsed_date.date().isoformat()
                                except ValueError:
                                    try:
                                        date_str = str(cell).strip().split(' ')[0]
                                        parsed_date = datetime.strptime(date_str, '%d/%m/%Y')
                                        row_dict[field] = parsed_date.date().isoformat()
                                    except ValueError:
                                        try:
                                            date_str = str(cell).strip().split(' ')[0]
                                            parsed_date = datetime.strptime(date_str, '%Y/%m/%d')
                                            row_dict[field] = parsed_date.date().isoformat()
                                        except ValueError:
                                            try:
                                                date_str = str(cell).strip().split(' ')[0]
                                                parsed_date = datetime.strptime(date_str, '%d.%m.%Y')
                                                row_dict[field] = parsed_date.date().isoformat()
                                            except ValueError:
                                                try:
                                                    date_str = str(cell).strip().split(' ')[0]
                                                    parsed_date = datetime.strptime(date_str, '%Y.%m.%d')
                                                    row_dict[field] = parsed_date.date().isoformat()
                                                except ValueError:
                                                    try:
                                                        date_str = str(cell).strip().split(' ')[0]
                                                        parsed_date = datetime.strptime(date_str, '%d-%b-%Y')
                                                        row_dict[field] = parsed_date.date().isoformat()
                                                    except ValueError:
                                                        try:
                                                            date_str = " ".join(str(cell).strip().split(' ')[:3])
                                                            parsed_date = datetime.strptime(date_str, '%d %b %Y')
                                                            row_dict[field] = parsed_date.date().isoformat()
                                                        except ValueError:
                                                            row_dict[field] = str(cell).strip()
                    elif field in ['credited_amount', 'debited_amount']:
                        try:
                            cleaned_val = str(cell).replace('₹', '').replace('Rs', '').replace(',', '').strip()
                            val = float(cleaned_val)
                            row_dict[field] = val if val > 0 else None
                        except (ValueError, TypeError):
                            row_dict[field] = None
                    elif field == 'amount':
                        try:
                            cleaned_val = str(cell).replace('₹', '').replace('Rs', '').replace(',', '').strip()
                            row_dict['amount'] = float(cleaned_val)
                        except (ValueError, TypeError):
                            row_dict['amount'] = None
                    elif field == 'type':
                        row_dict['type'] = str(cell).strip().lower()
                    else:
                        row_dict[field] = str(cell).strip()

        # Handle single 'amount' and 'type' column format if separate columns aren't filled
        if row_dict.get('amount') is not None:
            amt = row_dict['amount']
            t = row_dict.get('type', 'debit')
            if 'credit' in t:
                row_dict['credited_amount'] = amt
                row_dict['debited_amount'] = None
            else:
                row_dict['debited_amount'] = amt
                row_dict['credited_amount'] = None

        if 'credited_amount' not in row_dict:
            row_dict['credited_amount'] = None
        if 'debited_amount' not in row_dict:
            row_dict['debited_amount'] = None

        is_credit = row_dict.get('credited_amount') is not None

        if 'remark' in row_dict:
            if is_credit:
                row_dict['credit_remark'] = row_dict['remark']
                row_dict['debit_remark'] = ''
            else:
                row_dict['debit_remark'] = row_dict['remark']
                row_dict['credit_remark'] = ''

        if 'person' in row_dict:
            if is_credit:
                row_dict['credit_person'] = row_dict['person']
                row_dict['debit_person'] = ''
            else:
                row_dict['debit_person'] = row_dict['person']
                row_dict['credit_person'] = ''

        if 'mode' in row_dict:
            if is_credit:
                row_dict['credit_payment_mode'] = row_dict['mode']
                row_dict['debit_payment_mode'] = ''
            else:
                row_dict['debit_payment_mode'] = row_dict['mode']
                row_dict['credit_payment_mode'] = ''

        # Normalize payment modes to valid Title Case/Uppercase choice values
        def normalize_mode(val):
            if not val:
                return ''
            val_lower = str(val).strip().lower()
            if val_lower in ['cash', 'c']:
                return 'Cash'
            if val_lower in ['bank transfer', 'bank_transfer', 'bank', 'transfer']:
                return 'Bank Transfer'
            if val_lower in ['gpay', 'g-pay', 'google pay']:
                return 'GPay'
            if val_lower in ['phonepe', 'phone-pe', 'phone pe']:
                return 'PhonePe'
            if val_lower in ['upi']:
                return 'UPI'
            if val_lower in ['cheque']:
                return 'Cheque'
            return 'Other'

        if row_dict.get('credit_payment_mode'):
            row_dict['credit_payment_mode'] = normalize_mode(row_dict['credit_payment_mode'])
        if row_dict.get('debit_payment_mode'):
            row_dict['debit_payment_mode'] = normalize_mode(row_dict['debit_payment_mode'])

        if 'branch' not in row_dict or not row_dict['branch']:
            row_dict['branch'] = 'Main Branch'
        if 'category' not in row_dict or not row_dict['category']:
            row_dict['category'] = 'Misc'
        if 'date' not in row_dict or not row_dict['date']:
            import datetime as dt
            row_dict['date'] = dt.date.today().isoformat()

        # Final sanitization: convert any leftover None/missing for string fields to empty string
        string_fields = [
            'credit_remark', 'debit_remark',
            'credit_person', 'debit_person',
            'credit_payment_mode', 'debit_payment_mode',
            'category', 'branch'
        ]
        for f in string_fields:
            if row_dict.get(f) is None:
                row_dict[f] = ''

        import_data.append((row_idx, row_dict))

    errors = []
    success_count = 0

    # Fields accepted by the serializer
    valid_fields = {
        'date', 'category', 'branch',
        'credited_amount', 'credit_remark', 'credit_person', 'credit_payment_mode',
        'debited_amount', 'debit_remark', 'debit_person', 'debit_payment_mode',
    }

    for row_idx, data in import_data:
        # Strip out extra keys that aren't in the serializer
        clean_data = {k: v for k, v in data.items() if k in valid_fields}
        print(f"[IMPORT DEBUG] Row {row_idx} clean_data: {clean_data}")
        serializer = ExpenseCreateSerializer(data=clean_data)
        if serializer.is_valid():
            serializer.save()
            success_count += 1
            print(f"[IMPORT DEBUG] Row {row_idx}: SAVED OK")
        else:
            err_msg = ", ".join([f"{k}: {', '.join(v)}" for k, v in serializer.errors.items()])
            errors.append(f"Row {row_idx}: {err_msg}")
            print(f"[IMPORT DEBUG] Row {row_idx} ERRORS: {serializer.errors}")

    if errors:
        return Response({
            'detail': f'Import completed. Successfully imported {success_count} of {success_count + len(errors)} entries.',
            'errors': errors,
            'success_count': success_count
        }, status=status.HTTP_200_OK)

    return Response({
        'detail': f'Successfully imported {success_count} expenses.',
        'success_count': success_count
    }, status=status.HTTP_201_CREATED)


class PettyCashDebitViewSet(viewsets.ModelViewSet):
    queryset = PettyCashDebit.objects.select_related('branch').all()
    serializer_class = PettyCashDebitSerializer
    pagination_class = None
    permission_classes = [IsAuthenticated, BlockPnlOnly]

    def get_queryset(self):
        qs = PettyCashDebit.objects.select_related('branch').all()
        branch_val = self.request.query_params.get('branch')
        if branch_val:
            if branch_val.isdigit():
                qs = qs.filter(branch_id=branch_val)
            else:
                qs = qs.filter(branch__location__icontains=branch_val)
        
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
            
        return qs.order_by('-date', '-created_at')


@api_view(['GET'])
@permission_classes([IsAuthenticated, BlockPnlOnly])
def petty_cash_summary(request):
    """Get petty cash summary, including credits (from Expenses) and debits."""
    credits_qs = Expense.objects.filter(category__icontains='petty')
    debits_qs = PettyCashDebit.objects.all()

    # Apply branch and date filters
    branch_val = request.query_params.get('branch')
    if branch_val:
        if branch_val.isdigit():
            credits_qs = credits_qs.filter(branch_id=branch_val)
            debits_qs = debits_qs.filter(branch_id=branch_val)
        else:
            credits_qs = credits_qs.filter(branch__location__icontains=branch_val)
            debits_qs = debits_qs.filter(branch__location__icontains=branch_val)

    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')
    if date_from:
        credits_qs = credits_qs.filter(date__gte=date_from)
        debits_qs = debits_qs.filter(date__gte=date_from)
    if date_to:
        credits_qs = credits_qs.filter(date__lte=date_to)
        debits_qs = debits_qs.filter(date__lte=date_to)

    # Calculate totals
    totals = credits_qs.aggregate(
        credits_sum=Coalesce(Sum('credited_amount'), Decimal('0.00')),
        debits_sum=Coalesce(Sum('debited_amount'), Decimal('0.00')),
    )
    total_credits = totals['credits_sum'] + totals['debits_sum']
    
    total_debits = debits_qs.aggregate(total=Coalesce(Sum('amount'), Decimal('0.00')))['total']
    balance = total_credits - total_debits

    credits_data = ExpenseSerializer(credits_qs.order_by('-date', '-created_at'), many=True).data
    debits_data = PettyCashDebitSerializer(debits_qs.order_by('-date', '-created_at'), many=True).data

    return Response({
        'balance': str(balance),
        'total_credits': str(total_credits),
        'total_debits': str(total_debits),
        'credits': credits_data,
        'debits': debits_data,
    })


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceViewSet(viewsets.ModelViewSet):
    """CRUD for customer invoices (Tax Invoice / Bill of Supply)."""
    queryset = Invoice.objects.prefetch_related('items').all()
    serializer_class = InvoiceSerializer
    pagination_class = None
    permission_classes = [IsAuthenticated, RequireSection(SECTION_INVOICE)]

    def get_queryset(self):
        qs = Invoice.objects.prefetch_related('items').all()
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(invoice_number__icontains=search) |
                Q(customer_name__icontains=search)
            )
        return qs.order_by('-issue_date', '-created_at')

