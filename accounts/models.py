from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrator")
        ACCOUNTANT = "accountant", _("Accountant")
        CASHIER = "cashier", _("Cashier")

    role = models.CharField(
        _("Role"),
        max_length=20,
        choices=Role.choices,
        default=Role.CASHIER,
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")

    def __str__(self):
        return self.username