from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from customers.models import Customer
from suppliers.models import Supplier


class TaxRate(models.Model):
    """
    A named tax percentage, maintained from the admin.

    Products point at one of these instead of anybody typing a tax amount by
    hand, which is what previously allowed the same tax to be entered twice on
    one invoice.
    """
    code = models.CharField(_("Code"), max_length=20, unique=True)
    name = models.CharField(_("Name"), max_length=200)

    rate = models.DecimalField(
        _("Tax Rate"),
        max_digits=6,
        decimal_places=3,
        default=0,
        help_text=_("Percentage, for example 16.000 for 16%."),
    )

    subject_to_tax = models.BooleanField(
        _("Subject To Tax"),
        default=True,
        help_text=_("Clear this for exempt items so no tax is calculated."),
    )

    is_active = models.BooleanField(_("Is Active"), default=True)

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("Tax Rate")
        verbose_name_plural = _("Tax Rates")
        ordering = ("code",)

    def __str__(self):
        return f"{self.name} ({self.rate}%)"

    def clean(self):
        if self.rate < 0:
            raise ValidationError({"rate": _("Tax rate cannot be negative.")})
        if self.rate > 100:
            raise ValidationError({"rate": _("Tax rate cannot be greater than 100%.")})

    def tax_for(self, amount):
        """Tax due on `amount`, rounded to three decimals. Exempt returns zero."""
        from decimal import Decimal, ROUND_HALF_UP

        if not self.subject_to_tax or not self.rate:
            return Decimal("0.000")

        return (Decimal(amount) * Decimal(self.rate) / Decimal("100")).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )

class PeriodStatus(models.TextChoices):
    OPEN = "open", _("Open")
    CLOSED = "closed", _("Closed")


class FiscalYear(models.Model):
    year = models.PositiveSmallIntegerField(_("Year"), unique=True)
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=PeriodStatus.choices,
        default=PeriodStatus.OPEN,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Closed By"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_fiscal_years",
    )
    closed_at = models.DateTimeField(_("Closed At"), null=True, blank=True)
    close_reason = models.CharField(
        _("Close Reason"),
        max_length=255,
        blank=True,
    )
    notes = models.TextField(_("Notes"), blank=True)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        ordering = ("-year",)
        verbose_name = _("Fiscal Year")
        verbose_name_plural = _("Fiscal Years")

    def __str__(self):
        return str(self.year)


class FiscalPeriod(models.Model):
    fiscal_year = models.ForeignKey(
        FiscalYear,
        verbose_name=_("Fiscal Year"),
        on_delete=models.CASCADE,
        related_name="periods",
    )
    month = models.PositiveSmallIntegerField(_("Month"))
    start_date = models.DateField(_("Start Date"))
    end_date = models.DateField(_("End Date"))
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=PeriodStatus.choices,
        default=PeriodStatus.OPEN,
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Closed By"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="closed_fiscal_periods",
    )
    closed_at = models.DateTimeField(_("Closed At"), null=True, blank=True)
    close_reason = models.CharField(
        _("Close Reason"),
        max_length=255,
        blank=True,
    )
    notes = models.TextField(_("Notes"), blank=True)

    class Meta:
        ordering = ("fiscal_year__year", "month")
        constraints = [
            models.UniqueConstraint(
                fields=("fiscal_year", "month"),
                name="unique_month_per_fiscal_year",
            ),
            models.CheckConstraint(
                condition=models.Q(month__gte=1, month__lte=12),
                name="fiscal_period_month_1_to_12",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="fiscal_period_valid_date_range",
            ),
        ]

    def __str__(self):
        return f"{self.fiscal_year.year}-{self.month:02d}"


class FiscalPeriodAction(models.Model):
    class Action(models.TextChoices):
        OPENED = "opened", _("Opened")
        CLOSED = "closed", _("Closed")

    fiscal_year = models.ForeignKey(
        FiscalYear,
        verbose_name=_("Fiscal Year"),
        on_delete=models.PROTECT,
        related_name="actions",
    )
    period = models.ForeignKey(
        FiscalPeriod,
        verbose_name=_("Fiscal Period"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="actions",
        help_text=_("Empty for an action on the complete fiscal year."),
    )
    action = models.CharField(
        _("Action"),
        max_length=10,
        choices=Action.choices,
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Performed By"),
        on_delete=models.PROTECT,
        related_name="fiscal_period_actions",
    )
    performed_at = models.DateTimeField(_("Performed At"), auto_now_add=True)
    reason = models.TextField(_("Reason"), blank=True)

    class Meta:
        ordering = ("-performed_at", "-id")
        verbose_name = _("Fiscal Period Action")
        verbose_name_plural = _("Fiscal Period Actions")

    def __str__(self):
        target = self.period or self.fiscal_year
        return f"{target} - {self.get_action_display()}"


class FinanceAuditLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", _("Created")
        UPDATED = "updated", _("Updated")
        POSTED = "posted", _("Posted")
        REVERSED = "reversed", _("Reversed")
        CLOSED = "closed", _("Closed")
        REOPENED = "reopened", _("Reopened")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("User"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finance_audit_logs",
    )
    actor_label = models.CharField(_("User snapshot"), max_length=150, blank=True)
    action = models.CharField(_("Action"), max_length=20, choices=Action.choices)
    entity_type = models.CharField(_("Record type"), max_length=80, db_index=True)
    entity_label = models.CharField(_("Record type label"), max_length=120)
    object_id = models.CharField(_("Record ID"), max_length=64, db_index=True)
    object_repr = models.CharField(_("Record"), max_length=255)
    changes = models.JSONField(_("Changes"), default=dict, blank=True)
    created_at = models.DateTimeField(_("Date and time"), auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = _("Finance Audit Log")
        verbose_name_plural = _("Finance Audit Logs")

    def __str__(self):
        return f"{self.get_action_display()} - {self.object_repr}"


class Account(models.Model):
    class AccountType(models.TextChoices):
        ASSET = "asset", _("Asset")
        LIABILITY = "liability", _("Liability")
        EQUITY = "equity", _("Equity")
        REVENUE = "revenue", _("Revenue")
        EXPENSE = "expense", _("Expense")

    code = models.CharField(_("Code"), max_length=20, unique=True)
    name = models.CharField(_("Name"), max_length=200)

    account_type = models.CharField(
        _("Account Type"),
        max_length=20,
        choices=AccountType.choices,
    )

    parent = models.ForeignKey(
        "self",
        verbose_name=_("Parent Account"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )

    allow_posting = models.BooleanField(_("Allow Posting"), default=True)
    is_active = models.BooleanField(_("Is Active"), default=True)
    is_cash_equivalent = models.BooleanField(
        _("Cash or Cash Equivalent"),
        default=False,
        help_text=_("Include this account in the cash-flow statement."),
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("Account")
        verbose_name_plural = _("Accounts")

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        REVERSED = "reversed", _("Reversed")

    class SourceType(models.TextChoices):
        MANUAL = "manual", _("Manual")
        PURCHASE = "purchase", _("Purchase")
        POS_SALE = "pos_sale", _("POS Sale")
        POS_SESSION = "pos_session", _("POS Register Session")
        RECEIPT = "receipt", _("Receipt Voucher")
        PAYMENT = "payment", _("Payment Voucher")
        WASTE = "waste", _("Waste & Loss")
        STOCK_ADJUSTMENT = "stock_adjustment", _("Stock Adjustment")
        SALES_RETURN = "sales_return", _("Sales Return")
        REVERSAL = "reversal", _("Journal Reversal")

    class CashFlowActivity(models.TextChoices):
        NONE = "none", _("No Cash-Flow Effect")
        OPERATING = "operating", _("Operating Activity")
        INVESTING = "investing", _("Investing Activity")
        FINANCING = "financing", _("Financing Activity")

    entry_number = models.CharField(
        _("Entry Number"),
        max_length=30,
        unique=True,
    )

    date = models.DateField(_("Date"))

    description = models.TextField(_("Description"), blank=True)

    source_type = models.CharField(
        _("Source Type"),
        max_length=30,
        choices=SourceType.choices,
        default=SourceType.MANUAL,
    )

    source_id = models.PositiveBigIntegerField(
        _("Source ID"),
        null=True,
        blank=True,
    )

    cash_flow_activity = models.CharField(
        _("Cash-Flow Activity"),
        max_length=20,
        choices=CashFlowActivity.choices,
        default=CashFlowActivity.NONE,
        help_text=_("Required when this journal changes cash or bank balances."),
    )

    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.PROTECT,
        related_name="created_journal_entries",
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Approved By"),
        on_delete=models.PROTECT,
        related_name="approved_journal_entries",
        null=True,
        blank=True,
    )
    reversal_of = models.OneToOneField(
        "self",
        verbose_name=_("Reversal Of"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entry",
    )

    reversal_reason = models.TextField(
        _("Reversal Reason"),
        blank=True,
    )

    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Reversed By"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_journal_entries",
    )

    reversed_at = models.DateTimeField(
        _("Reversed At"),
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("Journal Entry")
        verbose_name_plural = _("Journal Entries")
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "source_id"],
                condition=Q(source_id__isnull=False),
                name="unique_journal_source",
            ),
        ]

    def __str__(self):
        return self.entry_number


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(
        JournalEntry,
        verbose_name=_("Journal Entry"),
        on_delete=models.CASCADE,
        related_name="lines",
    )

    account = models.ForeignKey(
        Account,
        verbose_name=_("Account"),
        on_delete=models.PROTECT,
        related_name="journal_lines",
    )

    customer = models.ForeignKey(
        Customer,
        verbose_name=_("Customer"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="journal_lines",
    )

    supplier = models.ForeignKey(
        Supplier,
        verbose_name=_("Supplier"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="journal_lines",
    )

    description = models.CharField(
        _("Description"),
        max_length=255,
        blank=True,
    )

    debit = models.DecimalField(
        _("Debit"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    credit = models.DecimalField(
        _("Credit"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Journal Entry Line")
        verbose_name_plural = _("Journal Entry Lines")

    def clean(self):
        super().clean()

        if self.debit < 0:
            raise ValidationError({
                "debit": _("Debit cannot be negative.")
            })

        if self.credit < 0:
            raise ValidationError({
                "credit": _("Credit cannot be negative.")
            })

        if self.debit > 0 and self.credit > 0:
            raise ValidationError(
                _("A journal line cannot contain both debit and credit.")
            )

        if self.debit == 0 and self.credit == 0:
            raise ValidationError(
                _("A journal line must contain a debit or credit amount.")
            )

        if not self.account.allow_posting:
            raise ValidationError({
                "account": _("This account does not allow direct posting.")
            })

        if not self.account.is_active:
            raise ValidationError({
                "account": _("This account is inactive.")
            })

        if self.customer_id and self.supplier_id:
            raise ValidationError(
                _("A journal line cannot reference both a customer and a supplier.")
            )

    def __str__(self):
        return f"{self.journal_entry.entry_number} - {self.account}"


class OpenItem(models.Model):
    class ItemType(models.TextChoices):
        RECEIVABLE = "receivable", _("Receivable")
        PAYABLE = "payable", _("Payable")

    item_type = models.CharField(max_length=20, choices=ItemType.choices)
    customer = models.ForeignKey(
        Customer, null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="open_items",
    )
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="open_items",
    )
    journal_line = models.OneToOneField(
        JournalEntryLine,
        on_delete=models.PROTECT,
        related_name="open_item",
    )
    document_number = models.CharField(max_length=100)
    document_date = models.DateField()
    due_date = models.DateField()
    original_amount = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        ordering = ["due_date", "document_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(item_type="receivable", customer__isnull=False, supplier__isnull=True)
                    | Q(item_type="payable", customer__isnull=True, supplier__isnull=False)
                ),
                name="open_item_has_correct_party",
            ),
            models.CheckConstraint(
                condition=Q(original_amount__gt=0),
                name="open_item_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.item_type == self.ItemType.RECEIVABLE:
            if not self.customer_id or self.supplier_id:
                raise ValidationError(_("A receivable must reference only a customer."))
        elif self.item_type == self.ItemType.PAYABLE:
            if not self.supplier_id or self.customer_id:
                raise ValidationError(_("A payable must reference only a supplier."))
        if self.original_amount <= 0:
            raise ValidationError(_("Open-item amount must be greater than zero."))

    def __str__(self):
        return f"{self.document_number} - {self.original_amount}"


class OpenItemAllocation(models.Model):
    open_item = models.ForeignKey(
        OpenItem,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    journal_line = models.ForeignKey(
        JournalEntryLine,
        on_delete=models.PROTECT,
        related_name="open_item_allocations",
    )
    allocation_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=3)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_open_item_allocations",
    )

    class Meta:
        ordering = ["allocation_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="open_item_allocation_amount_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError(_("Allocation amount must be greater than zero."))
        if self.open_item_id and self.allocation_date < self.open_item.document_date:
            raise ValidationError(_("Allocation date cannot precede the document date."))

    def __str__(self):
        return f"{self.open_item.document_number} - {self.amount}"


class ReceiptVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")

    class PaymentMethod(models.TextChoices):
        CASH = "cash", _("Cash")
        CARD = "card", _("Card")
        BANK = "bank", _("Bank")

    voucher_number = models.CharField(
        _("Voucher Number"),
        max_length=30,
        unique=True,
    )

    date = models.DateField(_("Date"))

    customer = models.ForeignKey(
        "customers.Customer",
        verbose_name=_("Customer"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipt_vouchers",
    )

    received_from = models.CharField(
        _("Received From"),
        max_length=200,
    )

    account = models.ForeignKey(
        Account,
        verbose_name=_("Account"),
        on_delete=models.PROTECT,
        related_name="receipt_vouchers",
    )

    amount = models.DecimalField(
        _("Amount"),
        max_digits=14,
        decimal_places=3,
    )

    payment_method = models.CharField(
        _("Payment Method"),
        max_length=20,
        choices=PaymentMethod.choices,
    )

    reference = models.CharField(
        _("Reference"),
        max_length=100,
        blank=True,
    )

    description = models.TextField(
        _("Description"),
        blank=True,
    )

    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.PROTECT,
        related_name="created_receipt_vouchers",
    )

    created_at = models.DateTimeField(
        _("Created At"),
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        _("Updated At"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Receipt Voucher")
        verbose_name_plural = _("Receipt Vouchers")

    def __str__(self):
        return self.voucher_number


class PaymentVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")

    class PaymentMethod(models.TextChoices):
        CASH = "cash", _("Cash")
        CARD = "card", _("Card")
        BANK = "bank", _("Bank")

    voucher_number = models.CharField(
        _("Voucher Number"),
        max_length=30,
        unique=True,
    )

    date = models.DateField(_("Date"))

    supplier = models.ForeignKey(
        "suppliers.Supplier",
        verbose_name=_("Supplier"),
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_vouchers",
    )

    paid_to = models.CharField(
        _("Paid To"),
        max_length=200,
    )

    account = models.ForeignKey(
        Account,
        verbose_name=_("Account"),
        on_delete=models.PROTECT,
        related_name="payment_vouchers",
    )

    amount = models.DecimalField(
        _("Amount"),
        max_digits=14,
        decimal_places=3,
    )

    payment_method = models.CharField(
        _("Payment Method"),
        max_length=20,
        choices=PaymentMethod.choices,
    )

    reference = models.CharField(
        _("Reference"),
        max_length=100,
        blank=True,
    )

    description = models.TextField(
        _("Description"),
        blank=True,
    )

    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.PROTECT,
        related_name="created_payment_vouchers",
    )

    created_at = models.DateTimeField(
        _("Created At"),
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        _("Updated At"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Payment Voucher")
        verbose_name_plural = _("Payment Vouchers")

    def __str__(self):
        return self.voucher_number
