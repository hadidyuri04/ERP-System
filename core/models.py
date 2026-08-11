from django.db import models
from django.utils.translation import gettext_lazy as _


class CompanySettings(models.Model):
    company_name = models.CharField(_("Company Name"), max_length=255)
    tax_number = models.CharField(_("Tax Number"), max_length=100, blank=True, null=True)
    phone = models.CharField(_("Phone"), max_length=50, blank=True, null=True)
    email = models.EmailField(_("Email"), blank=True, null=True)
    address = models.TextField(_("Address"), blank=True, null=True)
    currency = models.CharField(_("Currency"), max_length=10, default='JOD')
    logo = models.ImageField(_("Logo"), upload_to='company_logo/', blank=True, null=True)
    expiration_warning_days = models.PositiveIntegerField(_("Expiration Warning Days"), default=30)

    class Meta:
        verbose_name = _("Company Settings")
        verbose_name_plural = _("Company Settings")

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1, defaults={'company_name': 'My Supermarket'})
        return obj