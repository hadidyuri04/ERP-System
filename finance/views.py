from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import accountant_required

from .forms import (
    AsOfDateForm,
    CustomerStatementForm,
    FiscalPeriodNotesForm,
    FiscalYearForm,
    FiscalYearNotesForm,
    JournalEntryForm,
    JournalEntryLineFormSet,
    PayablesAgingForm,
    PaymentVoucherForm,
    ReceivablesAgingForm,
    ReceiptVoucherForm,
    ReportDateRangeForm,
    SupplierStatementForm,
)
from .models import (
    Account,
    FiscalPeriod,
    FiscalYear,
    JournalEntry,
    PaymentVoucher,
    PeriodStatus,
    ReceiptVoucher,
)
from .reports import (
    generate_balance_sheet,
    generate_cash_flow_statement,
    generate_customer_statement,
    generate_general_ledger,
    generate_income_statement,
    generate_payables_aging,
    generate_receivables_aging,
    generate_supplier_statement,
    generate_trial_balance,
)
from .services import (
    create_fiscal_year,
    get_period_summary,
    post_journal_entry,
    post_payment_voucher,
    post_receipt_voucher,
    reverse_journal_entry,
    set_fiscal_year_status,
    set_period_status,
)


def _message_validation_error(request, exc):
    messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


@login_required
@accountant_required
def account_list_view(request):
    accounts = Account.objects.select_related("parent").order_by("code")
    return render(request, "finance/account_list.html", {"accounts": accounts})


@login_required
@accountant_required
def journal_list_view(request):
    entries = JournalEntry.objects.select_related("created_by", "approved_by").order_by("-date", "-id")
    status = request.GET.get("status")
    if status:
        entries = entries.filter(status=status)
    return render(request, "finance/journal_list.html", {"entries": entries, "statuses": JournalEntry.Status.choices})


@login_required
@accountant_required
def journal_detail_view(request, pk):
    entry = get_object_or_404(
        JournalEntry.objects.select_related("created_by", "approved_by").prefetch_related("lines__account"),
        pk=pk,
    )
    return render(request, "finance/journal_detail.html", {"entry": entry})


@login_required
@accountant_required
@require_POST
def post_journal_view(request, pk):
    try:
        post_journal_entry(pk, request.user)
        messages.success(request, _("Journal entry posted successfully."))
    except ValidationError as exc:
        _message_validation_error(request, exc)
    return redirect("finance:journal_detail", pk=pk)


@login_required
@accountant_required
def receipt_list_view(request):
    vouchers = ReceiptVoucher.objects.select_related("customer", "account").order_by("-date", "-id")
    return render(request, "finance/receipt_list.html", {"vouchers": vouchers})


@login_required
@accountant_required
def receipt_create_view(request):
    form = ReceiptVoucherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        voucher = form.save(commit=False)
        voucher.created_by = request.user
        voucher.save()
        if request.POST.get("action") == "confirm":
            try:
                post_receipt_voucher(voucher.id, request.user)
            except ValidationError as exc:
                _message_validation_error(request, exc)
                return redirect("finance:receipt_detail", pk=voucher.pk)
        messages.success(request, _("Receipt voucher saved successfully."))
        return redirect("finance:receipt_detail", pk=voucher.pk)
    return render(request, "finance/receipt_voucher_form.html", {"form": form})


@login_required
@accountant_required
def receipt_detail_view(request, pk):
    voucher = get_object_or_404(ReceiptVoucher.objects.select_related("customer", "account", "created_by"), pk=pk)
    return render(request, "finance/voucher_detail.html", {"voucher": voucher, "kind": _("Receipt voucher")})


@login_required
@accountant_required
@require_POST
def receipt_post_view(request, pk):
    try:
        post_receipt_voucher(pk, request.user)
        messages.success(request, _("Receipt voucher posted successfully."))
    except ValidationError as exc:
        _message_validation_error(request, exc)
    return redirect("finance:receipt_detail", pk=pk)


@login_required
@accountant_required
def payment_list_view(request):
    vouchers = PaymentVoucher.objects.select_related("supplier", "account").order_by("-date", "-id")
    return render(request, "finance/payment_list.html", {"vouchers": vouchers})


@login_required
@accountant_required
def payment_create_view(request):
    form = PaymentVoucherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        voucher = form.save(commit=False)
        voucher.created_by = request.user
        voucher.save()
        if request.POST.get("action") == "confirm":
            try:
                post_payment_voucher(voucher.id, request.user)
            except ValidationError as exc:
                _message_validation_error(request, exc)
                return redirect("finance:payment_detail", pk=voucher.pk)
        messages.success(request, _("Payment voucher saved successfully."))
        return redirect("finance:payment_detail", pk=voucher.pk)
    return render(request, "finance/payment_voucher_form.html", {"form": form})


@login_required
@accountant_required
def payment_detail_view(request, pk):
    voucher = get_object_or_404(PaymentVoucher.objects.select_related("supplier", "account", "created_by"), pk=pk)
    return render(request, "finance/voucher_detail.html", {"voucher": voucher, "kind": _("Payment voucher")})


@login_required
@accountant_required
@require_POST
def payment_post_view(request, pk):
    try:
        post_payment_voucher(pk, request.user)
        messages.success(request, _("Payment voucher posted successfully."))
    except ValidationError as exc:
        _message_validation_error(request, exc)
    return redirect("finance:payment_detail", pk=pk)


@login_required
@accountant_required
def general_ledger_view(request):
    account_id = request.GET.get("account_id")
    start_date = request.GET.get("start_date") or None
    end_date = request.GET.get("end_date") or None
    context = {
        "accounts": Account.objects.filter(is_active=True).order_by("code"),
        "selected_account_id": account_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    if account_id:
        try:
            context.update(generate_general_ledger(account_id, start_date, end_date))
        except ValueError as exc:
            messages.error(request, str(exc))
    return render(request, "reports/general_ledger.html", context)


@login_required
@accountant_required
def trial_balance_view(request):
    start_date = request.GET.get("start_date") or None
    end_date = request.GET.get("end_date") or None
    tb_data = generate_trial_balance(start_date, end_date)
    return render(request, "reports/trial_balance.html", {"tb_data": tb_data, "start_date": start_date, "end_date": end_date})


@login_required
@accountant_required
@transaction.atomic
def journal_create_view(request):
    journal = JournalEntry(
        created_by=request.user,
        source_type=JournalEntry.SourceType.MANUAL,
    )

    form = JournalEntryForm(
        request.POST or None,
        instance=journal,
    )

    formset = JournalEntryLineFormSet(
        request.POST or None,
        instance=journal,
        prefix="lines",
    )

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        journal = form.save(commit=False)
        journal.created_by = request.user
        journal.source_type = JournalEntry.SourceType.MANUAL
        journal.status = JournalEntry.Status.DRAFT
        journal.save()

        formset.instance = journal
        formset.save()

        messages.success(request, _("Journal entry saved as draft."))

        return redirect(
            "finance:journal_detail",
            pk=journal.pk,
        )

    return render(
        request,
        "finance/journal_form.html",
        {
            "form": form,
            "formset": formset,
        },
    )


@login_required
@accountant_required
@require_POST
def reverse_journal_view(request, pk):
    get_object_or_404(JournalEntry, pk=pk)
    try:
        reversal = reverse_journal_entry(
            pk,
            request.user,
            request.POST.get("reason", ""),
        )
        messages.success(
            request,
            _("Journal entry reversed successfully."),
        )
        return redirect("finance:journal_detail", pk=reversal.pk)
    except ValidationError as exc:
        _message_validation_error(request, exc)
        return redirect("finance:journal_detail", pk=pk)


@login_required
@accountant_required
def income_statement_view(request):
    date_form = ReportDateRangeForm(request.GET or None)
    statement = None

    if not date_form.is_bound:
        statement = generate_income_statement()
    elif date_form.is_valid():
        statement = generate_income_statement(
            start_date=date_form.cleaned_data["start_date"],
            end_date=date_form.cleaned_data["end_date"],
        )

    return render(
        request,
        "reports/income_statement.html",
        {
            "statement": statement,
            "date_form": date_form,
        },
    )


@login_required
@accountant_required
def balance_sheet_view(request):
    date_form = AsOfDateForm(request.GET or None)
    balance_sheet = None

    if not date_form.is_bound:
        balance_sheet = generate_balance_sheet()
    elif date_form.is_valid():
        balance_sheet = generate_balance_sheet(
            as_of_date=date_form.cleaned_data["as_of_date"],
        )

    return render(
        request,
        "reports/balance_sheet.html",
        {
            "balance_sheet": balance_sheet,
            "date_form": date_form,
        },
    )


@login_required
@accountant_required
def cash_flow_statement_view(request):
    date_form = ReportDateRangeForm(request.GET or None)
    statement = None

    if not date_form.is_bound:
        statement = generate_cash_flow_statement()
    elif date_form.is_valid():
        statement = generate_cash_flow_statement(
            start_date=date_form.cleaned_data["start_date"],
            end_date=date_form.cleaned_data["end_date"],
        )

    return render(
        request,
        "reports/cash_flow_statement.html",
        {"date_form": date_form, "statement": statement},
    )

@login_required
@accountant_required
def fiscal_period_list_view(request):
    years = list(
        FiscalYear.objects.prefetch_related(
            "periods__closed_by",
            "actions__performed_by",
        ).order_by("-year")
    )
    for fiscal_year in years:
        for period in fiscal_year.periods.all():
            period.summary = get_period_summary(period)
    return render(
        request,
        "finance/fiscal_period_list.html",
        {"years": years},
    )


@login_required
@accountant_required
def fiscal_year_history_view(request, pk):
    fiscal_year = get_object_or_404(FiscalYear, pk=pk)
    actions = fiscal_year.actions.select_related(
        "period",
        "performed_by",
    ).order_by("-performed_at", "-id")
    page_obj = Paginator(actions, 10).get_page(request.GET.get("page"))
    return render(
        request,
        "finance/fiscal_year_history.html",
        {
            "fiscal_year": fiscal_year,
            "page_obj": page_obj,
        },
    )


@login_required
@accountant_required
def fiscal_year_create_view(request):
    form = FiscalYearForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            create_fiscal_year(
                form.cleaned_data["year"],
                notes=form.cleaned_data["notes"],
            )
            messages.success(request, _("Fiscal year created successfully."))
            return redirect("finance:fiscal_period_list")
        except ValidationError as exc:
            _message_validation_error(request, exc)

    return render(
        request,
        "finance/fiscal_year_form.html",
        {"form": form},
    )


@login_required
@accountant_required
@require_POST
def fiscal_period_status_view(request, pk):
    get_object_or_404(FiscalPeriod, pk=pk)
    status = request.POST.get("status")

    if status not in PeriodStatus.values:
        messages.error(request, _("Invalid period status."))
    else:
        try:
            set_period_status(
                pk,
                status,
                request.user,
                reason=request.POST.get("reason", ""),
            )
            messages.success(request, _("Accounting period updated."))
        except ValidationError as exc:
            _message_validation_error(request, exc)

    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
@require_POST
def fiscal_year_status_view(request, pk):
    get_object_or_404(FiscalYear, pk=pk)
    status = request.POST.get("status")

    if status not in PeriodStatus.values:
        messages.error(request, _("Invalid fiscal-year status."))
    else:
        try:
            set_fiscal_year_status(
                pk,
                status,
                request.user,
                reason=request.POST.get("reason", ""),
            )
            messages.success(request, _("Fiscal year updated."))
        except ValidationError as exc:
            _message_validation_error(request, exc)

    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
@require_POST
def fiscal_period_notes_view(request, pk):
    period = get_object_or_404(FiscalPeriod, pk=pk)
    form = FiscalPeriodNotesForm(request.POST, instance=period)
    if form.is_valid():
        form.save()
        messages.success(request, _("Fiscal-period notes updated."))
    else:
        messages.error(request, _("Fiscal-period notes could not be updated."))
    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
@require_POST
def fiscal_year_notes_view(request, pk):
    fiscal_year = get_object_or_404(FiscalYear, pk=pk)
    form = FiscalYearNotesForm(request.POST, instance=fiscal_year)
    if form.is_valid():
        form.save()
        messages.success(request, _("Fiscal-year notes updated."))
    else:
        messages.error(request, _("Fiscal-year notes could not be updated."))
    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
def customer_statement_view(request):
    form = CustomerStatementForm(request.GET or None)
    statement = None

    if form.is_valid():
        statement = generate_customer_statement(
            customer=form.cleaned_data["customer"],
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
        )

    return render(
        request,
        "reports/customer_statement.html",
        {"form": form, "statement": statement},
    )


@login_required
@accountant_required
def supplier_statement_view(request):
    form = SupplierStatementForm(request.GET or None)
    statement = None

    if form.is_valid():
        statement = generate_supplier_statement(
            supplier=form.cleaned_data["supplier"],
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
        )

    return render(
        request,
        "reports/supplier_statement.html",
        {"form": form, "statement": statement},
    )


@login_required
@accountant_required
def receivables_aging_view(request):
    form = ReceivablesAgingForm(
        request.GET or None,
        initial={"as_of_date": timezone.localdate()},
    )
    report = None

    if not form.is_bound:
        report = generate_receivables_aging()
    elif form.is_valid():
        report = generate_receivables_aging(
            as_of_date=form.cleaned_data["as_of_date"],
            customer=form.cleaned_data["customer"],
        )

    return render(
        request,
        "reports/aging_report.html",
        {
            "form": form,
            "report": report,
            "title": _("Accounts receivable aging"),
            "description": _("Outstanding customer balances grouped by age."),
            "party_label": _("Customer"),
            "report_kind": "receivable",
        },
    )


@login_required
@accountant_required
def payables_aging_view(request):
    form = PayablesAgingForm(
        request.GET or None,
        initial={"as_of_date": timezone.localdate()},
    )
    report = None

    if not form.is_bound:
        report = generate_payables_aging()
    elif form.is_valid():
        report = generate_payables_aging(
            as_of_date=form.cleaned_data["as_of_date"],
            supplier=form.cleaned_data["supplier"],
        )

    return render(
        request,
        "reports/aging_report.html",
        {
            "form": form,
            "report": report,
            "title": _("Accounts payable aging"),
            "description": _("Outstanding supplier balances grouped by age."),
            "party_label": _("Supplier"),
            "report_kind": "payable",
        },
    )
