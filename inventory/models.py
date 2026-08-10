from django.db import models


class Category(models.Model):
    """Represents item categories with support for hierarchical sub-categories[cite: 1]."""
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
    """Defines units of measure such as Piece, Kg, Liter, Box[cite: 1]."""
    name = models.CharField(max_length=100)  # e.g., Piece, Kilogram
    symbol = models.CharField(max_length=20)   # e.g., pcs, kg, L
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class Product(models.Model):
    """Stores master data for items, prices, and inventory thresholds[cite: 1]."""
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
    """Maintains physical or virtual storage facilities[cite: 1]."""
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"[{self.code}] {self.name}"