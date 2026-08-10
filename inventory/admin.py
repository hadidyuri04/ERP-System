from django.contrib import admin
from .models import (
    Category,
    Unit,
    Product,
    Warehouse,
    StockBatch,
    StockMovement,
    StockBalance,
    WarehouseTransfer,
    WarehouseTransferItem,
    WasteLoss,
    WasteLossItem,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'parent', 'is_active')
    search_fields = ('code', 'name')
    list_filter = ('is_active',)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'is_active')
    search_fields = ('name', 'symbol')
    list_filter = ('is_active',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('code', 'barcode', 'name', 'category', 'unit', 'selling_price', 'purchase_price', 'is_active')
    search_fields = ('code', 'barcode', 'name')
    list_filter = ('is_active', 'track_expiration', 'category')


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'location', 'is_active')
    search_fields = ('code', 'name', 'location')
    list_filter = ('is_active',)


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ('batch_number', 'product', 'warehouse', 'quantity_remaining', 'expiration_date', 'status')
    search_fields = ('batch_number', 'product__name', 'product__code')
    list_filter = ('status', 'warehouse')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('movement_type', 'product', 'warehouse', 'quantity', 'reference_type', 'reference_id', 'created_at')
    search_fields = ('product__name', 'reference_type', 'reference_id')
    list_filter = ('movement_type', 'warehouse', 'created_at')


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'quantity', 'reserved_quantity', 'updated_at')
    search_fields = ('product__name', 'warehouse__name')
    list_filter = ('warehouse',)


class WarehouseTransferItemInline(admin.TabularInline):
    model = WarehouseTransferItem
    extra = 1


@admin.register(WarehouseTransfer)
class WarehouseTransferAdmin(admin.ModelAdmin):
    list_display = ('transfer_number', 'source_warehouse', 'destination_warehouse', 'date', 'status', 'created_by')
    search_fields = ('transfer_number',)
    list_filter = ('status', 'source_warehouse', 'destination_warehouse')
    inlines = [WarehouseTransferItemInline]


class WasteLossItemInline(admin.TabularInline):
    model = WasteLossItem
    extra = 1


@admin.register(WasteLoss)
class WasteLossAdmin(admin.ModelAdmin):
    list_display = ('document_number', 'warehouse', 'date', 'reason', 'created_by')
    search_fields = ('document_number',)
    list_filter = ('reason', 'warehouse')
    inlines = [WasteLossItemInline]