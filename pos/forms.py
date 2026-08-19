from django import forms
from django.utils.translation import gettext_lazy as _
from .models import DiscountCode

class DiscountCodeForm(forms.ModelForm):
    class Meta:
        model = DiscountCode
        fields = [
            'code',
            'discount_type',
            'value',
            'max_discount_amount',
            'min_order_amount',
            'usage_limit',
            'valid_from',
            'valid_to',
            'is_active',
        ]
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. SUMMER10'}),
            'discount_type': forms.Select(attrs={'class': 'form-select'}),
            'value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'placeholder': '0.000'}),
            'max_discount_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'placeholder': 'Optional'}),
            'min_order_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'placeholder': '0.000'}),
            'usage_limit': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Unlimited if left blank'}),
            'valid_from': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'
            ),
            'valid_to': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'
            ),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure initial value rendering matches datetime-local input requirements
        if self.instance and self.instance.pk:
            if self.instance.valid_from:
                self.initial['valid_from'] = self.instance.valid_from.strftime('%Y-%m-%dT%H:%M')
            if self.instance.valid_to:
                self.initial['valid_to'] = self.instance.valid_to.strftime('%Y-%m-%dT%H:%M')