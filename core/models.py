from django.db import models

class CompanySettings(models.Model):
    company_name = models.CharField(max_length=255)
    tax_number = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    currency = models.CharField(max_length=10, default='JOD') # 3 decimal precision context
    logo = models.ImageField(upload_to='company_logo/', blank=True, null=True)
    expiration_warning_days = models.PositiveIntegerField(default=30)

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        # Enforce singleton pattern to keep only one row of settings active
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1, defaults={'company_name': 'My Supermarket'})
        return obj