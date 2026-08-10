from django.conf import settings
from django.db import models


class POSSale(models.Model):
    """Represents a Point of Sale transaction ticket."""
    class SaleStatus(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    sale_number = models.CharField(max_length=100, unique=True)
    
    # Optional customer (Null for walk-ins)
    customer = models.ForeignKey(
        'customers.Customer', 
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='pos_sales'
    )
    
    warehouse = models.ForeignKey(
        'inventory.Warehouse', 
        on_delete=models.PROTECT, 
        related_name='pos_sales'
    )
    
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT, 
        related_name='pos_sales'
    )
    
    date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, 
        choices=SaleStatus.choices, 
        default=SaleStatus.DRAFT
    )
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Sale #{self.sale_number} - Total: ${self.total}"


class POSSaleItem(models.Model):
    """Stores individual line items within a POS sale ticket[cite: 4]."""
    sale = models.ForeignKey(
        POSSale, 
        on_delete=models.CASCADE, 
        related_name='items'
    )
    
    product = models.ForeignKey(
        'inventory.Product', 
        on_delete=models.PROTECT, 
        related_name='pos_sale_items'
    )
    
    batch = models.ForeignKey(
        'inventory.StockBatch', 
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='pos_sale_items'
    )
    
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)  # Selling price at sale time[cite: 4]
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)   # Historical cost for COGS[cite: 4]
    
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product.name} on Sale #{self.sale.sale_number}"


class POSPayment(models.Model):
    """Tracks payment methods for a sale, allowing split tenders[cite: 4]."""
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        CARD = 'CARD', 'Card'
        BANK = 'BANK', 'Bank Transfer'
        CREDIT = 'CREDIT', 'Store Credit'

    sale = models.ForeignKey(
        POSSale, 
        on_delete=models.CASCADE, 
        related_name='payments'
    )
    
    payment_method = models.CharField(
        max_length=20, 
        choices=PaymentMethod.choices
    )
    
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference_number = models.CharField(max_length=100, blank=True, null=True)  # Card/bank reference[cite: 4]
    received_at = models.DateTimeField(auto_now_add=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT, 
        related_name='pos_payments'
    )

    def __str__(self):
        return f"{self.payment_method}: ${self.amount} for Sale #{self.sale.sale_number}"