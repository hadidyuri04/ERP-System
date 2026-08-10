from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "phone",
        "opening_balance",
        "is_active",
    )

    search_fields = (
        "code",
        "name",
        "phone",
        "email",
    )

    list_filter = (
        "is_active",
    )