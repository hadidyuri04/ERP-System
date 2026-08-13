from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "code", "name", "phone", "email", "address", "tax_number",
            "credit_limit", "opening_balance", "notes", "is_active",
        ]

