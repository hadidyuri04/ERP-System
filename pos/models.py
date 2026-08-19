from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class POSSession(models.Model):
    """Tracks a cashier's daily register session/shift from opening to closing float reconciliation."""
    class SessionStatus(models.TextChoices):
        OPEN = 'OPEN', _('Open')
        CLOSED = 'CLOSED', _('Closed')

    session_number = models.CharField(_("Session Number"), max_length=100, unique=True)
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Cashier"),
        on_delete=models.PROTECT,
        related_name='pos_sessions'
    )
    warehouse = models.ForeignKey(
        'inventory.Warehouse',
        verbose_name=_("Warehouse"),
        on_delete=models.PROTECT,
        related_name='pos_sessions'
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=SessionStatus.choices,
        default=SessionStatus.OPEN
    )
    
    opened_at = models.DateTimeField(_("Opened At"), auto_now_add=True)
    closed_at = models.DateTimeField(_("Closed At"), blank=True, null=True)
    
    opening_balance = models.DecimalField(_("Opening Cash Float"), max_digits=12, decimal_places=3, default=0.000)
    closing_balance_expected = models.DecimalField(_("Expected Cash"), max_digits=12, decimal_places=3, default=0.000)
    closing_balance_actual = models.DecimalField(_("Actual Counted Cash"), max_digits=12, decimal_places=3, default=0.000)
    difference = models.DecimalField(_("Difference / Shortage"), max_digits=12, decimal_places=3, default=0.000)
    
    notes = models.TextField(_("Session Notes"), blank=True, null=True)

    class Meta:
        verbose_name = _("POS Session")
        verbose_name_plural = _("POS Sessions")
        ordering = ['-opened_at']

    def __str__(self):
        return f"Session #{self.session_number} ({self.cashier.username}) - {self.get_status_display()}"


class POSCashTransaction(models.Model):
    """Tracks manual cash movements into or out of the drawer during a session (e.g. Cash Drop / Paid Out)."""
    class TransactionType(models.TextChoices):
        CASH_IN = 'IN', _('Cash In / Add Float')
        CASH_OUT = 'OUT', _('Cash Out / Drop')

    session = models.ForeignKey(
        POSSession,
        verbose_name=_("POS Session"),
        on_delete=models.CASCADE,
        related_name='cash_transactions'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("User"),
        on_delete=models.PROTECT,
        related_name='pos_cash_transactions'
    )
    transaction_type = models.CharField(
        _("Transaction Type"),
        max_length=10,
        choices=TransactionType.choices
    )
    amount = models.DecimalField(_("Amount"), max_digits=12, decimal_places=3)
    reason = models.CharField(_("Reason / Note"), max_length=255)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("POS Cash Transaction")
        verbose_name_plural = _("POS Cash Transactions")

    def __str__(self):
        return f"{self.get_transaction_type_display()} ({self.amount}) - Session #{self.session.session_number}"


class POSSale(models.Model):
    """Represents a Point of Sale transaction ticket."""
    class SaleStatus(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        HELD = 'HELD', _('On Hold')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    session = models.ForeignKey(
        POSSession,
        verbose_name=_("POS Session"),
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='sales'
    )
    sale_number = models.CharField(_("Sale Number"), max_length=100, unique=True)
    
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
    unit_price = models.DecimalField(_("Unit Price"), max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=12, decimal_places=3)
    
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
    reference_number = models.CharField(_("Reference Number"), max_length=100, blank=True, null=True)
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