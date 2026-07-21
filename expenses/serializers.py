from decimal import Decimal
from rest_framework import serializers
from .models import Branch, Expense, PaymentModeBalance, BillingReminder, PettyCashDebit, Invoice, InvoiceItem


class BranchSerializer(serializers.ModelSerializer):
    current_balance = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Branch
        fields = ['id', 'location', 'current_balance', 'created_at']


class ExpenseSerializer(serializers.ModelSerializer):
    branch_location = serializers.CharField(source='branch.location', read_only=True)
    running_balances = serializers.JSONField(read_only=True, required=False)

    class Meta:
        model = Expense
        fields = [
            'id', 'date', 'category', 'branch', 'branch_location',
            'credited_amount', 'credit_remark', 'credit_person', 'credit_payment_mode',
            'debited_amount', 'debit_remark', 'debit_person', 'debit_payment_mode',
            'running_balances', 'created_at',
        ]

    def validate(self, data):
        """Ensure at least one of credit or debit is provided."""
        credit = data.get('credited_amount')
        debit = data.get('debited_amount')

        if not credit and not debit:
            raise serializers.ValidationError(
                "Either credited_amount or debited_amount must be provided."
            )

        if credit is not None and credit < 0:
            raise serializers.ValidationError(
                {"credited_amount": "Amount must be positive."}
            )

        if debit is not None and debit < 0:
            raise serializers.ValidationError(
                {"debited_amount": "Amount must be positive."}
            )

        return data


class ExpenseCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating expenses."""

    branch = serializers.CharField()
    
    class Meta:
        model = Expense
        fields = [
            'id', 'date', 'category', 'branch',
            'credited_amount', 'credit_remark', 'credit_person', 'credit_payment_mode',
            'debited_amount', 'debit_remark', 'debit_person', 'debit_payment_mode',
        ]

    def validate_branch(self, value):
        """Find or create branch by location name."""
        if not value:
            raise serializers.ValidationError("Branch location is required.")
        branch, _ = Branch.objects.get_or_create(location=value)
        return branch

    def validate(self, data):
        credit = data.get('credited_amount')
        debit = data.get('debited_amount')

        if not credit and not debit:
            raise serializers.ValidationError(
                "Either credited_amount or debited_amount must be provided."
            )

        if credit is not None and credit < 0:
            raise serializers.ValidationError(
                {"credited_amount": "Amount must be positive."}
            )

        if debit is not None and debit < 0:
            raise serializers.ValidationError(
                {"debited_amount": "Amount must be positive."}
            )

        if debit is not None and debit > 0:
            mode = data.get('debit_payment_mode') or ''
            if self.instance and not mode:
                mode = self.instance.debit_payment_mode or ''
                
            if mode:
                from .models import PaymentModeBalance
                from django.db.models import Sum
                from django.db.models.functions import Coalesce
                
                try:
                    bal = PaymentModeBalance.objects.get(payment_mode=mode)
                    initial = bal.initial_balance
                except PaymentModeBalance.DoesNotExist:
                    initial = Decimal('0.00')

                total_credits = Expense.objects.filter(credit_payment_mode=mode).aggregate(
                    t=Coalesce(Sum('credited_amount'), Decimal('0.00'))
                )['t']
                total_debits = Expense.objects.filter(debit_payment_mode=mode).aggregate(
                    t=Coalesce(Sum('debited_amount'), Decimal('0.00'))
                )['t']

                current_balance = initial + total_credits - total_debits
                if self.instance and self.instance.debit_payment_mode == mode:
                    current_balance += (self.instance.debited_amount or Decimal('0.00'))

                # if current_balance < debit:
                #     raise serializers.ValidationError(
                #         f"Insufficient funds! You have only \u20b9{current_balance:,.2f} balance in {mode}."
                #     )

        return data


class PaymentModeBalanceSerializer(serializers.ModelSerializer):
    current_balance = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, required=False
    )
    total_credits = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, required=False
    )
    total_debits = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, required=False
    )
    period_available = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True, required=False
    )

    class Meta:
        model = PaymentModeBalance
        fields = ['id', 'payment_mode', 'initial_balance', 'current_balance', 'total_credits', 'total_debits', 'period_available']


class BillingReminderSerializer(serializers.ModelSerializer):
    branch_location = serializers.CharField(source='branch.location', read_only=True)

    class Meta:
        model = BillingReminder
        fields = [
            'id', 'title', 'amount', 'due_day', 'frequency',
            'category', 'notes', 'is_paid', 'next_due_date',
            'branch', 'branch_location',
            'created_at', 'updated_at',
        ]


class PettyCashDebitSerializer(serializers.ModelSerializer):
    branch = serializers.CharField()
    branch_location = serializers.CharField(source='branch.location', read_only=True)

    class Meta:
        model = PettyCashDebit
        fields = [
            'id', 'date', 'amount', 'remark', 'person', 'branch', 'branch_location', 'created_at'
        ]

    def validate_branch(self, value):
        if not value:
            raise serializers.ValidationError("Branch location is required.")
        branch, _ = Branch.objects.get_or_create(location=value)
        return branch


# ---------------------------------------------------------------------------
# Invoicing
# ---------------------------------------------------------------------------

def amount_in_words_inr(amount):
    """Convert a numeric rupee amount to Indian-English words, e.g.
    3480 -> 'Three Thousand Four Hundred Eighty'. Paise are ignored (invoices
    round to the rupee)."""
    n = int(round(float(amount)))
    if n == 0:
        return 'Zero'

    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
            'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
            'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def two(num):
        if num < 20:
            return ones[num]
        return (tens[num // 10] + (' ' + ones[num % 10] if num % 10 else '')).strip()

    def three(num):
        h = num // 100
        rest = num % 100
        parts = []
        if h:
            parts.append(ones[h] + ' Hundred')
        if rest:
            parts.append(two(rest))
        return ' '.join(parts)

    # Indian numbering: crore, lakh, thousand, hundred.
    crore = n // 10000000
    n %= 10000000
    lakh = n // 100000
    n %= 100000
    thousand = n // 1000
    n %= 1000
    rest = n

    parts = []
    if crore:
        parts.append(three(crore) + ' Crore')
    if lakh:
        parts.append(two(lakh) + ' Lakh')
    if thousand:
        parts.append(two(thousand) + ' Thousand')
    if rest:
        parts.append(three(rest))
    return ' '.join(parts).strip()


class InvoiceItemSerializer(serializers.ModelSerializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    half_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = InvoiceItem
        fields = [
            'id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom',
            'unit_price', 'gst_rate', 'position',
            'taxable_value', 'cgst_amount', 'sgst_amount', 'half_gst_rate', 'line_total',
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True)
    resolved_doc_type = serializers.CharField(read_only=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'doc_type', 'resolved_doc_type',
            'customer_name', 'customer_phone', 'customer_address', 'customer_gstin',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'due_date', 'place_of_supply', 'contact_name', 'terms', 'notes',
            'items',
            'taxable_total', 'cgst_total', 'sgst_total',
            'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['invoice_number', 'created_at']

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        # Generate the next invoice number if not supplied.
        validated_data['invoice_number'] = self._next_invoice_number()
        invoice = Invoice.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            InvoiceItem.objects.create(invoice=invoice, **item)
        return invoice

    def _next_invoice_number(self):
        """Generate 'RT{fy}-REN-{seq}' e.g. RT25-26-REN-2472, incrementing from
        the highest existing sequence."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        prefix = f"RT{fy}-REN-"
        last = (
            Invoice.objects.filter(invoice_number__startswith=prefix)
            .order_by('-invoice_number')
            .values_list('invoice_number', flat=True)
            .first()
        )
        seq = 1
        if last:
            try:
                seq = int(last.rsplit('-', 1)[-1]) + 1
            except (ValueError, IndexError):
                seq = Invoice.objects.count() + 1
        else:
            # Continue from a sensible base so numbers look established.
            seq = 2472
        return f"{prefix}{seq}"

