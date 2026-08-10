from django.contrib import admin

from .models import PurchaseInvoice, PurchaseInvoiceItem


class PurchaseInvoiceItemInline(admin.TabularInline):
    model = PurchaseInvoiceItem
    extra = 1


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_number",
        "supplier",
        "warehouse",
        "invoice_date",
        "payment_type",
        "status",
        "total",
    )

    list_filter = (
        "status",
        "payment_type",
        "invoice_date",
    )

    search_fields = (
        "invoice_number",
        "supplier__name",
        "supplier_invoice_number",
    )

    inlines = [
        PurchaseInvoiceItemInline,
    ]