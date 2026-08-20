from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SalesInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        CANCELLED = "cancelled", _("Cancelled")
        CREDITED = "credited", _("Credited")

    class PaymentType(models.TextChoices):
        CASH = "cash", _("Cash")
        CREDIT = "credit", _("Credit")

    invoice_number = models.CharField(_("Invoice Number"), max_length=50, unique=True)
    quotation = models.OneToOneField(
        "quotations.Quotation", verbose_name=_("Quotation"), null=True, blank=True,
        on_delete=models.PROTECT, related_name="sales_invoice",
    )
    customer = models.ForeignKey(
        "customers.Customer", verbose_name=_("Customer"), on_delete=models.PROTECT,
        related_name="sales_invoices",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", verbose_name=_("Warehouse"), on_delete=models.PROTECT,
        related_name="sales_invoices",
    )
    invoice_date = models.DateField(_("Invoice Date"))
    due_date = models.DateField(_("Due Date"))
    payment_type = models.CharField(
        _("Payment Type"), max_length=10, choices=PaymentType.choices,
        default=PaymentType.CREDIT,
    )
    payment_account = models.ForeignKey(
        "finance.Account", verbose_name=_("Cash / Bank Account"),
        null=True, blank=True, on_delete=models.PROTECT,
        related_name="cash_sales_invoices",
    )
    status = models.CharField(
        _("Status"), max_length=15, choices=Status.choices, default=Status.DRAFT,
    )
    subtotal = models.DecimalField(_("Subtotal"), max_digits=14, decimal_places=3, default=0)
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=14, decimal_places=3, default=0)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=14, decimal_places=3, default=0)
    total = models.DecimalField(_("Total"), max_digits=14, decimal_places=3, default=0)
    notes = models.TextField(_("Notes"), blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("Created By"),
        on_delete=models.PROTECT, related_name="created_sales_invoices",
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)

    class Meta:
        ordering = ("-invoice_date", "-id")
        verbose_name = _("Sales Invoice")
        verbose_name_plural = _("Sales Invoices")

    def __str__(self):
        return self.invoice_number

    @property
    def open_item(self):
        from finance.models import OpenItem
        return OpenItem.objects.filter(
            journal_line__journal_entry__source_type="sales_invoice",
            journal_line__journal_entry__source_id=self.pk,
        ).first()

    @property
    def paid_amount(self):
        item = self.open_item
        if not item:
            return Decimal("0.000")
        return item.allocations.aggregate(total=Sum("amount"))["total"] or Decimal("0.000")

    @property
    def outstanding_amount(self):
        return max(self.total - self.paid_amount, Decimal("0.000"))

    @property
    def payment_status_code(self):
        if self.status not in {self.Status.POSTED, self.Status.CREDITED}:
            return "not_posted"
        if self.outstanding_amount == 0:
            return "paid" if self.status != self.Status.CREDITED else "credited"
        if self.paid_amount > 0:
            return "partially_paid"
        if self.due_date < timezone.localdate():
            return "overdue"
        return "unpaid"

    @property
    def payment_status(self):
        return {
            "not_posted": _("Not posted"),
            "paid": _("Paid"),
            "credited": _("Credited"),
            "partially_paid": _("Partially paid"),
            "overdue": _("Overdue"),
            "unpaid": _("Unpaid"),
        }[self.payment_status_code]

    @property
    def is_overdue(self):
        return (
            self.status == self.Status.POSTED
            and self.outstanding_amount > 0
            and self.due_date < timezone.localdate()
        )


class SalesInvoiceItem(models.Model):
    invoice = models.ForeignKey(
        SalesInvoice, verbose_name=_("Sales Invoice"), on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        "inventory.Product", verbose_name=_("Product"), on_delete=models.PROTECT,
        related_name="sales_invoice_items",
    )
    quantity = models.DecimalField(
        _("Quantity"), max_digits=14, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(
        _("Unit Price"), max_digits=14, decimal_places=3,
        validators=[MinValueValidator(Decimal("0.000"))],
    )
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=14, decimal_places=3, default=0)
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=14, decimal_places=3, default=0)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=14, decimal_places=3, default=0)
    line_total = models.DecimalField(_("Line Total"), max_digits=14, decimal_places=3, default=0)

    class Meta:
        verbose_name = _("Sales Invoice Item")
        verbose_name_plural = _("Sales Invoice Items")

    def __str__(self):
        return f"{self.invoice.invoice_number} - {self.product}"


class SalesInvoiceStockAllocation(models.Model):
    invoice_item = models.ForeignKey(
        SalesInvoiceItem, on_delete=models.PROTECT, related_name="stock_allocations",
    )
    batch = models.ForeignKey(
        "inventory.StockBatch", on_delete=models.PROTECT,
        related_name="sales_invoice_allocations",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=3)


class SalesCreditNote(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        CANCELLED = "cancelled", _("Cancelled")

    credit_note_number = models.CharField(_("Credit Note Number"), max_length=50, unique=True)
    invoice = models.OneToOneField(
        SalesInvoice, verbose_name=_("Sales Invoice"), on_delete=models.PROTECT,
        related_name="credit_note",
    )
    date = models.DateField(_("Date"))
    reason = models.TextField(_("Reason"))
    status = models.CharField(_("Status"), max_length=15, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("Created By"),
        on_delete=models.PROTECT, related_name="created_sales_credit_notes",
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)

    class Meta:
        ordering = ("-date", "-id")

    def __str__(self):
        return self.credit_note_number
