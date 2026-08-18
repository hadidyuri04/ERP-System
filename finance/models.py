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
        RECEIPT = "receipt", _("Receipt Voucher")
        PAYMENT = "payment", _("Payment Voucher")
        WASTE = "waste", _("Waste & Loss")
        SALES_RETURN = "sales_return", _("Sales Return")
        REVERSAL = "reversal", _("Journal Reversal")

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
