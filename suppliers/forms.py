from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "code", "name", "phone", "email", "address", "tax_number",
            "opening_balance", "notes", "is_active",
        ]

