from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _
from .models import Quotation, QuotationItem

class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        # tax_amount is calculated from each line's product tax rate.
        fields = ['customer', 'date', 'expiry_date', 'discount_amount', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'discount_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

class QuotationItemForm(forms.ModelForm):
    class Meta:
        model = QuotationItem
        fields = ['product', 'quantity', 'unit_price', 'discount_amount']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select product-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control qty-input', 'step': '0.001'}),
            'unit_price': forms.NumberInput(attrs={'class': 'form-control price-input', 'step': '0.001'}),
            'discount_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001'}),
        }

QuotationItemFormSet = inlineformset_factory(
    Quotation,
    QuotationItem,
    form=QuotationItemForm,
    extra=1,
    can_delete=True
)