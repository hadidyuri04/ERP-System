from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from customers.models import Customer
from inventory.models import Product

class Quotation(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', _('Draft')
        SENT = 'SENT', _('Sent')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        REJECTED = 'REJECTED', _('Rejected')
        EXPIRED = 'EXPIRED', _('Expired')

    quotation_number = models.CharField(_("Quotation Number"), max_length=50, unique=True)
    customer = models.ForeignKey(Customer, verbose_name=_("Customer"), on_delete=models.PROTECT)
    date = models.DateField(_("Date"))
    expiry_date = models.DateField(_("Expiry Date"))
    status = models.CharField(_("Status"), max_length=20, choices=Status.choices, default=Status.DRAFT)
    
    subtotal = models.DecimalField(_("Subtotal"), max_digits=12, decimal_places=3, default=0.000)
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=12, decimal_places=3, default=0.000)
    total = models.DecimalField(_("Total"), max_digits=12, decimal_places=3, default=0.000)
    
    notes = models.TextField(_("Notes"), blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("Created By"), on_delete=models.PROTECT)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Quotation")
        verbose_name_plural = _("Quotations")

    def __str__(self):
        return self.quotation_number


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, verbose_name=_("Quotation"), related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, verbose_name=_("Product"), on_delete=models.PROTECT)
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(_("Unit Price"), max_digits=12, decimal_places=3)
    discount_amount = models.DecimalField(_("Discount Amount"), max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(_("Tax Amount"), max_digits=12, decimal_places=3, default=0.000)
    line_total = models.DecimalField(_("Line Total"), max_digits=12, decimal_places=3)

    class Meta:
        verbose_name = _("Quotation Item")
        verbose_name_plural = _("Quotation Items")

    def __str__(self):
        return f"{self.quotation.quotation_number} - {self.product.name}"