from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# App sections — the canonical list of pages a user can be granted access to.
# Used for per-user access control (see UserProfile.allowed_sections).
# ---------------------------------------------------------------------------
SECTION_DASHBOARD = 'dashboard'
SECTION_EXPENSES = 'expenses'
SECTION_PNL = 'pnl'
SECTION_REGION = 'region'
SECTION_INVOICE = 'invoice'

ALL_SECTIONS = [SECTION_DASHBOARD, SECTION_EXPENSES, SECTION_PNL, SECTION_REGION, SECTION_INVOICE]

SECTION_LABELS = {
    SECTION_DASHBOARD: 'Dashboard',
    SECTION_EXPENSES: 'Expenses',
    SECTION_PNL: 'Profit & Loss',
    SECTION_REGION: 'Region Expense',
    SECTION_INVOICE: 'Invoice',
}


class Branch(models.Model):
    """Company branch — identified by location."""
    location = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Branches'
        ordering = ['location']

    def __str__(self):
        return self.location

    @property
    def current_balance(self):
        """Calculate balance = total credits - total debits for this branch."""
        from django.db.models import Sum
        totals = self.expenses.aggregate(
            total_credits=Sum('credited_amount'),
            total_debits=Sum('debited_amount'),
        )
        credits = totals['total_credits'] or 0
        debits = totals['total_debits'] or 0
        return credits - debits


class Expense(models.Model):
    """Expense entry model."""
    CATEGORY_CHOICES = [
        ('Petrol', 'Petrol'),
        ('Food', 'Food'),
        ('Travel', 'Travel'),
        ('Snacks', 'Snacks'),
        ('Stationary', 'Stationary'),
        ('Toolkit', 'Toolkit'),
        ('Misc', 'Misc'),
    ]

    date = models.DateField()
    category = models.CharField(max_length=100)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='expenses',
    )
    credited_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    credit_remark = models.CharField(max_length=300, blank=True, default='')
    credit_person = models.CharField(max_length=200, blank=True, default='')
    credit_payment_mode = models.CharField(
        max_length=30,
        blank=True,
        default='',
        choices=[
            ('Cash', 'Cash'),
            ('Bank Transfer', 'Bank Transfer'),
            ('GPay', 'GPay'),
            ('PhonePe', 'PhonePe'),
            ('UPI', 'UPI'),
            ('Cheque', 'Cheque'),
            ('Other', 'Other'),
        ],
    )
    debited_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )
    debit_remark = models.CharField(max_length=300, blank=True, default='')
    debit_person = models.CharField(max_length=200, blank=True, default='')
    debit_payment_mode = models.CharField(
        max_length=30,
        blank=True,
        default='',
        choices=[
            ('Cash', 'Cash'),
            ('Bank Transfer', 'Bank Transfer'),
            ('GPay', 'GPay'),
            ('PhonePe', 'PhonePe'),
            ('UPI', 'UPI'),
            ('Cheque', 'Cheque'),
            ('Other', 'Other'),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.date} | {self.category} | {self.branch.location}"


PAYMENT_MODE_CHOICES = [
    ('Cash', 'Cash'),
    ('Bank Transfer', 'Bank Transfer'),
    ('GPay', 'GPay'),
    ('PhonePe', 'PhonePe'),
    ('UPI', 'UPI'),
    ('Cheque', 'Cheque'),
    ('Other', 'Other'),
]


class PaymentModeBalance(models.Model):
    """Tracks initial balance for each payment mode."""
    payment_mode = models.CharField(
        max_length=30,
        choices=PAYMENT_MODE_CHOICES,
        unique=True,
    )
    initial_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['payment_mode']

    def __str__(self):
        return f"{self.payment_mode}: {self.initial_balance}"


class BillingReminder(models.Model):
    """Recurring bill / expense reminder (e.g. WiFi, electricity, rent)."""

    FREQUENCY_CHOICES = [
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('half_yearly', 'Half Yearly'),
        ('yearly', 'Yearly'),
        ('one_time', 'One Time'),
    ]

    title = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_day = models.PositiveIntegerField(
        help_text='Day of month when bill is due (1-31)',
        default=1,
    )
    frequency = models.CharField(
        max_length=20,
        choices=FREQUENCY_CHOICES,
        default='monthly',
    )
    category = models.CharField(max_length=100, blank=True, default='')
    branch = models.ForeignKey(
        'Branch',
        on_delete=models.CASCADE,
        related_name='billing_reminders',
        null=True,
        blank=True
    )
    notes = models.TextField(blank=True, default='')
    is_paid = models.BooleanField(default=False)
    next_due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['next_due_date', 'due_day']

    def __str__(self):
        return f"{self.title} — ₹{self.amount} ({self.get_frequency_display()})"


class PettyCashDebit(models.Model):
    """Tracks direct cash expenditures from petty cash drawer."""
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    remark = models.CharField(max_length=300, blank=True, default='')
    person = models.CharField(max_length=200, blank=True, default='')
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name='petty_cash_debits',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"{self.date} | {self.amount} | {self.branch.location}"


class UserProfile(models.Model):
    """Per-user app settings — currently which sections the user may access.

    Admins (is_staff / is_superuser) implicitly get every section regardless of
    this list; this only restricts non-admin logins. `allowed_sections` stores a
    subset of ALL_SECTIONS.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    allowed_sections = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}: {self.allowed_sections}"

    def clean_sections(self):
        """Return the stored sections filtered to only valid, known section keys,
        preserving the canonical order."""
        stored = set(self.allowed_sections or [])
        return [s for s in ALL_SECTIONS if s in stored]


# ---------------------------------------------------------------------------
# Invoicing — GST tax invoices / bills of supply
# ---------------------------------------------------------------------------
class Invoice(models.Model):
    """A customer invoice. Renders as a TAX INVOICE (with GST) or a BILL OF
    SUPPLY (no GST) — decided automatically from whether any line has GST,
    unless `doc_type` is forced to a specific value."""

    DOC_AUTO = 'auto'
    DOC_TAX_INVOICE = 'tax_invoice'
    DOC_BILL_OF_SUPPLY = 'bill_of_supply'
    DOC_TYPE_CHOICES = [
        (DOC_AUTO, 'Auto (based on GST)'),
        (DOC_TAX_INVOICE, 'Tax Invoice'),
        (DOC_BILL_OF_SUPPLY, 'Bill of Supply'),
    ]

    # Human-facing invoice number, e.g. "RT25-26-REN-2471". Auto-generated if blank.
    invoice_number = models.CharField(max_length=60, unique=True, blank=True)
    doc_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES, default=DOC_AUTO)

    # Bill To
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    customer_gstin = models.CharField(max_length=20, blank=True, default='')

    # Ship To (falls back to Bill To when blank)
    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    contact_name = models.CharField(max_length=200, blank=True, default='')
    terms = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.invoice_number} | {self.customer_name}"

    # --- Derived amounts (computed from items) ---
    @property
    def taxable_total(self):
        from decimal import Decimal
        return sum((item.taxable_value for item in self.items.all()), Decimal('0.00'))

    @property
    def cgst_total(self):
        from decimal import Decimal
        return sum((item.cgst_amount for item in self.items.all()), Decimal('0.00'))

    @property
    def sgst_total(self):
        from decimal import Decimal
        return sum((item.sgst_amount for item in self.items.all()), Decimal('0.00'))

    @property
    def has_gst(self):
        return any(item.gst_rate and item.gst_rate > 0 for item in self.items.all())

    @property
    def resolved_doc_type(self):
        """Effective document type after applying the auto rule."""
        if self.doc_type == self.DOC_TAX_INVOICE:
            return self.DOC_TAX_INVOICE
        if self.doc_type == self.DOC_BILL_OF_SUPPLY:
            return self.DOC_BILL_OF_SUPPLY
        return self.DOC_TAX_INVOICE if self.has_gst else self.DOC_BILL_OF_SUPPLY

    @property
    def grand_total_raw(self):
        """Taxable + all GST, before rounding."""
        return self.taxable_total + self.cgst_total + self.sgst_total

    @property
    def grand_total(self):
        """Rounded to the nearest rupee (standard invoice rounding)."""
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        """grand_total - grand_total_raw (can be negative or positive)."""
        return self.grand_total - self.grand_total_raw


class InvoiceItem(models.Model):
    """A single line on an invoice."""
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')  # the italic notes under an item
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # GST rate as a percentage for THIS line's total GST (e.g. 18 → 9% CGST + 9% SGST).
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    # A line's price is treated as GST-inclusive-free (i.e. the taxable value is
    # qty × unit_price), matching how the sample invoices compute the table.
    @property
    def taxable_value(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    @property
    def half_gst_rate(self):
        """The CGST (== SGST) rate, i.e. gst_rate / 2."""
        from decimal import Decimal
        return (self.gst_rate / Decimal('2')) if self.gst_rate else Decimal('0')

    @property
    def cgst_amount(self):
        from decimal import Decimal
        return (self.taxable_value * self.half_gst_rate / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def sgst_amount(self):
        return self.cgst_amount

    @property
    def line_total(self):
        return self.taxable_value + self.cgst_amount + self.sgst_amount


class AppSetting(models.Model):
    """Singleton row for app-wide settings. Currently holds the hashed password
    that gates the destructive "Clear All Data" action."""
    clear_data_password = models.CharField(max_length=256, blank=True, default='')  # hashed
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AppSetting (clear password {'set' if self.clear_data_password else 'unset'})"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def set_clear_password(self, raw):
        from django.contrib.auth.hashers import make_password
        self.clear_data_password = make_password(raw)

    def check_clear_password(self, raw):
        from django.contrib.auth.hashers import check_password
        if not self.clear_data_password:
            return False
        return check_password(raw, self.clear_data_password)

    @property
    def clear_password_is_set(self):
        return bool(self.clear_data_password)


