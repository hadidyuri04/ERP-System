from django.contrib import admin

from .models import SalesCreditNote, SalesInvoice, SalesInvoiceItem, SalesInvoiceStockAllocation


class SalesInvoiceItemInline(admin.TabularInline):
    model = SalesInvoiceItem
    extra = 0


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "customer", "invoice_date", "due_date", "payment_type", "status", "total")
    list_filter = ("status", "payment_type", "invoice_date")
    search_fields = ("invoice_number", "customer__name", "customer__code")
    inlines = [SalesInvoiceItemInline]


admin.site.register(SalesCreditNote)
admin.site.register(SalesInvoiceStockAllocation)
