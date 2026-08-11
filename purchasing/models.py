from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from inventory.models import Product, Warehouse
from suppliers.models import Supplier

class PurchaseInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED = "cancelled", _("Cancelled")

    class PaymentType(models.TextChoices):
        CASH = "cash", _("Cash")
        CREDIT = "credit", _("Credit")

    invoice_number = models.CharField(
        _("Invoice Number"),
        max_length=30,
        unique=True,
    )

    supplier = models.ForeignKey(
        Supplier,
        verbose_name=_("Supplier"),
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        verbose_name=_("Warehouse"),
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )

    supplier_invoice_number = models.CharField(
        _("Supplier Invoice Number"),
        max_length=50,
        blank=True,
    )

    invoice_date = models.DateField(_("Invoice Date"))

    due_date = models.DateField(
        _("Due Date"),
        null=True,
        blank=True,
    )

    payment_type = models.CharField(
        _("Payment Type"),
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.CREDIT,
    )

    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    subtotal = models.DecimalField(
        _("Subtotal"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    discount_amount = models.DecimalField(
        _("Discount Amount"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    tax_amount = models.DecimalField(
        _("Tax Amount"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    additional_expenses = models.DecimalField(
        _("Additional Expenses"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    total = models.DecimalField(
        _("Total"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    paid_amount = models.DecimalField(
        _("Paid Amount"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    notes = models.TextField(
        _("Notes"),
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.PROTECT,
        related_name="created_purchase_invoices",
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
        verbose_name = _("Purchase Invoice")
        verbose_name_plural = _("Purchase Invoices")

    def __str__(self):
        return self.invoice_number

class PurchaseInvoiceItem(models.Model):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice,
        verbose_name=_("Purchase Invoice"),
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        Product,
        verbose_name=_("Product"),
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )

    quantity = models.DecimalField(
        _("Quantity"),
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )

    unit_cost = models.DecimalField(
        _("Unit Cost"),
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(0)],
    )

    discount_amount = models.DecimalField(
        _("Discount Amount"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    tax_amount = models.DecimalField(
        _("Tax Amount"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    line_total = models.DecimalField(
        _("Line Total"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    batch_number = models.CharField(
        _("Batch Number"),
        max_length=100,
        blank=True,
    )

    expiration_date = models.DateField(
        _("Expiration Date"),
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        _("Created At"),
        auto_now_add=True,
    )

    class Meta:
        verbose_name = _("Purchase Invoice Item")
        verbose_name_plural = _("Purchase Invoice Items")

    def __str__(self):
        return f"{self.purchase_invoice.invoice_number} - {self.product}"