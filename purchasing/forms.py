from django import forms
from django.forms import inlineformset_factory

from .models import PurchaseInvoice, PurchaseInvoiceItem


class PurchaseInvoiceForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoice
        # tax_amount is deliberately absent: it is calculated from each line's
        # product tax rate. Leaving it on the form is what allowed the same tax
        # to be entered twice and counted twice.
        fields = [
            "supplier", "warehouse", "supplier_invoice_number", "invoice_date",
            "due_date", "payment_type", "discount_amount",
            "additional_expenses", "paid_amount", "notes",
        ]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class PurchaseInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoiceItem
        # tax_amount is calculated from the product's tax rate, not typed.
        fields = [
            "product", "quantity", "unit_cost", "discount_amount",
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
