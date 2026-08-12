from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class POSSale(models.Model):
    """Represents a Point of Sale transaction ticket."""
    class SaleStatus(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    sale_number = models.CharField(_("Sale Number"), max_length=100, unique=True)
    
    # Optional customer (Null for walk-ins)
    customer = models.ForeignKey(
        'customers.Customer', 
        verbose_name=_("Customer"),
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='pos_sales'
    )
    
    warehouse = models.ForeignKey(
        'inventory.Warehouse', 
        verbose_name=_("Warehouse"),
        on_delete=models.PROTECT, 
        related_name='pos_sales'
    )
    
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        verbose_name=_("Cashier"),
        on_delete=models.PROTECT, 
        related_name='pos_sales'
    )
    
    date = models.DateTimeField(_("Date"), auto_now_add=True)
    status = models.CharField(
        _("Status"),
        max_length=20, 
        choices=SaleStatus.choices, 
        default=SaleStatus.DRAFT
    )
    
    subtotal = models.DecimalField(_("Subtotal"), max_digits=12, decimal_places=3, default=0.000)
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=12, decimal_places=3, default=0.000)
    total = models.DecimalField(_("Total"), max_digits=12, decimal_places=3, default=0.000)
    
    paid_amount = models.DecimalField(_("Paid Amount"), max_digits=12, decimal_places=3, default=0.000)
    change_amount = models.DecimalField(_("Change Amount"), max_digits=12, decimal_places=3, default=0.000)
    
    notes = models.TextField(_("Notes"), blank=True, null=True)

    class Meta:
        verbose_name = _("POS Sale")
        verbose_name_plural = _("POS Sales")

    def __str__(self):
        return f"Sale #{self.sale_number} - Total: ${self.total}"


class POSSaleItem(models.Model):
    """Stores individual line items within a POS sale ticket."""
    sale = models.ForeignKey(
        POSSale, 
        verbose_name=_("Sale"),
        on_delete=models.CASCADE, 
        related_name='items'
    )
    
    product = models.ForeignKey(
        'inventory.Product', 
        verbose_name=_("Product"),
        on_delete=models.PROTECT, 
        related_name='pos_sale_items'
    )
    
    batch = models.ForeignKey(
        'inventory.StockBatch', 
        verbose_name=_("Stock Batch"),
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='pos_sale_items'
    )
    
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(_("Unit Price"), max_digits=12, decimal_places=3)  # Selling price at sale time
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=12, decimal_places=3)   # Historical cost for COGS
    
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=12, decimal_places=3, default=0.000)
    line_total = models.DecimalField(_("Line Total"), max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = _("POS Sale Item")
        verbose_name_plural = _("POS Sale Items")

    def __str__(self):
        return f"{self.quantity}x {self.product.name} on Sale #{self.sale.sale_number}"


class POSPayment(models.Model):
    """Tracks payment methods for a sale, allowing split tenders."""
    class PaymentMethod(models.TextChoices):
        CASH = 'cash', _('Cash')
        CARD = 'card', _('Card')
        BANK = 'bank', _('Bank Transfer')
        CREDIT = 'credit', _('Store Credit')

    sale = models.ForeignKey(
        POSSale, 
        verbose_name=_("Sale"),
        on_delete=models.CASCADE, 
        related_name='payments'
    )
    
    payment_method = models.CharField(
        _("Payment Method"),
        max_length=20, 
        choices=PaymentMethod.choices
    )
    
    amount = models.DecimalField(_("Amount"), max_digits=12, decimal_places=3)
    reference_number = models.CharField(_("Reference Number"), max_length=100, blank=True, null=True)  # Card/bank reference
    received_at = models.DateTimeField(_("Received At"), auto_now_add=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        verbose_name=_("Created By"),
        on_delete=models.PROTECT, 
        related_name='pos_payments'
    )

    class Meta:
        verbose_name = _("POS Payment")
        verbose_name_plural = _("POS Payments")

    def __str__(self):
        return f"{self.payment_method}: ${self.amount} for Sale #{self.sale.sale_number}"