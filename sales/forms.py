from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from finance.models import Account, ReceiptVoucher
from .models import SalesCreditNote, SalesInvoice, SalesInvoiceItem


class SalesInvoiceForm(forms.ModelForm):
    class Meta:
        model = SalesInvoice
        fields = [
            "customer", "warehouse", "invoice_date", "due_date",
            "payment_type", "payment_account", "notes",
        ]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_account"].queryset = Account.objects.filter(
            is_active=True, allow_posting=True, is_cash_equivalent=True,
        ).order_by("code")

    def clean(self):
        cleaned = super().clean()
        invoice_date = cleaned.get("invoice_date")
        due_date = cleaned.get("due_date")
        payment_type = cleaned.get("payment_type")
        if invoice_date and due_date and due_date < invoice_date:
            self.add_error("due_date", _("Due date cannot precede invoice date."))
        if payment_type == SalesInvoice.PaymentType.CASH:
            cleaned["due_date"] = invoice_date
            if not cleaned.get("payment_account"):
                self.add_error("payment_account", _("Select a cash or bank account for a cash invoice."))
        else:
            cleaned["payment_account"] = None
        return cleaned


class SalesInvoiceItemForm(forms.ModelForm):
    class Meta:
        model = SalesInvoiceItem
        fields = ["product", "quantity", "unit_price", "discount_amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["discount_amount"].required = False
        self.fields["discount_amount"].initial = 0

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or Decimal("0.000")

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get("quantity") or Decimal("0.000")
        price = cleaned.get("unit_price") or Decimal("0.000")
        discount = cleaned.get("discount_amount") or Decimal("0.000")
        if discount < 0:
            self.add_error("discount_amount", _("Discount cannot be negative."))
        if discount > quantity * price:
            self.add_error("discount_amount", _("Discount cannot exceed the line value."))
        return cleaned


SalesInvoiceItemFormSet = inlineformset_factory(
    SalesInvoice,
    SalesInvoiceItem,
    form=SalesInvoiceItemForm,
    extra=1,
    can_delete=True,
)


class CustomerInvoicePaymentForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    account = forms.ModelChoiceField(
        queryset=Account.objects.none(), label=_("Cash / Bank Account")
    )
    amount = forms.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal("0.001"))
    payment_method = forms.ChoiceField(choices=ReceiptVoucher.PaymentMethod.choices)
    reference = forms.CharField(max_length=100, required=False)

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        self.fields["account"].queryset = Account.objects.filter(
            is_active=True, allow_posting=True, is_cash_equivalent=True,
        ).order_by("code")

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.invoice and amount > self.invoice.outstanding_amount:
            raise forms.ValidationError(_("Payment cannot exceed the invoice outstanding balance."))
        return amount


class SalesCreditNoteForm(forms.ModelForm):
    class Meta:
        model = SalesCreditNote
        fields = ["date", "reason"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }
