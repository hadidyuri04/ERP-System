from django.conf import settings
from django.db import models


class Category(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        'self', 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='subcategories'
    )
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"[{self.code}] {self.name}"


class Unit(models.Model):
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class Product(models.Model):
    code = models.CharField(max_length=50, unique=True)
    barcode = models.CharField(max_length=100, unique=True, blank=True, null=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    
    category = models.ForeignKey(
        Category, 
        on_delete=models.PROTECT, 
        related_name='products'
    )
    unit = models.ForeignKey(
        Unit, 
        on_delete=models.PROTECT, 
        related_name='products'
    )
    
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    minimum_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
    track_expiration = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"[{self.code}] {self.name}"


class Warehouse(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"[{self.code}] {self.name}"


class StockBatch(models.Model):
    """Tracks physical inventory batches for expiration control and FEFO."""
    class BatchStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        EXPIRED = 'EXPIRED', 'Expired'
        DEPLETED = 'DEPLETED', 'Depleted'
        BLOCKED = 'BLOCKED', 'Blocked'

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='batches')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='batches')
    batch_number = models.CharField(max_length=100)
    
    expiration_date = models.DateField(blank=True, null=True)
    received_date = models.DateField()
    
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_received = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_remaining = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Linked to Supplier & PurchaseInvoiceItem (using strings to avoid circular import loops if apps reference each other)
    supplier = models.ForeignKey('suppliers.Supplier', on_delete=models.SET_NULL, blank=True, null=True, related_name='batches')
    purchase_item = models.ForeignKey('purchasing.PurchaseInvoiceItem', on_delete=models.SET_NULL, blank=True, null=True, related_name='batches')
    
    status = models.CharField(max_length=20, choices=BatchStatus.choices, default=BatchStatus.ACTIVE)

    def __str__(self):
        return f"Batch {self.batch_number} - {self.product.name} ({self.quantity_remaining} left)"


class StockMovement(models.Model):
    """The master inventory audit trail tracking every single stock increase or decrease."""
    class MovementType(models.TextChoices):
        OPENING_BALANCE = 'OPENING_BALANCE', 'Opening Balance'
        PURCHASE = 'PURCHASE', 'Purchase'
        PURCHASE_RETURN = 'PURCHASE_RETURN', 'Purchase Return'
        SALE = 'SALE', 'Sale'
        SALE_RETURN = 'SALE_RETURN', 'Sale Return'
        TRANSFER_IN = 'TRANSFER_IN', 'Transfer In'
        TRANSFER_OUT = 'TRANSFER_OUT', 'Transfer Out'
        WASTE = 'WASTE', 'Waste'
        DAMAGE = 'DAMAGE', 'Damage'
        ADJUSTMENT_IN = 'ADJUSTMENT_IN', 'Adjustment In'
        ADJUSTMENT_OUT = 'ADJUSTMENT_OUT', 'Adjustment Out'

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='movements')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='movements')
    batch = models.ForeignKey(StockBatch, on_delete=models.SET_NULL, blank=True, null=True, related_name='movements')
    
    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)  # Positive for additions, negative or managed by type
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    
    reference_type = models.CharField(max_length=100)  # e.g., 'PurchaseInvoice', 'POS', 'WasteLoss'
    reference_id = models.BigIntegerField()
    
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.movement_type}: {self.quantity} of {self.product.name} in {self.warehouse.name}"


class StockBalance(models.Model):
    """Provides fast lookups for current stock levels per product and warehouse."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='balances')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='balances')
    
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    reserved_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Enforces that a product can only have one stock balance row per warehouse location
        unique_together = ('product', 'warehouse')

    def __str__(self):
        return f"{self.product.name} @ {self.warehouse.name}: {self.quantity}"

class WarehouseTransfer(models.Model):
    """Tracks movement of stock quantities between two warehouses while total company stock remains unchanged[cite: 1]."""
    class TransferStatus(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    transfer_number = models.CharField(max_length=100, unique=True)
    source_warehouse = models.ForeignKey(
        Warehouse, 
        on_delete=models.PROTECT, 
        related_name='transfers_out'
    )
    destination_warehouse = models.ForeignKey(
        Warehouse, 
        on_delete=models.PROTECT, 
        related_name='transfers_in'
    )
    
    date = models.DateField()
    status = models.CharField(
        max_length=20, 
        choices=TransferStatus.choices, 
        default=TransferStatus.DRAFT
    )
    notes = models.TextField(blank=True, null=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT, 
        related_name='warehouse_transfers'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Transfer #{self.transfer_number}: {self.source_warehouse.name} -> {self.destination_warehouse.name}"


class WarehouseTransferItem(models.Model):
    """Line items for specific products and quantities moved during a warehouse transfer."""
    transfer = models.ForeignKey(
        WarehouseTransfer, 
        on_delete=models.CASCADE, 
        related_name='items'
    )
    product = models.ForeignKey(
        Product, 
        on_delete=models.PROTECT, 
        related_name='transfer_items'
    )
    batch = models.ForeignKey(
        StockBatch, 
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='transfer_items'
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product.name} on Transfer #{self.transfer.transfer_number}"


class WasteLoss(models.Model):
    """Records expired, damaged, spoiled, broken, or missing stock (reduces inventory and records a financial loss)[cite: 1]."""
    class WasteReason(models.TextChoices):
        EXPIRED = 'EXPIRED', 'Expired'
        DAMAGED = 'DAMAGED', 'Damaged'
        SPOILED = 'SPOILED', 'Spoiled'
        BROKEN = 'BROKEN', 'Broken'
        MISSING = 'MISSING', 'Missing'
        OTHER = 'OTHER', 'Other'

    document_number = models.CharField(max_length=100, unique=True)
    warehouse = models.ForeignKey(
        Warehouse, 
        on_delete=models.PROTECT, 
        related_name='waste_losses'
    )
    date = models.DateField()
    reason = models.CharField(
        max_length=30, 
        choices=WasteReason.choices
    )
    notes = models.TextField(blank=True, null=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT, 
        related_name='waste_loss_records'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Waste/Loss #{self.document_number} ({self.reason})"


class WasteLossItem(models.Model):
    """Individual product lines written off as waste or loss."""
    waste_loss = models.ForeignKey(
        WasteLoss, 
        on_delete=models.CASCADE, 
        related_name='items'
    )
    product = models.ForeignKey(
        Product, 
        on_delete=models.PROTECT, 
        related_name='waste_loss_items'
    )
    batch = models.ForeignKey(
        StockBatch, 
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='waste_loss_items'
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2) # Captured for financial loss calculation
    total_cost = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product.name} written off for {self.waste_loss.reason}"