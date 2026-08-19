from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _

from customers.models import Customer
from suppliers.models import Supplier

from .models import (
    Account,
    FiscalPeriod,
    FiscalYear,
    JournalEntry,
    JournalEntryLine,
    PaymentVoucher,
    ReceiptVoucher,
)

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
            is_cash_equivalent=True,
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
        fields = ["entry_number", "date", "description", "cash_flow_activity"]
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


class FiscalYearForm(forms.ModelForm):
    class Meta:
        model = FiscalYear
        fields = ["year", "notes"]

    def clean_year(self):
        year = self.cleaned_data["year"]
        if year < 2000 or year > 2100:
            raise ValidationError(_("Enter a year between 2000 and 2100."))
        return year


class FiscalYearNotesForm(forms.ModelForm):
    class Meta:
        model = FiscalYear
        fields = ["notes"]


class FiscalPeriodNotesForm(forms.ModelForm):
    class Meta:
        model = FiscalPeriod
        fields = ["notes"]


class CustomerStatementForm(ReportDateRangeForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        label=_("Customer"),
    )


class SupplierStatementForm(ReportDateRangeForm):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all().order_by("name"),
        label=_("Supplier"),
    )


class ReceivablesAgingForm(AsOfDateForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all().order_by("name"),
        label=_("Customer"),
        required=False,
    )


class PayablesAgingForm(AsOfDateForm):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all().order_by("name"),
        label=_("Supplier"),
        required=False,
    )


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = [
            "code",
            "name",
            "account_type",
            "parent",
            "allow_posting",
            "is_cash_equivalent",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        excluded_ids = set()

        if self.instance.pk:
            excluded_ids.add(self.instance.pk)

            pending = list(self.instance.children.values_list("pk", flat=True))
            while pending:
                account_id = pending.pop()
                if account_id in excluded_ids:
                    continue

                excluded_ids.add(account_id)
                pending.extend(
                    Account.objects.filter(parent_id=account_id)
                    .values_list("pk", flat=True)
                )

        self.fields["parent"].queryset = (
            Account.objects.exclude(pk__in=excluded_ids).order_by("code")
        )
        self.fields["parent"].help_text = _(
            "Leave blank for a top-level account. Parent accounts must be non-posting."
        )
        self.fields["allow_posting"].help_text = _(
            "Enable only for leaf accounts that can receive journal debits and credits."
        )

    def clean(self):
        cleaned_data = super().clean()

        parent = cleaned_data.get("parent")
        account_type = cleaned_data.get("account_type")
        allow_posting = cleaned_data.get("allow_posting")
        is_cash_equivalent = cleaned_data.get("is_cash_equivalent")

        if (
            self.instance.pk
            and account_type
            and account_type != self.instance.account_type
        ):
            if self.instance.children.exclude(account_type=account_type).exists():
                self.add_error(
                    "account_type",
                    _("Change the child accounts to this type before changing the parent."),
                )
            if self.instance.journal_lines.exists():
                self.add_error(
                    "account_type",
                    _("The type of an account used in journal entries cannot be changed."),
                )

        if parent:
            if parent.account_type != account_type:
                self.add_error(
                    "parent",
                    _("The parent and child accounts must have the same type."),
                )

            if parent.allow_posting:
                self.add_error(
                    "parent",
                    _("A posting account cannot be used as a parent account."),
                )

            if not parent.is_active:
                self.add_error(
                    "parent",
                    _("An inactive account cannot be used as a parent account."),
                )

            current = parent
            visited = set()
            while current:
                if current.pk in visited:
                    self.add_error(
                        "parent",
                        _("The selected parent belongs to an invalid circular hierarchy."),
                    )
                    break
                visited.add(current.pk)
                if self.instance.pk and current.pk == self.instance.pk:
                    self.add_error(
                        "parent",
                        _("An account cannot be its own parent or descendant."),
                    )
                    break
                current = current.parent

        if (
            allow_posting
            and self.instance.pk
            and self.instance.children.exists()
        ):
            self.add_error(
                "allow_posting",
                _("An account with child accounts cannot allow direct posting."),
            )

        if is_cash_equivalent:
            if account_type != Account.AccountType.ASSET:
                self.add_error(
                    "is_cash_equivalent",
                    _("A cash-equivalent account must be an asset account."),
                )

            if not allow_posting:
                self.add_error(
                    "is_cash_equivalent",
                    _("A cash-equivalent account must allow posting."),
                )

        return cleaned_data
