from django.contrib import admin
from .models import POSSale, POSSaleItem, POSPayment


class POSSaleItemInline(admin.TabularInline):
    model = POSSaleItem
    extra = 1


class POSPaymentInline(admin.TabularInline):
    model = POSPayment
    extra = 1


@admin.register(POSSale)
class POSSaleAdmin(admin.ModelAdmin):
    list_display = ('sale_number', 'customer', 'warehouse', 'cashier', 'date', 'status', 'total', 'paid_amount', 'change_amount')
    search_fields = ('sale_number', 'customer__name')
    list_filter = ('status', 'warehouse', 'date')
    inlines = [POSSaleItemInline, POSPaymentInline]


@admin.register(POSSaleItem)
class POSSaleItemAdmin(admin.ModelAdmin):
    list_display = ('sale', 'product', 'quantity', 'unit_price', 'line_total')
    search_fields = ('sale__sale_number', 'product__name')
    list_filter = ('product',)


@admin.register(POSPayment)
class POSPaymentAdmin(admin.ModelAdmin):
    list_display = ('sale', 'payment_method', 'amount', 'reference_number', 'received_at', 'created_by')
    search_fields = ('sale__sale_number', 'reference_number')
    list_filter = ('payment_method', 'received_at')