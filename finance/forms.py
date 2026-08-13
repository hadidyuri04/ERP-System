from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _

from .models import Account, JournalEntry, JournalEntryLine, PaymentVoucher, ReceiptVoucher


class ReportDateRangeForm(forms.Form):
    start_date = forms.DateField(
        label=_("Start date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    end_date = forms.DateField(
        label=_("End date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError(
                _("The start date cannot be later than the end date.")
            )
        return cleaned_data


class AsOfDateForm(forms.Form):
    as_of_date = forms.DateField(
        label=_("As of date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )


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
            raise forms.ValidationError(
                _("A customer is required for an accounts receivable receipt.")
            )
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
            raise forms.ValidationError(
                _("A supplier is required for an accounts payable payment.")
            )
        return supplier


class JournalEntryForm(forms.ModelForm):
    class Meta:
        model = JournalEntry
        fields = ["entry_number", "date", "description"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }


class JournalEntryLineForm(forms.ModelForm):
    class Meta:
        model = JournalEntryLine
        fields = [
            "account",
            "customer",
            "supplier",
            "description",
            "debit",
            "credit",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(
            is_active=True,
            allow_posting=True,
        ).order_by("code")

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("customer") and cleaned_data.get("supplier"):
            raise ValidationError(
                _("A journal line cannot reference both a customer and a supplier.")
            )
        return cleaned_data


class BaseJournalEntryLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()

        if any(self.errors):
            return

        total_debit = Decimal("0.000")
        total_credit = Decimal("0.000")
        line_count = 0

        for form in self.forms:
            data = form.cleaned_data

            if not data or data.get("DELETE"):
                continue

            debit = data.get("debit") or Decimal("0.000")
            credit = data.get("credit") or Decimal("0.000")

            if debit == 0 and credit == 0:
                continue

            line_count += 1
            total_debit += debit
            total_credit += credit

        if line_count < 2:
            raise ValidationError(
                _("A journal entry must contain at least two lines.")
            )

        if total_debit == 0:
            raise ValidationError(
                _("Journal entry total cannot be zero.")
            )

        if total_debit != total_credit:
            raise ValidationError(
                _(
                    "Journal is not balanced. Debit: %(debit)s, "
                    "Credit: %(credit)s."
                ) % {"debit": total_debit, "credit": total_credit}
            )


JournalEntryLineFormSet = inlineformset_factory(
    JournalEntry,
    JournalEntryLine,
    form=JournalEntryLineForm,
    formset=BaseJournalEntryLineFormSet,
    extra=2,
    can_delete=True,
)
