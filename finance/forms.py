from django import forms

from .models import Account, PaymentVoucher, ReceiptVoucher


class CashAccountMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            account_type=Account.AccountType.ASSET,
            allow_posting=True,
            is_active=True,
        ).order_by("code")


class ReceiptVoucherForm(CashAccountMixin, forms.ModelForm):
    class Meta:
        model = ReceiptVoucher
        fields = [
            "voucher_number", "date", "customer", "received_from", "account",
            "amount", "payment_method", "reference", "description",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_customer(self):
        customer = self.cleaned_data.get("customer")
        if customer is None:
            raise forms.ValidationError("A customer is required for an accounts receivable receipt.")
        return customer


class PaymentVoucherForm(CashAccountMixin, forms.ModelForm):
    class Meta:
        model = PaymentVoucher
        fields = [
            "voucher_number", "date", "supplier", "paid_to", "account",
            "amount", "payment_method", "reference", "description",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def clean_supplier(self):
        supplier = self.cleaned_data.get("supplier")
        if supplier is None:
            raise forms.ValidationError("A supplier is required for an accounts payable payment.")
        return supplier
