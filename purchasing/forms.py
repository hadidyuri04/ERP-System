from django import forms
from django.forms import inlineformset_factory

from .models import PurchaseInvoice, PurchaseInvoiceItem


class PurchaseInvoiceForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoice
        fields = [
            "supplier", "warehouse", "supplier_invoice_number", "invoice_date",
            "due_date", "payment_type", "discount_amount", "tax_amount",
            "additional_expenses", "paid_amount", "notes",
        ]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class PurchaseInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoiceItem
        fields = [
            "product", "quantity", "unit_cost", "discount_amount", "tax_amount",
            "batch_number", "expiration_date",
        ]
        widgets = {"expiration_date": forms.DateInput(attrs={"type": "date"})}


PurchaseInvoiceItemFormSet = inlineformset_factory(
    PurchaseInvoice,
    PurchaseInvoiceItem,
    form=PurchaseInvoiceItemForm,
    extra=1,
    can_delete=True,
)
