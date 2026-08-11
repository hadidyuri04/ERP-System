from django.db import models
from django.utils.translation import gettext_lazy as _


class Customer(models.Model):
    code = models.CharField(
        _("Code"),
        max_length=20,
        unique=True,
    )

    name = models.CharField(
        _("Name"),
        max_length=200,
    )

    phone = models.CharField(
        _("Phone"),
        max_length=30,
        blank=True,
    )

    email = models.EmailField(
        _("Email"),
        blank=True,
    )

    address = models.TextField(
        _("Address"),
        blank=True,
    )

    tax_number = models.CharField(
        _("Tax Number"),
        max_length=50,
        blank=True,
    )

    credit_limit = models.DecimalField(
        _("Credit Limit"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    opening_balance = models.DecimalField(
        _("Opening Balance"),
        max_digits=14,
        decimal_places=3,
        default=0,
    )

    notes = models.TextField(
        _("Notes"),
        blank=True,
    )

    is_active = models.BooleanField(
        _("Is Active"),
        default=True,
    )

    created_at = models.DateTimeField(
        _("Created At"),
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        _("Updated At"),
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

    def __str__(self):
        return f"{self.code} - {self.name}"