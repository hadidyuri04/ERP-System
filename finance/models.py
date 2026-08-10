from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from customers.models import Customer
from suppliers.models import Supplier


class Account(models.Model):
    class AccountType(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        REVENUE = "revenue", "Revenue"
        EXPENSE = "expense", "Expense"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)

    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )

    allow_posting = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    class SourceType(models.TextChoices):
        MANUAL = "manual", "Manual"
        PURCHASE = "purchase", "Purchase"
        POS_SALE = "pos_sale", "POS Sale"
        RECEIPT = "receipt", "Receipt Voucher"
        PAYMENT = "payment", "Payment Voucher"
        WASTE = "waste", "Waste & Loss"
        SALES_RETURN = "sales_return", "Sales Return"

    entry_number = models.CharField(
        max_length=30,
        unique=True,
    )

    date = models.DateField()

    description = models.TextField(blank=True)

    source_type = models.CharField(
        max_length=30,
        choices=SourceType.choices,
        default=SourceType.MANUAL,
    )

    source_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_journal_entries",
    )

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_journal_entries",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.entry_number


class JournalEntryLine(models.Model):
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="journal_lines",
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="journal_lines",
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="journal_lines",
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    debit = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    credit = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()

        if self.debit < 0:
            raise ValidationError({
                "debit": "Debit cannot be negative."
            })

        if self.credit < 0:
            raise ValidationError({
                "credit": "Credit cannot be negative."
            })

        if self.debit > 0 and self.credit > 0:
            raise ValidationError(
                "A journal line cannot contain both debit and credit."
            )

        if self.debit == 0 and self.credit == 0:
            raise ValidationError(
                "A journal line must contain a debit or credit amount."
            )

        if not self.account.allow_posting:
            raise ValidationError({
                "account": "This account does not allow direct posting."
            })

    def __str__(self):
        return f"{self.journal_entry.entry_number} - {self.account}"

class ReceiptVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BANK = "bank", "Bank"

    voucher_number = models.CharField(
        max_length=30,
        unique=True,
    )

    date = models.DateField()

    customer = models.ForeignKey(
        "customers.Customer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="receipt_vouchers",
    )

    received_from = models.CharField(
        max_length=200,
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="receipt_vouchers",
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    description = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_receipt_vouchers",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.voucher_number


class PaymentVoucher(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BANK = "bank", "Bank"

    voucher_number = models.CharField(
        max_length=30,
        unique=True,
    )

    date = models.DateField()

    supplier = models.ForeignKey(
        "suppliers.Supplier",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_vouchers",
    )

    paid_to = models.CharField(
        max_length=200,
    )

    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="payment_vouchers",
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
    )

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    description = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_payment_vouchers",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.voucher_number