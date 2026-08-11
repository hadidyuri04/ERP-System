from django.db import models
from django.conf import settings
from customers.models import Customer
from inventory.models import Product

class Quotation(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SENT = 'SENT', 'Sent'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        REJECTED = 'REJECTED', 'Rejected'
        EXPIRED = 'EXPIRED', 'Expired'

    quotation_number = models.CharField(max_length=50, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    date = models.DateField()
    expiry_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    total = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.quotation_number


class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=3)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=3, default=0.000)
    line_total = models.DecimalField(max_digits=12, decimal_places=3)

    def __str__(self):
        return f"{self.quotation.quotation_number} - {self.product.name}"