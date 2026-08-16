from django.contrib import admin
from .models import POSSession, POSCashTransaction, POSSale, POSSaleItem, POSPayment


class POSCashTransactionInline(admin.TabularInline):
    model = POSCashTransaction
    extra = 0


@admin.register(POSSession)
class POSSessionAdmin(admin.ModelAdmin):
    list_display = ('session_number', 'cashier', 'warehouse', 'status', 'opened_at', 'closed_at', 'opening_balance', 'closing_balance_actual', 'difference')
    search_fields = ('session_number', 'cashier__username')
    list_filter = ('status', 'warehouse', 'opened_at')
    inlines = [POSCashTransactionInline]


@admin.register(POSCashTransaction)
class POSCashTransactionAdmin(admin.ModelAdmin):
    list_display = ('session', 'transaction_type', 'amount', 'reason', 'user', 'created_at')
    list_filter = ('transaction_type', 'created_at')


class POSSaleItemInline(admin.TabularInline):
    model = POSSaleItem
    extra = 1


class POSPaymentInline(admin.TabularInline):
    model = POSPayment
    extra = 1


@admin.register(POSSale)
class POSSaleAdmin(admin.ModelAdmin):
    list_display = ('sale_number', 'session', 'customer', 'warehouse', 'cashier', 'date', 'status', 'total', 'paid_amount', 'change_amount')
    search_fields = ('sale_number', 'customer__name', 'session__session_number')
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