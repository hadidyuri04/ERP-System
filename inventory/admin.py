from django.contrib import admin

from .services import confirm_waste_loss
from .models import (
    Category,
    Unit,
    Product,
    Warehouse,
    StockAdjustment,
    StockAdjustmentItem,
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

    def get_readonly_fields(self, request, obj=None):
        # Lines of a completed or cancelled transfer are history, not data entry.
        if obj and obj.status != WarehouseTransfer.TransferStatus.DRAFT:
            return ("product", "batch", "quantity", "unit_cost")
        return ("unit_cost",)


@admin.register(WarehouseTransfer)
class WarehouseTransferAdmin(admin.ModelAdmin):
    list_display = ('transfer_number', 'source_warehouse', 'destination_warehouse', 'date', 'status', 'created_by')
    search_fields = ('transfer_number',)
    list_filter = ('status', 'source_warehouse', 'destination_warehouse')
    inlines = [WarehouseTransferItemInline]
    readonly_fields = ('status', 'created_by', 'created_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        # Spec 10: confirmed documents are locked.
        if obj and obj.status != WarehouseTransfer.TransferStatus.DRAFT:
            return False
        return super().has_change_permission(request, obj)


class StockAdjustmentItemInline(admin.TabularInline):
    model = StockAdjustmentItem
    extra = 1
    readonly_fields = ("system_quantity", "variance")

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != StockAdjustment.Status.DRAFT:
            return ("product", "batch", "counted_quantity", "system_quantity", "variance")
        return self.readonly_fields


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('adjustment_number', 'warehouse', 'date', 'status', 'created_by')
    search_fields = ('adjustment_number',)
    list_filter = ('status', 'warehouse')
    inlines = [StockAdjustmentItemInline]
    readonly_fields = ('status', 'created_by', 'created_at')

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        # Spec 10: confirmed documents are locked.
        if obj and obj.status != StockAdjustment.Status.DRAFT:
            return False
        return super().has_change_permission(request, obj)


class WasteLossItemInline(admin.TabularInline):
    model = WasteLossItem
    extra = 1

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != WasteLoss.Status.DRAFT:
            return ("product", "batch", "quantity", "unit_cost", "total_cost")
        return ()

    def has_add_permission(self, request, obj=None):
        if obj and obj.status != WasteLoss.Status.DRAFT:
            return False
        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status != WasteLoss.Status.DRAFT:
            return False
        return super().has_delete_permission(request, obj)


@admin.action(description="Confirm and post selected waste/loss documents")
def confirm_and_post_selected_waste(modeladmin, request, queryset):
    for waste_id in queryset.values_list("id", flat=True):
        try:
            waste, _journal = confirm_waste_loss(waste_id, request.user)
            modeladmin.message_user(
                request,
                f"{waste.document_number} confirmed and posted successfully.",
            )
        except Exception as exc:
            modeladmin.message_user(
                request,
                str(exc),
                level="ERROR",
            )


@admin.register(WasteLoss)
class WasteLossAdmin(admin.ModelAdmin):
    list_display = ('document_number', 'warehouse', 'date', 'reason', 'status', 'created_by')
    search_fields = ('document_number',)
    list_filter = ('status', 'reason', 'warehouse')
    inlines = [WasteLossItemInline]
    actions = [confirm_and_post_selected_waste]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != WasteLoss.Status.DRAFT:
            return (
                'document_number',
                'warehouse',
                'date',
                'reason',
                'notes',
                'status',
                'created_by',
                'created_at',
            )
        return ('status', 'created_at')
