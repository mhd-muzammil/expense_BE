from decimal import Decimal

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
SECTION_CHALLAN = 'challan'
SECTION_PURCHASE = 'purchase'
SECTION_PORDER = 'porder'
SECTION_RECEIPT = 'receipt'
SECTION_PETTYCASH = 'pettycash'
SECTION_QUOTE = 'quote'
SECTION_BOS = 'bos'
SECTION_TAXINVOICE = 'taxinvoice'
SECTION_IDFC = 'idfc'
SECTION_BOB = 'bob'
SECTION_ENGPNL = 'engpnl'
SECTION_SBINVOICE = 'sbinvoice'
SECTION_SUBSCRIPTION = 'subscription'
SECTION_INSIGHTS = 'insights'

ALL_SECTIONS = [SECTION_DASHBOARD, SECTION_EXPENSES, SECTION_PNL, SECTION_REGION, SECTION_INVOICE, SECTION_CHALLAN, SECTION_PURCHASE, SECTION_PORDER, SECTION_RECEIPT, SECTION_PETTYCASH, SECTION_QUOTE, SECTION_BOS, SECTION_TAXINVOICE, SECTION_IDFC, SECTION_BOB, SECTION_ENGPNL, SECTION_SBINVOICE, SECTION_SUBSCRIPTION, SECTION_INSIGHTS]

SECTION_LABELS = {
    SECTION_DASHBOARD: 'Dashboard',
    SECTION_EXPENSES: 'Expenses',
    SECTION_PNL: 'Profit & Loss',
    SECTION_REGION: 'Region Expense',
    SECTION_INVOICE: 'Invoice',
    SECTION_CHALLAN: 'Delivery Challan',
    SECTION_PURCHASE: 'Purchase Bill',
    SECTION_PORDER: 'Purchase Order',
    SECTION_RECEIPT: 'Payment Receipt',
    SECTION_PETTYCASH: 'Petty Cash',
    SECTION_QUOTE: 'Quote',
    SECTION_BOS: 'Bill of Supply',
    SECTION_TAXINVOICE: 'Tax Invoice',
    SECTION_IDFC: 'IDFC Statement',
    SECTION_BOB: 'BOB Statement',
    SECTION_ENGPNL: 'Engineer P&L',
    SECTION_SBINVOICE: 'Invoice Register',
    SECTION_SUBSCRIPTION: 'Subscriptions',
    SECTION_INSIGHTS: 'Insights',
}

# Our own company's GST state code (Tamil Nadu). A supply to a different state
# code is inter-state (IGST); same state is intra-state (CGST + SGST).
COMPANY_STATE_CODE = '33'


def parse_state_code(place_of_supply):
    """Extract the numeric GST state code from a 'Place of Supply' string like
    'TN (33)' or 'KA (29)'. Returns the code as a string, or '' if not found."""
    import re
    m = re.search(r'(\d{2})', str(place_of_supply or ''))
    return m.group(1) if m else ''


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
    # Free-form: payment modes are user-configurable (see PaymentModeBalance),
    # so no fixed choices here. Kept as a denormalized string.
    credit_payment_mode = models.CharField(
        max_length=30,
        blank=True,
        default='',
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


class DeliveryChallan(models.Model):
    """A goods delivery challan. Like an invoice but WITHOUT any amounts — it
    records what items are shipped, to whom, when."""
    challan_number = models.CharField(max_length=60, unique=True, blank=True)

    # Bill To
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    customer_gstin = models.CharField(max_length=20, blank=True, default='')

    # Ship To (falls back to Bill To when blank)
    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    challan_date = models.DateField()
    shipping_date = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-challan_date', '-created_at']

    def __str__(self):
        return f"{self.challan_number} | {self.customer_name}"


class DeliveryChallanItem(models.Model):
    """A single line on a delivery challan — no price/amount, just qty."""
    challan = models.ForeignKey(DeliveryChallan, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class PurchaseBill(models.Model):
    """A purchase bill — goods/services bought FROM a vendor. Same GST maths as
    an invoice, but the counterparty is a Vendor (with PAN) and it records the
    vendor's own invoice number."""
    bill_number = models.CharField(max_length=60, unique=True, blank=True)

    # Vendor (the seller we bought from)
    vendor_name = models.CharField(max_length=200)
    vendor_phone = models.CharField(max_length=40, blank=True, default='')
    vendor_address = models.TextField(blank=True, default='')
    vendor_gstin = models.CharField(max_length=20, blank=True, default='')
    vendor_pan = models.CharField(max_length=20, blank=True, default='')
    vendor_invoice_number = models.CharField(max_length=60, blank=True, default='')

    # Ship To (defaults to our own company details on the frontend)
    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.bill_number} | {self.vendor_name}"

    # --- Derived amounts (mirror Invoice) ---
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
    def grand_total_raw(self):
        return self.taxable_total + self.cgst_total + self.sgst_total

    @property
    def grand_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        return self.grand_total - self.grand_total_raw


class PurchaseBillItem(models.Model):
    """A single line on a purchase bill (with price + GST, like an invoice)."""
    bill = models.ForeignKey(PurchaseBill, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    @property
    def taxable_value(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    @property
    def half_gst_rate(self):
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


class PurchaseOrder(models.Model):
    """A purchase order — issued to a vendor BEFORE buying. Same GST maths as a
    purchase bill, but has a 'Valid Until' date and no vendor invoice number."""
    order_number = models.CharField(max_length=60, unique=True, blank=True)

    # Vendor
    vendor_name = models.CharField(max_length=200)
    vendor_phone = models.CharField(max_length=40, blank=True, default='')
    vendor_address = models.TextField(blank=True, default='')
    vendor_gstin = models.CharField(max_length=20, blank=True, default='')
    vendor_pan = models.CharField(max_length=20, blank=True, default='')

    # Ship To (defaults to our own company on the frontend)
    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.order_number} | {self.vendor_name}"

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
    def grand_total_raw(self):
        return self.taxable_total + self.cgst_total + self.sgst_total

    @property
    def grand_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        return self.grand_total - self.grand_total_raw


class PurchaseOrderItem(models.Model):
    """A single line on a purchase order (with price + GST)."""
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    @property
    def taxable_value(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    @property
    def half_gst_rate(self):
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


class PaymentReceipt(models.Model):
    """A payment receipt — money RECEIVED from a customer against one or more
    documents (invoices). No GST; just document lines + payment amounts."""
    receipt_number = models.CharField(max_length=60, unique=True, blank=True)

    # Receipt To (the payer)
    receipt_to_name = models.CharField(max_length=200)
    receipt_to_phone = models.CharField(max_length=40, blank=True, default='')
    receipt_to_address = models.TextField(blank=True, default='')

    payment_date = models.DateField()
    payment_method = models.CharField(max_length=40, blank=True, default='Bank Transfer')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        return f"{self.receipt_number} | {self.receipt_to_name}"

    @property
    def amount_received(self):
        from decimal import Decimal
        return sum((line.payment_amount for line in self.lines.all()), Decimal('0.00'))


class PaymentReceiptLine(models.Model):
    """A single settled document on a payment receipt."""
    receipt = models.ForeignKey(PaymentReceipt, on_delete=models.CASCADE, related_name='lines')
    document_number = models.CharField(max_length=100, blank=True, default='')
    document_date = models.DateField(null=True, blank=True)
    document_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.document_number}: {self.payment_amount}"


class Quote(models.Model):
    """A price quotation issued to a customer. Same GST maths as an invoice, with
    a 'Valid Until' date and a 'Quote To' customer."""
    quote_number = models.CharField(max_length=60, unique=True, blank=True)

    # Quote To (the customer)
    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    customer_gstin = models.CharField(max_length=20, blank=True, default='')

    # Ship To (falls back to Quote To when blank)
    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.quote_number} | {self.customer_name}"

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
    def grand_total_raw(self):
        return self.taxable_total + self.cgst_total + self.sgst_total

    @property
    def grand_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        return self.grand_total - self.grand_total_raw


class QuoteItem(models.Model):
    """A single line on a quote (with price + GST)."""
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    @property
    def taxable_value(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    @property
    def half_gst_rate(self):
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


class BillOfSupply(models.Model):
    """A Bill of Supply — sale without GST (composition/exempt). Item table shows
    only Price and Amount (no tax columns)."""
    bos_number = models.CharField(max_length=60, unique=True, blank=True)

    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    customer_gstin = models.CharField(max_length=20, blank=True, default='')

    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.bos_number} | {self.customer_name}"

    @property
    def taxable_total(self):
        from decimal import Decimal
        return sum((item.amount for item in self.items.all()), Decimal('0.00'))

    @property
    def grand_total_raw(self):
        return self.taxable_total

    @property
    def grand_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        return self.grand_total - self.grand_total_raw


class BillOfSupplyItem(models.Model):
    """A single line on a bill of supply (price + amount, no GST)."""
    bos = models.ForeignKey(BillOfSupply, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    @property
    def amount(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))


class TaxInvoice(models.Model):
    """A GST tax invoice. Uses CGST+SGST for intra-state supplies and IGST for
    inter-state supplies (decided from the Place of Supply state code)."""
    ti_number = models.CharField(max_length=60, unique=True, blank=True)

    customer_name = models.CharField(max_length=200)
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    customer_address = models.TextField(blank=True, default='')
    customer_gstin = models.CharField(max_length=20, blank=True, default='')

    ship_to_name = models.CharField(max_length=200, blank=True, default='')
    ship_to_address = models.TextField(blank=True, default='')

    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    place_of_supply = models.CharField(max_length=100, blank=True, default='TN (33)')

    notes = models.TextField(blank=True, default='')
    terms = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-created_at']

    def __str__(self):
        return f"{self.ti_number} | {self.customer_name}"

    @property
    def is_inter_state(self):
        """True when the place-of-supply state differs from our company state →
        IGST applies. Falls back to intra-state if the code can't be parsed."""
        code = parse_state_code(self.place_of_supply)
        return bool(code) and code != COMPANY_STATE_CODE

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
    def igst_total(self):
        from decimal import Decimal
        return sum((item.igst_amount for item in self.items.all()), Decimal('0.00'))

    @property
    def grand_total_raw(self):
        return self.taxable_total + self.cgst_total + self.sgst_total + self.igst_total

    @property
    def grand_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        return self.grand_total_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    @property
    def rounded_off(self):
        return self.grand_total - self.grand_total_raw


class TaxInvoiceItem(models.Model):
    """A single line on a tax invoice. Tax is CGST+SGST (intra) or IGST (inter)."""
    invoice = models.ForeignKey(TaxInvoice, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=300)
    sub_description = models.TextField(blank=True, default='')
    hsn_sac = models.CharField(max_length=20, blank=True, default='')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=20, blank=True, default='')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return f"{self.description} x{self.quantity}"

    @property
    def taxable_value(self):
        from decimal import Decimal
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))

    @property
    def half_gst_rate(self):
        from decimal import Decimal
        return (self.gst_rate / Decimal('2')) if self.gst_rate else Decimal('0')

    @property
    def cgst_amount(self):
        from decimal import Decimal
        if self.invoice_id and self.invoice.is_inter_state:
            return Decimal('0.00')
        return (self.taxable_value * self.half_gst_rate / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def sgst_amount(self):
        return self.cgst_amount

    @property
    def igst_amount(self):
        from decimal import Decimal
        if self.invoice_id and self.invoice.is_inter_state:
            return (self.taxable_value * self.gst_rate / Decimal('100')).quantize(Decimal('0.01'))
        return Decimal('0.00')

    @property
    def line_total(self):
        return self.taxable_value + self.cgst_amount + self.sgst_amount + self.igst_amount


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


class BankStatementEntry(models.Model):
    """A single transaction row parsed from an uploaded bank statement.
    Two banks are supported as separate sections: IDFC FIRST Bank and Bank of
    Baroda (BOB). Rows are imported from the bank's own Excel/CSV export."""
    BANK_IDFC = 'idfc'
    BANK_BOB = 'bob'
    BANK_CHOICES = [(BANK_IDFC, 'IDFC FIRST Bank'), (BANK_BOB, 'Bank of Baroda')]

    bank = models.CharField(max_length=10, choices=BANK_CHOICES, db_index=True)
    txn_date = models.DateField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    narration = models.TextField(blank=True, default='')
    ref_no = models.CharField(max_length=150, blank=True, default='')
    debit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    # 'Cr' / 'Dr' indicator that banks print next to the running balance.
    balance_dc = models.CharField(max_length=2, blank=True, default='')
    source_file = models.CharField(max_length=255, blank=True, default='')
    # Fingerprint of the row (bank + date + narration + amounts + balance) used to
    # skip duplicates when the same statement file is uploaded more than once.
    row_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-txn_date', '-id']
        verbose_name_plural = 'Bank statement entries'

    def __str__(self):
        return f"{self.get_bank_display()} {self.txn_date} {self.narration[:30]}"


class EngineerPnl(models.Model):
    """Per-engineer profit & loss configuration for the live Engineer P&L board.

    The number of closed calls is pulled LIVE from the OpenCall system (matched
    by email, falling back to name); every other field here is an editable P&L
    parameter. All money figures are derived in `compute()` from the live
    closed-call count so the board reflects real-time profit/loss."""
    engineer_name = models.CharField(max_length=200)
    # Match key to the OpenCall system — email is unique & stable (name is a fallback).
    email = models.EmailField(blank=True, default='')
    engg_count = models.PositiveIntegerField(default=1)
    per_day_target = models.PositiveIntegerField(default=10)
    per_call_rate = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('350'))
    engg_salary = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('25000'))
    total_working_days = models.PositiveIntegerField(default=30)
    actual_working_days = models.PositiveIntegerField(default=25)
    active = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']

    def __str__(self):
        return self.engineer_name

    @property
    def per_day(self):
        if not self.total_working_days:
            return Decimal('0.00')
        return (self.engg_salary / self.total_working_days).quantize(Decimal('0.01'))

    def compute(self, closed_calls, period_days=None):
        """Live P&L figures for the window.

        Engg Earning = closed calls × per-call-rate.
        Profit/Loss  = Engg Earning − one-day salary (engg salary ÷ total working
        days). period_days is kept for call-signature compatibility but no longer
        changes the figures."""
        closed = Decimal(str(closed_calls or 0))
        awd = self.actual_working_days or 0
        actual_closed_pd = (closed / awd).quantize(Decimal('0.01')) if awd else Decimal('0.00')
        engg_earning = (closed * self.per_call_rate).quantize(Decimal('0.01'))
        one_day_salary = self.per_day
        profit_loss = (engg_earning - one_day_salary).quantize(Decimal('0.01'))
        return {
            'closed_calls': int(closed),
            'actual_closed_pd': str(actual_closed_pd),
            'total_calls_closed_pm': int(closed),
            'per_day': str(one_day_salary),
            'engg_earning': str(engg_earning),
            'revenue': str(engg_earning),               # alias (Engg Earning)
            'total_engg_salary': str(self.engg_salary),
            'profit_loss': str(profit_loss),
            'nett': str(profit_loss),                    # alias (Profit/Loss)
        }


class SleekBillInvoice(models.Model):
    """A single invoice imported from the Sleek Bill export (.xls). Mirrors the
    Sleek Bill invoice list so the Invoice Register section shows the same data.
    One row per invoice; re-importing updates existing rows by invoice_number."""
    TYPE_TAX = 'Tax Invoice'
    TYPE_BOS = 'Bill of Supply'

    invoice_number = models.CharField(max_length=80, unique=True, db_index=True)
    invoice_type = models.CharField(max_length=40, blank=True, default='', db_index=True)

    client_name = models.CharField(max_length=250, blank=True, default='')
    client_gstin = models.CharField(max_length=20, blank=True, default='')
    client_phone = models.CharField(max_length=40, blank=True, default='')
    client_email = models.CharField(max_length=150, blank=True, default='')
    client_city = models.CharField(max_length=100, blank=True, default='')
    client_state = models.CharField(max_length=100, blank=True, default='')
    creator_name = models.CharField(max_length=150, blank=True, default='')

    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    date_of_payment = models.DateField(null=True, blank=True)

    currency = models.CharField(max_length=10, blank=True, default='INR')
    amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)   # taxable value
    tax = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    status = models.CharField(max_length=30, blank=True, default='', db_index=True)
    dr_cr = models.CharField(max_length=5, blank=True, default='')

    cgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    payment_mode = models.CharField(max_length=60, blank=True, default='')
    payment_info = models.TextField(blank=True, default='')
    financial_year = models.CharField(max_length=20, blank=True, default='')

    source_file = models.CharField(max_length=255, blank=True, default='')
    imported_at = models.DateTimeField(auto_now=True)

    # The exact Sleek Bill invoice PDF, stored in the DB (no media volume needed).
    pdf_data = models.BinaryField(null=True, blank=True, editable=False)
    pdf_name = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-issue_date', '-id']
        verbose_name_plural = 'Sleek Bill invoices'

    def __str__(self):
        return f"{self.invoice_number} | {self.client_name}"

    @property
    def has_pdf(self):
        return self.pdf_data is not None and len(self.pdf_data) > 0


class Subscription(models.Model):
    """A recurring service subscription (Sleek Bill, hosting, domain, etc.) with
    a renewal date, so the app can flag ones expiring soon / expired and the
    user never misses a renewal."""
    CYCLE_CHOICES = [
        ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
        ('half_yearly', 'Half Yearly'), ('yearly', 'Yearly'), ('one_time', 'One Time'),
    ]

    name = models.CharField(max_length=200)
    vendor = models.CharField(max_length=200, blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cycle = models.CharField(max_length=20, choices=CYCLE_CHOICES, default='yearly')
    renewal_date = models.DateField(help_text='Next renewal / expiry date')
    reminder_days_before = models.PositiveIntegerField(default=7)
    auto_renew = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['renewal_date', 'id']

    def __str__(self):
        return f"{self.name} — {self.renewal_date}"

    @property
    def days_left(self):
        from datetime import date
        return (self.renewal_date - date.today()).days if self.renewal_date else None

    @property
    def status(self):
        d = self.days_left
        if d is None:
            return 'active'
        if d < 0:
            return 'expired'
        if d <= self.reminder_days_before:
            return 'expiring_soon'
        return 'active'


