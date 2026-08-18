from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from .models import PurchaseInvoice, PurchaseInvoiceItem


class PurchaseInvoiceForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoice
        # tax_amount and discount_amount are deliberately absent: both are
        # totalled from the item lines. Keeping header boxes for them let the
        # same figure be entered twice, and made the stored discount compound
        # every time an invoice was re-saved.
        fields = [
            "supplier", "warehouse", "supplier_invoice_number", "invoice_date",
            "due_date", "payment_type",
            "additional_expenses", "paid_amount", "notes",
        ]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    # These are blank=False on the model, so Django would demand a value and
    # fail validation on an empty box. On money fields empty means zero.
    OPTIONAL_MONEY = ("additional_expenses", "paid_amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.OPTIONAL_MONEY:
            if name in self.fields:
                self.fields[name].required = False
                self.fields[name].initial = 0

    def clean_additional_expenses(self):
        return self.cleaned_data.get("additional_expenses") or Decimal("0.000")

    def clean_paid_amount(self):
        paid = self.cleaned_data.get("paid_amount") or Decimal("0.000")
        if paid < 0:
            raise forms.ValidationError(_("Paid amount cannot be negative."))
        return paid


class PurchaseInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseInvoiceItem
        # tax_amount is calculated from the product's tax rate, not typed.
        fields = [
            "product", "quantity", "unit_cost", "discount_amount",
            "batch_number", "expiration_date",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "discount_amount" in self.fields:
            self.fields["discount_amount"].required = False
            self.fields["discount_amount"].initial = 0

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or Decimal("0.000")
        widgets = {"expiration_date": forms.DateInput(attrs={"type": "date"})}


PurchaseInvoiceItemFormSet = inlineformset_factory(
    PurchaseInvoice,
    PurchaseInvoiceItem,
    form=PurchaseInvoiceItemForm,
    extra=1,
    can_delete=True,
)
