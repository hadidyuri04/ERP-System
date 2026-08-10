from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from inventory.models import Product, Warehouse
from suppliers.models import Supplier

class PurchaseInvoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentType(models.TextChoices):
        CASH = "cash", "Cash"
        CREDIT = "credit", "Credit"

    invoice_number = models.CharField(
        max_length=30,
        unique=True,
    )

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_invoices",
    )

    supplier_invoice_number = models.CharField(
        max_length=50,
        blank=True,
    )

    invoice_date = models.DateField()

    due_date = models.DateField(
        null=True,
        blank=True,
    )

    payment_type = models.CharField(
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.CREDIT,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    subtotal = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    tax_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    additional_expenses = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    total = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    paid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_purchase_invoices",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return self.invoice_number

class PurchaseInvoiceItem(models.Model):
    purchase_invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="purchase_items",
    )

    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )

    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        validators=[MinValueValidator(0)],
    )

    discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    tax_amount = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    line_total = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    batch_number = models.CharField(
        max_length=100,
        blank=True,
    )

    expiration_date = models.DateField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.purchase_invoice.invoice_number} - {self.product}"