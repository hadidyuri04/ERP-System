from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _, get_language


class Category(models.Model):
    code = models.CharField(_("Code"), max_length=50, unique=True)
    name_en = models.CharField(_("English Name"), max_length=255)
    name_ar = models.CharField(_("Arabic Name"), max_length=255)
    parent = models.ForeignKey(
        'self', 
        verbose_name=_("Parent Category"),
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='subcategories'
    )
    description = models.TextField(_("Description"), blank=True, null=True)
    is_active = models.BooleanField(_("Is Active"), default=True)

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

    @property
    def name(self):
        """Dynamic property to return Arabic or English name based on active language."""
        if get_language() == 'ar' and self.name_ar:
            return self.name_ar
        return self.name_en

    def __str__(self):
        return f"[{self.code}] {self.name}"


class Unit(models.Model):
    name_en = models.CharField(_("English Name"), max_length=100)
    name_ar = models.CharField(_("Arabic Name"), max_length=100)
    symbol = models.CharField(_("Symbol"), max_length=20)
    is_active = models.BooleanField(_("Is Active"), default=True)

    class Meta:
        verbose_name = _("Unit")
        verbose_name_plural = _("Units")

    @property
    def name(self):
        if get_language() == 'ar' and self.name_ar:
            return self.name_ar
        return self.name_en

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class Product(models.Model):
    code = models.CharField(_("Code"), max_length=50, unique=True)
    barcode = models.CharField(_("Barcode"), max_length=100, unique=True, blank=True, null=True)
    name_en = models.CharField(_("English Name"), max_length=255)
    name_ar = models.CharField(_("Arabic Name"), max_length=255)
    description = models.TextField(_("Description"), blank=True, null=True)
    
    category = models.ForeignKey(
        Category, 
        verbose_name=_("Category"),
        on_delete=models.PROTECT, 
        related_name='products'
    )
    unit = models.ForeignKey(
        Unit, 
        verbose_name=_("Unit"),
        on_delete=models.PROTECT, 
        related_name='products'
    )
    
    purchase_price = models.DecimalField(_("Purchase Price"), max_digits=12, decimal_places=3)
    selling_price = models.DecimalField(_("Selling Price"), max_digits=12, decimal_places=3)
    minimum_stock = models.DecimalField(_("Minimum Stock"), max_digits=12, decimal_places=3, default=0.000)
    
    track_expiration = models.BooleanField(_("Track Expiration"), default=False)
    is_active = models.BooleanField(_("Is Active"), default=True)
    image = models.ImageField(_("Product Image"), upload_to='products/', blank=True, null=True)
    primary_supplier = models.ForeignKey(
        'suppliers.Supplier', 
        verbose_name=_("Primary Supplier"), 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    maximum_stock = models.DecimalField(_("Maximum Stock"), max_digits=12, decimal_places=3, default=0.000)
    reorder_quantity = models.DecimalField(_("Reorder Quantity"), max_digits=12, decimal_places=3, default=0.000)

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")

    @property
    def name(self):
        if get_language() == 'ar' and self.name_ar:
            return self.name_ar
        return self.name_en

    def __str__(self):
        return f"[{self.code}] {self.name}"


class Warehouse(models.Model):
    code = models.CharField(_("Code"), max_length=50, unique=True)
    name = models.CharField(_("Name"), max_length=255)
    location = models.CharField(_("Location"), max_length=500, blank=True, null=True)
    description = models.TextField(_("Description"), blank=True, null=True)
    is_active = models.BooleanField(_("Is Active"), default=True)

    class Meta:
        verbose_name = _("Warehouse")
        verbose_name_plural = _("Warehouses")

    def __str__(self):
        return f"[{self.code}] {self.name}"


class StockBatch(models.Model):
    """Tracks physical inventory batches for expiration control and FEFO."""
    class BatchStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', _('Active')
        EXPIRED = 'EXPIRED', _('Expired')
        DEPLETED = 'DEPLETED', _('Depleted')
        BLOCKED = 'BLOCKED', _('Blocked')

    product = models.ForeignKey(Product, verbose_name=_("Product"), on_delete=models.PROTECT, related_name='batches')
    warehouse = models.ForeignKey(Warehouse, verbose_name=_("Warehouse"), on_delete=models.PROTECT, related_name='batches')
    batch_number = models.CharField(_("Batch Number"), max_length=100)
    
    expiration_date = models.DateField(_("Expiration Date"), blank=True, null=True)
    received_date = models.DateField(_("Received Date"))
    
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=12, decimal_places=3)
    quantity_received = models.DecimalField(_("Quantity Received"), max_digits=12, decimal_places=3)
    quantity_remaining = models.DecimalField(_("Quantity Remaining"), max_digits=12, decimal_places=3)
    
    supplier = models.ForeignKey('suppliers.Supplier', verbose_name=_("Supplier"), on_delete=models.SET_NULL, blank=True, null=True, related_name='batches')
    purchase_item = models.ForeignKey('purchasing.PurchaseInvoiceItem', verbose_name=_("Purchase Item"), on_delete=models.SET_NULL, blank=True, null=True, related_name='batches')
    
    status = models.CharField(_("Status"), max_length=20, choices=BatchStatus.choices, default=BatchStatus.ACTIVE)

    class Meta:
        verbose_name = _("Stock Batch")
        verbose_name_plural = _("Stock Batches")

    def __str__(self):
        return f"Batch {self.batch_number} - {self.product.name} ({self.quantity_remaining} left)"


class StockMovement(models.Model):
    """The master inventory audit trail tracking every single stock increase or decrease."""
    class MovementType(models.TextChoices):
        OPENING_BALANCE = 'OPENING_BALANCE', _('Opening Balance')
        PURCHASE = 'PURCHASE', _('Purchase')
        PURCHASE_RETURN = 'PURCHASE_RETURN', _('Purchase Return')
        SALE = 'SALE', _('Sale')
        SALE_RETURN = 'SALE_RETURN', _('Sale Return')
        TRANSFER_IN = 'TRANSFER_IN', _('Transfer In')
        TRANSFER_OUT = 'TRANSFER_OUT', _('Transfer Out')
        WASTE = 'WASTE', _('Waste')
        DAMAGE = 'DAMAGE', _('Damage')
        ADJUSTMENT_IN = 'ADJUSTMENT_IN', _('Adjustment In')
        ADJUSTMENT_OUT = 'ADJUSTMENT_OUT', _('Adjustment Out')

    product = models.ForeignKey(Product, verbose_name=_("Product"), on_delete=models.PROTECT, related_name='movements')
    warehouse = models.ForeignKey(Warehouse, verbose_name=_("Warehouse"), on_delete=models.PROTECT, related_name='movements')
    batch = models.ForeignKey(StockBatch, verbose_name=_("Stock Batch"), on_delete=models.SET_NULL, blank=True, null=True, related_name='movements')
    
    movement_type = models.CharField(_("Movement Type"), max_length=30, choices=MovementType.choices)
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=12, decimal_places=3)
    
    reference_type = models.CharField(_("Reference Type"), max_length=100)
    reference_id = models.BigIntegerField(_("Reference ID"))
    
    notes = models.TextField(_("Notes"), blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("Created By"), on_delete=models.PROTECT)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Stock Movement")
        verbose_name_plural = _("Stock Movements")

    def __str__(self):
        return f"{self.movement_type}: {self.quantity} of {self.product.name} in {self.warehouse.name}"


class StockBalance(models.Model):
    """Provides fast lookups for current stock levels per product and warehouse."""
    product = models.ForeignKey(Product, verbose_name=_("Product"), on_delete=models.PROTECT, related_name='balances')
    warehouse = models.ForeignKey(Warehouse, verbose_name=_("Warehouse"), on_delete=models.PROTECT, related_name='balances')
    
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3, default=0.000)
    reserved_quantity = models.DecimalField(_("Reserved Quantity"), max_digits=12, decimal_places=3, default=0.000)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    @property
    def needs_reorder(self):
        return self.quantity <= self.product.minimum_stock

    class Meta:
        unique_together = ('product', 'warehouse')
        verbose_name = _("Stock Balance")
        verbose_name_plural = _("Stock Balances")

    def __str__(self):
        return f"{self.product.name} @ {self.warehouse.name}: {self.quantity}"


class WarehouseTransfer(models.Model):
    """Tracks movement of stock quantities between two warehouses while total company stock remains unchanged."""
    class TransferStatus(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        COMPLETED = 'COMPLETED', _('Completed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    transfer_number = models.CharField(_("Transfer Number"), max_length=100, unique=True)
    source_warehouse = models.ForeignKey(
        Warehouse, 
        verbose_name=_("Source Warehouse"),
        on_delete=models.PROTECT, 
        related_name='transfers_out'
    )
    destination_warehouse = models.ForeignKey(
        Warehouse, 
        verbose_name=_("Destination Warehouse"),
        on_delete=models.PROTECT, 
        related_name='transfers_in'
    )
    
    date = models.DateField(_("Date"))
    status = models.CharField(
        _("Status"),
        max_length=20, 
        choices=TransferStatus.choices, 
        default=TransferStatus.DRAFT
    )
    notes = models.TextField(_("Notes"), blank=True, null=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        verbose_name=_("Created By"),
        on_delete=models.PROTECT, 
        related_name='warehouse_transfers'
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Warehouse Transfer")
        verbose_name_plural = _("Warehouse Transfers")

    def __str__(self):
        return f"Transfer #{self.transfer_number}: {self.source_warehouse.name} -> {self.destination_warehouse.name}"


class WarehouseTransferItem(models.Model):
    """Line items for specific products and quantities moved during a warehouse transfer."""
    transfer = models.ForeignKey(
        WarehouseTransfer, 
        verbose_name=_("Transfer"),
        on_delete=models.CASCADE, 
        related_name='items'
    )
    product = models.ForeignKey(
        Product, 
        verbose_name=_("Product"),
        on_delete=models.PROTECT, 
        related_name='transfer_items'
    )
    batch = models.ForeignKey(
        StockBatch, 
        verbose_name=_("Stock Batch"),
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='transfer_items'
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = _("Warehouse Transfer Item")
        verbose_name_plural = _("Warehouse Transfer Items")

    def __str__(self):
        return f"{self.quantity}x {self.product.name} on Transfer #{self.transfer.transfer_number}"


class WasteLoss(models.Model):
    """Records expired, damaged, spoiled, broken, or missing stock."""
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        CANCELLED = "CANCELLED", _("Cancelled")

    class WasteReason(models.TextChoices):
        EXPIRED = 'EXPIRED', _('Expired')
        DAMAGED = 'DAMAGED', _('Damaged')
        SPOILED = 'SPOILED', _('Spoiled')
        BROKEN = 'BROKEN', _('Broken')
        MISSING = 'MISSING', _('Missing')
        OTHER = 'OTHER', _('Other')

    document_number = models.CharField(_("Document Number"), max_length=100, unique=True)
    warehouse = models.ForeignKey(
        Warehouse, 
        verbose_name=_("Warehouse"),
        on_delete=models.PROTECT, 
        related_name='waste_losses'
    )
    date = models.DateField(_("Date"))
    reason = models.CharField(
        _("Reason"),
        max_length=30, 
        choices=WasteReason.choices
    )
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    notes = models.TextField(_("Notes"), blank=True, null=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        verbose_name=_("Created By"),
        on_delete=models.PROTECT, 
        related_name='waste_loss_records'
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Waste Loss")
        verbose_name_plural = _("Waste Losses")

    def __str__(self):
        return f"Waste/Loss #{self.document_number} ({self.reason})"


class WasteLossItem(models.Model):
    """Individual product lines written off as waste or loss."""
    waste_loss = models.ForeignKey(
        WasteLoss, 
        verbose_name=_("Waste Loss"),
        on_delete=models.CASCADE, 
        related_name='items'
    )
    product = models.ForeignKey(
        Product, 
        verbose_name=_("Product"),
        on_delete=models.PROTECT, 
        related_name='waste_loss_items'
    )
    batch = models.ForeignKey(
        StockBatch, 
        verbose_name=_("Stock Batch"),
        on_delete=models.SET_NULL, 
        blank=True, 
        null=True, 
        related_name='waste_loss_items'
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(_("Unit Cost"), max_digits=12, decimal_places=3)
    total_cost = models.DecimalField(_("Total Cost"), max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = _("Waste Loss Item")
        verbose_name_plural = _("Waste Loss Items")

    def __str__(self):
        return f"{self.quantity}x {self.product.name} written off for {self.waste_loss.reason}"


class StockAdjustment(models.Model):
    """Document for physical inventory counts and stock reconciliation."""
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        CONFIRMED = 'CONFIRMED', _('Confirmed')
        CANCELLED = 'CANCELLED', _('Cancelled')

    adjustment_number = models.CharField(_("Adjustment Number"), max_length=100, unique=True)
    warehouse = models.ForeignKey(
        Warehouse,
        verbose_name=_("Warehouse"),
        on_delete=models.PROTECT,
        related_name='stock_adjustments'
    )
    date = models.DateField(_("Date"))
    status = models.CharField(_("Status"), max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(_("Reason / Notes"), blank=True, null=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("Created By"),
        on_delete=models.PROTECT,
        related_name='stock_adjustments'
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Stock Adjustment")
        verbose_name_plural = _("Stock Adjustments")

    def __str__(self):
        return f"Adjustment #{self.adjustment_number} @ {self.warehouse.name}"


class StockAdjustmentItem(models.Model):
    """Counted vs system quantity for one product line in an adjustment."""
    adjustment = models.ForeignKey(
        StockAdjustment,
        verbose_name=_("Adjustment"),
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        verbose_name=_("Product"),
        on_delete=models.PROTECT,
        related_name='adjustment_items'
    )
    batch = models.ForeignKey(
        StockBatch,
        verbose_name=_("Stock Batch"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adjustment_items'
    )

    system_quantity = models.DecimalField(_("System Quantity"), max_digits=12, decimal_places=3)
    counted_quantity = models.DecimalField(_("Counted Quantity"), max_digits=12, decimal_places=3)
    variance = models.DecimalField(_("Variance"), max_digits=12, decimal_places=3, editable=False)

    class Meta:
        unique_together = ('adjustment', 'product', 'batch')
        verbose_name = _("Stock Adjustment Item")
        verbose_name_plural = _("Stock Adjustment Items")

    def save(self, *args, **kwargs):
        self.variance = self.counted_quantity - self.system_quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name}: counted {self.counted_quantity} vs system {self.system_quantity}"