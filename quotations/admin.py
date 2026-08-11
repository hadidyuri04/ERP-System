from django.contrib import admin
from .models import Quotation, QuotationItem

class QuotationItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 1

@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ('quotation_number', 'customer', 'date', 'expiry_date', 'status', 'total')
    list_filter = ('status', 'date')
    search_fields = ('quotation_number', 'customer__name')
    inlines = [QuotationItemInline]