from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "phone",
        "credit_limit",
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