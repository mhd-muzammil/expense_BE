from decimal import Decimal
from rest_framework import serializers
from .models import Branch, Expense, PaymentModeBalance, BillingReminder, PettyCashDebit, Invoice, InvoiceItem, DeliveryChallan, DeliveryChallanItem, PurchaseBill, PurchaseBillItem, PurchaseOrder, PurchaseOrderItem, PaymentReceipt, PaymentReceiptLine, Quote, QuoteItem, BillOfSupply, BillOfSupplyItem, TaxInvoice, TaxInvoiceItem, BankStatementEntry, EngineerPnl, SleekBillInvoice, Subscription


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
    # Reconciliation against the bank statements (set by ExpenseViewSet.list):
    # 'in_statement' / 'not_in_statement' for bank-mode entries, else None.
    statement_status = serializers.SerializerMethodField()
    # The bank-statement row this entry reconciled to (null when not matched).
    matched_statement = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            'id', 'date', 'category', 'branch', 'branch_location',
            'credited_amount', 'credit_remark', 'credit_person', 'credit_payment_mode',
            'debited_amount', 'debit_remark', 'debit_person', 'debit_payment_mode',
            'running_balances', 'statement_status', 'matched_statement', 'created_at',
        ]

    def get_statement_status(self, obj):
        return getattr(obj, 'statement_status', None)

    def get_matched_statement(self, obj):
        return getattr(obj, 'matched_statement', None)

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


def next_document_number(model, field, prefix, base):
    """Return the next 'prefix{seq}' document number, where seq is (the highest
    existing numeric suffix for that prefix) + 1, starting at `base` when none
    exist. Uses a NUMERIC max (parsed suffix) rather than a lexicographic string
    sort, so it stays correct across the 999->1000 boundary (a plain
    order_by('-field') would rank 'PUR-999' above 'PUR-1000' and collide)."""
    existing = model.objects.filter(**{f'{field}__startswith': prefix}).values_list(field, flat=True)
    max_seq = base - 1
    for num in existing:
        try:
            s = int(str(num).rsplit('-', 1)[-1])
        except (ValueError, IndexError):
            continue
        if s > max_seq:
            max_seq = s
    return f"{prefix}{max_seq + 1}"


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
        """Generate 'RT{fy}-REN-{seq}' e.g. RT25-26-REN-2472."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(Invoice, 'invoice_number', f"RT{fy}-REN-", 2472)


# ---------------------------------------------------------------------------
# Delivery Challan
# ---------------------------------------------------------------------------
class DeliveryChallanItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryChallanItem
        fields = ['id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom', 'position']


class DeliveryChallanSerializer(serializers.ModelSerializer):
    items = DeliveryChallanItemSerializer(many=True)

    class Meta:
        model = DeliveryChallan
        fields = [
            'id', 'challan_number',
            'customer_name', 'customer_phone', 'customer_address', 'customer_gstin',
            'ship_to_name', 'ship_to_address',
            'challan_date', 'shipping_date', 'place_of_supply', 'notes', 'terms',
            'items', 'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'challan_number': {'required': False, 'allow_blank': True}}

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('challan_number'):
            validated_data['challan_number'] = self._next_challan_number()
        challan = DeliveryChallan.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            DeliveryChallanItem.objects.create(challan=challan, **item)
        return challan

    def _next_challan_number(self):
        """Generate 'RT/{fy}/OTW/RPL-{seq}' e.g. RT/25-26/OTW/RPL-5302."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(DeliveryChallan, 'challan_number', f"RT/{fy}/OTW/RPL-", 5302)


# ---------------------------------------------------------------------------
# Purchase Bill
# ---------------------------------------------------------------------------
class PurchaseBillItemSerializer(serializers.ModelSerializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    half_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseBillItem
        fields = [
            'id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom',
            'unit_price', 'gst_rate', 'position',
            'taxable_value', 'cgst_amount', 'sgst_amount', 'half_gst_rate', 'line_total',
        ]


class PurchaseBillSerializer(serializers.ModelSerializer):
    items = PurchaseBillItemSerializer(many=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseBill
        fields = [
            'id', 'bill_number',
            'vendor_name', 'vendor_phone', 'vendor_address', 'vendor_gstin', 'vendor_pan', 'vendor_invoice_number',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'due_date', 'place_of_supply', 'notes', 'terms',
            'items',
            'taxable_total', 'cgst_total', 'sgst_total',
            'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'bill_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('bill_number'):
            validated_data['bill_number'] = self._next_bill_number()
        bill = PurchaseBill.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            PurchaseBillItem.objects.create(bill=bill, **item)
        return bill

    def _next_bill_number(self):
        """Generate 'RT{fy}/PUR-{seq}' e.g. RT25-26/PUR-5074."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(PurchaseBill, 'bill_number', f"RT{fy}/PUR-", 5074)


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------
class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    half_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = [
            'id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom',
            'unit_price', 'gst_rate', 'position',
            'taxable_value', 'cgst_amount', 'sgst_amount', 'half_gst_rate', 'line_total',
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'order_number',
            'vendor_name', 'vendor_phone', 'vendor_address', 'vendor_gstin', 'vendor_pan',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'valid_until', 'place_of_supply', 'notes', 'terms',
            'items',
            'taxable_total', 'cgst_total', 'sgst_total',
            'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'order_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('order_number'):
            validated_data['order_number'] = self._next_order_number()
        order = PurchaseOrder.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            PurchaseOrderItem.objects.create(order=order, **item)
        return order

    def _next_order_number(self):
        """Generate 'RT/{fy}/PUR-{seq}' e.g. RT/25-26/PUR-444."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(PurchaseOrder, 'order_number', f"RT/{fy}/PUR-", 444)


# ---------------------------------------------------------------------------
# Payment Receipt
# ---------------------------------------------------------------------------
class PaymentReceiptLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentReceiptLine
        fields = ['id', 'document_number', 'document_date', 'document_amount', 'payment_amount', 'position']


class PaymentReceiptSerializer(serializers.ModelSerializer):
    lines = PaymentReceiptLineSerializer(many=True)
    amount_received = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = PaymentReceipt
        fields = [
            'id', 'receipt_number',
            'receipt_to_name', 'receipt_to_phone', 'receipt_to_address',
            'payment_date', 'payment_method', 'notes', 'terms',
            'lines', 'amount_received', 'amount_in_words', 'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'receipt_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.amount_received)

    def create(self, validated_data):
        lines_data = validated_data.pop('lines', [])
        if not validated_data.get('receipt_number'):
            validated_data['receipt_number'] = self._next_receipt_number()
        receipt = PaymentReceipt.objects.create(**validated_data)
        for idx, line in enumerate(lines_data):
            line.setdefault('position', idx)
            PaymentReceiptLine.objects.create(receipt=receipt, **line)
        return receipt

    def _next_receipt_number(self):
        """Generate 'RT/{fy}/SER-{seq}' e.g. RT/26-27/SER-2817."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(PaymentReceipt, 'receipt_number', f"RT/{fy}/SER-", 2817)


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------
class QuoteItemSerializer(serializers.ModelSerializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    half_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = QuoteItem
        fields = [
            'id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom',
            'unit_price', 'gst_rate', 'position',
            'taxable_value', 'cgst_amount', 'sgst_amount', 'half_gst_rate', 'line_total',
        ]


class QuoteSerializer(serializers.ModelSerializer):
    items = QuoteItemSerializer(many=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            'id', 'quote_number',
            'customer_name', 'customer_phone', 'customer_address', 'customer_gstin',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'valid_until', 'place_of_supply', 'notes', 'terms',
            'items',
            'taxable_total', 'cgst_total', 'sgst_total',
            'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'quote_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('quote_number'):
            validated_data['quote_number'] = self._next_quote_number()
        quote = Quote.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            QuoteItem.objects.create(quote=quote, **item)
        return quote

    def _next_quote_number(self):
        """Generate 'RT{fy}/QEN-{seq}' e.g. RT26-27/QEN-2646."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(Quote, 'quote_number', f"RT{fy}/QEN-", 2646)


# ---------------------------------------------------------------------------
# Bill of Supply (no GST)
# ---------------------------------------------------------------------------
class BillOfSupplyItemSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = BillOfSupplyItem
        fields = ['id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom', 'unit_price', 'position', 'amount']


class BillOfSupplySerializer(serializers.ModelSerializer):
    items = BillOfSupplyItemSerializer(many=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = BillOfSupply
        fields = [
            'id', 'bos_number',
            'customer_name', 'customer_phone', 'customer_address', 'customer_gstin',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'due_date', 'place_of_supply', 'notes', 'terms',
            'items', 'taxable_total', 'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'bos_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('bos_number'):
            validated_data['bos_number'] = self._next_bos_number()
        bos = BillOfSupply.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            BillOfSupplyItem.objects.create(bos=bos, **item)
        return bos

    def _next_bos_number(self):
        """Generate 'RT{fy}-REN-{seq}' e.g. RT26-27-REN-2487."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(BillOfSupply, 'bos_number', f"RT{fy}-REN-", 2487)


# ---------------------------------------------------------------------------
# Tax Invoice (CGST+SGST intra-state / IGST inter-state)
# ---------------------------------------------------------------------------
class TaxInvoiceItemSerializer(serializers.ModelSerializer):
    taxable_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    igst_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    half_gst_rate = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = TaxInvoiceItem
        fields = [
            'id', 'description', 'sub_description', 'hsn_sac', 'quantity', 'uom',
            'unit_price', 'gst_rate', 'position',
            'taxable_value', 'cgst_amount', 'sgst_amount', 'igst_amount', 'half_gst_rate', 'line_total',
        ]


class TaxInvoiceSerializer(serializers.ModelSerializer):
    items = TaxInvoiceItemSerializer(many=True)
    is_inter_state = serializers.BooleanField(read_only=True)
    taxable_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    cgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    sgst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    igst_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    grand_total_raw = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    rounded_off = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_in_words = serializers.SerializerMethodField()

    class Meta:
        model = TaxInvoice
        fields = [
            'id', 'ti_number',
            'customer_name', 'customer_phone', 'customer_address', 'customer_gstin',
            'ship_to_name', 'ship_to_address',
            'issue_date', 'due_date', 'place_of_supply', 'notes', 'terms',
            'items', 'is_inter_state',
            'taxable_total', 'cgst_total', 'sgst_total', 'igst_total',
            'grand_total', 'grand_total_raw', 'rounded_off', 'amount_in_words',
            'created_at',
        ]
        read_only_fields = ['created_at']
        extra_kwargs = {'ti_number': {'required': False, 'allow_blank': True}}

    def get_amount_in_words(self, obj):
        return amount_in_words_inr(obj.grand_total)

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        if not validated_data.get('ti_number'):
            validated_data['ti_number'] = self._next_ti_number()
        invoice = TaxInvoice.objects.create(**validated_data)
        for idx, item in enumerate(items_data):
            item.setdefault('position', idx)
            TaxInvoiceItem.objects.create(invoice=invoice, **item)
        return invoice

    def _next_ti_number(self):
        """Generate 'RT{fy}-SER-{seq}' e.g. RT26-27-SER-12."""
        from datetime import date
        today = date.today()
        fy_start = today.year if today.month >= 4 else today.year - 1
        fy = f"{str(fy_start)[-2:]}-{str(fy_start + 1)[-2:]}"
        return next_document_number(TaxInvoice, 'ti_number', f"RT{fy}-SER-", 12)


class BankStatementEntrySerializer(serializers.ModelSerializer):
    """Read-only-ish view of one imported bank-statement transaction row."""
    bank_display = serializers.CharField(source='get_bank_display', read_only=True)

    class Meta:
        model = BankStatementEntry
        fields = [
            'id', 'bank', 'bank_display', 'txn_date', 'value_date', 'narration',
            'ref_no', 'debit', 'credit', 'balance', 'balance_dc', 'source_file', 'uploaded_at',
        ]
        read_only_fields = ['uploaded_at']


class EngineerPnlSerializer(serializers.ModelSerializer):
    """CRUD for a single engineer's P&L parameters. The live closed-call count
    and derived money figures are added by the viewset's /board/ action."""
    per_day = serializers.SerializerMethodField()

    class Meta:
        model = EngineerPnl
        fields = [
            'id', 'engineer_name', 'email', 'engg_count', 'per_day_target',
            'per_call_rate', 'engg_salary', 'total_working_days', 'actual_working_days',
            'active', 'position', 'per_day', 'created_at',
        ]
        read_only_fields = ['created_at']

    def get_per_day(self, obj):
        return str(obj.per_day)


class SleekBillInvoiceSerializer(serializers.ModelSerializer):
    """One imported Sleek Bill invoice row (mirrors the Sleek Bill list)."""
    has_pdf = serializers.BooleanField(read_only=True)

    class Meta:
        model = SleekBillInvoice
        fields = [
            'id', 'invoice_number', 'invoice_type',
            'client_name', 'client_gstin', 'client_phone', 'client_email',
            'client_city', 'client_state', 'creator_name',
            'issue_date', 'due_date', 'date_of_payment',
            'currency', 'amount', 'tax', 'total', 'amount_paid', 'balance',
            'status', 'dr_cr', 'cgst', 'sgst', 'igst',
            'payment_mode', 'payment_info', 'financial_year',
            'source_file', 'imported_at', 'has_pdf',
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    """A tracked service subscription with computed days-left and status."""
    days_left = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'name', 'vendor', 'amount', 'cycle', 'renewal_date',
            'reminder_days_before', 'auto_renew', 'notes', 'active',
            'days_left', 'status', 'created_at',
        ]
        read_only_fields = ['created_at']

    def get_days_left(self, obj):
        return obj.days_left

    def get_status(self, obj):
        return obj.status

