from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import accountant_required
from .audit import compare_snapshots, record_finance_audit, snapshot
from .exports import (
    aging_document,
    audit_log_document,
    balance_sheet_document,
    cash_flow_document,
    export_response,
    general_ledger_document,
    income_statement_document,
    party_statement_document,
    trial_balance_document,
)
from .forms import (
    AccountForm,
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
    FinanceAuditLog,
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
    validate_journal_entry_for_posting,
)


@login_required
@accountant_required
def report_center_view(request):
    """A concise, grouped entry point for all financial reports."""
    return render(request, "reports/report_center.html")


def _message_validation_error(request, exc):
    messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))


def _build_account_tree():
    accounts = list(
        Account.objects.select_related("parent").order_by("code")
    )

    children = {}
    for account in accounts:
        children.setdefault(account.parent_id, []).append(account)

    rows = []
    visited = set()

    def add_children(parent_id, depth):
        for account in children.get(parent_id, []):
            if account.pk in visited:
                continue

            visited.add(account.pk)
            rows.append({
                "account": account,
                "depth": depth,
                "indent": depth * 24,
                "has_children": bool(children.get(account.pk)),
            })
            add_children(account.pk, depth + 1)

    add_children(None, 0)

    # Safely show any old accounts with an invalid hierarchy.
    for account in accounts:
        if account.pk not in visited:
            rows.append({
                "account": account,
                "depth": 0,
                "indent": 0,
                "has_children": bool(children.get(account.pk)),
            })

    return rows


ACCOUNT_AUDIT_FIELDS = (
    "code",
    "name",
    "account_type",
    "parent",
    "allow_posting",
    "is_cash_equivalent",
    "is_active",
)


@login_required
@accountant_required
def account_list_view(request):
    return render(
        request,
        "finance/account_list.html",
        {"account_rows": _build_account_tree()},
    )


@login_required
@accountant_required
@transaction.atomic
def account_create_view(request):
    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            account = form.save()
            after = snapshot(account, ACCOUNT_AUDIT_FIELDS)
            record_finance_audit(
                actor=request.user,
                action=FinanceAuditLog.Action.CREATED,
                instance=account,
                changes={
                    field: {"before": None, "after": value}
                    for field, value in after.items()
                },
            )
            messages.success(
                request,
                _("Account %(account)s created successfully.")
                % {"account": account},
            )
            return redirect("finance:account_list")
    else:
        form = AccountForm()

    return render(
        request,
        "finance/account_form.html",
        {
            "form": form,
            "page_title": _("Create account"),
        },
    )


@login_required
@accountant_required
@transaction.atomic
def account_update_view(request, pk):
    account = get_object_or_404(Account, pk=pk)
    before = snapshot(account, ACCOUNT_AUDIT_FIELDS)

    if request.method == "POST":
        form = AccountForm(request.POST, instance=account)
        if form.is_valid():
            account = form.save()
            changes = compare_snapshots(
                before,
                snapshot(account, ACCOUNT_AUDIT_FIELDS),
            )
            if changes:
                record_finance_audit(
                    actor=request.user,
                    action=FinanceAuditLog.Action.UPDATED,
                    instance=account,
                    changes=changes,
                )
            messages.success(
                request,
                _("Account %(account)s updated successfully.")
                % {"account": account},
            )
            return redirect("finance:account_list")
    else:
        form = AccountForm(instance=account)

    return render(
        request,
        "finance/account_form.html",
        {
            "form": form,
            "account": account,
            "page_title": _("Edit account"),
        },
    )

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
    posting_errors = []
    if entry.status == JournalEntry.Status.DRAFT:
        try:
            validate_journal_entry_for_posting(entry, lock_period=False)
        except ValidationError as exc:
            posting_errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
    return render(
        request,
        "finance/journal_detail.html",
        {"entry": entry, "posting_errors": posting_errors},
    )


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
@transaction.atomic
def receipt_create_view(request):
    form = ReceiptVoucherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        voucher = form.save(commit=False)
        voucher.created_by = request.user
        voucher.save()
        record_finance_audit(
            actor=request.user,
            action=FinanceAuditLog.Action.CREATED,
            instance=voucher,
            changes={
                "status": {"before": None, "after": voucher.status},
                "amount": {"before": None, "after": str(voucher.amount)},
                "customer": {"before": None, "after": str(voucher.customer)},
            },
        )
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
@transaction.atomic
def payment_create_view(request):
    form = PaymentVoucherForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        voucher = form.save(commit=False)
        voucher.created_by = request.user
        voucher.save()
        record_finance_audit(
            actor=request.user,
            action=FinanceAuditLog.Action.CREATED,
            instance=voucher,
            changes={
                "status": {"before": None, "after": voucher.status},
                "amount": {"before": None, "after": str(voucher.amount)},
                "supplier": {"before": None, "after": str(voucher.supplier)},
            },
        )
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
    ledger = None
    if account_id:
        try:
            ledger = generate_general_ledger(account_id, start_date, end_date)
            context.update(ledger)
        except ValueError as exc:
            messages.error(request, str(exc))
    if ledger:
        export = export_response(
            request,
            request.GET.get("export"),
            "general-ledger",
            general_ledger_document(ledger, start_date, end_date),
        )
        if export:
            return export
    return render(request, "reports/general_ledger.html", context)


@login_required
@accountant_required
def trial_balance_view(request):
    start_date = request.GET.get("start_date") or None
    end_date = request.GET.get("end_date") or None

    tb_data = generate_trial_balance(start_date, end_date)
    export = export_response(
        request,
        request.GET.get("export"),
        "trial-balance",
        trial_balance_document(tb_data, start_date, end_date),
    )
    if export:
        return export

    return render(
        request,
        "reports/trial_balance.html",
        {
            "tb_data": tb_data,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


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

    form_is_valid = form.is_valid() if request.method == "POST" else False
    formset_is_valid = formset.is_valid() if request.method == "POST" else False
    if request.method == "POST" and form_is_valid and formset_is_valid:
        journal = form.save(commit=False)
        journal.created_by = request.user
        journal.source_type = JournalEntry.SourceType.MANUAL
        journal.status = JournalEntry.Status.DRAFT
        journal.save()

        formset.instance = journal
        formset.save()
        record_finance_audit(
            actor=request.user,
            action=FinanceAuditLog.Action.CREATED,
            instance=journal,
            changes={
                "status": {"before": None, "after": journal.status},
                "date": {"before": None, "after": journal.date.isoformat()},
                "lines": {"before": 0, "after": journal.lines.count()},
            },
        )

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
            "page_title": _("New journal entry"),
        },
    )


def _journal_lines_snapshot(journal):
    return [
        {
            "account": line.account.code,
            "customer": str(line.customer_id or ""),
            "supplier": str(line.supplier_id or ""),
            "description": line.description,
            "debit": str(line.debit),
            "credit": str(line.credit),
        }
        for line in journal.lines.select_related("account").order_by("pk")
    ]


@login_required
@accountant_required
@transaction.atomic
def journal_update_view(request, pk):
    queryset = JournalEntry.objects.prefetch_related("lines__account")
    if request.method == "POST":
        queryset = queryset.select_for_update()
    journal = get_object_or_404(queryset, pk=pk)

    if (
        journal.status != JournalEntry.Status.DRAFT
        or journal.source_type != JournalEntry.SourceType.MANUAL
    ):
        messages.error(request, _("Only draft manual journal entries can be edited."))
        return redirect("finance:journal_detail", pk=journal.pk)

    before = snapshot(
        journal,
        ["entry_number", "date", "description", "cash_flow_activity"],
    )
    before_lines = _journal_lines_snapshot(journal)
    form = JournalEntryForm(request.POST or None, instance=journal)
    formset = JournalEntryLineFormSet(
        request.POST or None,
        instance=journal,
        prefix="lines",
    )

    form_is_valid = form.is_valid() if request.method == "POST" else False
    formset_is_valid = formset.is_valid() if request.method == "POST" else False
    if request.method == "POST" and form_is_valid and formset_is_valid:
        journal = form.save()
        formset.save()
        after = snapshot(
            journal,
            ["entry_number", "date", "description", "cash_flow_activity"],
        )
        changes = compare_snapshots(before, after)
        after_lines = _journal_lines_snapshot(journal)
        if before_lines != after_lines:
            changes["lines"] = {"before": before_lines, "after": after_lines}
        if changes:
            record_finance_audit(
                actor=request.user,
                action=FinanceAuditLog.Action.UPDATED,
                instance=journal,
                changes=changes,
            )
        messages.success(request, _("Journal entry updated successfully."))
        return redirect("finance:journal_detail", pk=journal.pk)

    return render(
        request,
        "finance/journal_form.html",
        {
            "form": form,
            "formset": formset,
            "journal": journal,
            "page_title": _("Edit journal entry"),
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

    if statement:
        export = export_response(
            request,
            request.GET.get("export"),
            "income-statement",
            income_statement_document(
                statement,
                date_form.cleaned_data.get("start_date") if date_form.is_bound else None,
                date_form.cleaned_data.get("end_date") if date_form.is_bound else None,
            ),
        )
        if export:
            return export

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

    if balance_sheet:
        as_of_date = (
            date_form.cleaned_data.get("as_of_date") if date_form.is_bound else None
        )
        export = export_response(
            request,
            request.GET.get("export"),
            "balance-sheet",
            balance_sheet_document(balance_sheet, as_of_date),
        )
        if export:
            return export

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

    if statement:
        export = export_response(
            request,
            request.GET.get("export"),
            "cash-flow-statement",
            cash_flow_document(statement),
        )
        if export:
            return export

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
                user=request.user,
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
@transaction.atomic
@require_POST
def fiscal_period_notes_view(request, pk):
    period = get_object_or_404(FiscalPeriod, pk=pk)
    before = period.notes
    form = FiscalPeriodNotesForm(request.POST, instance=period)
    if form.is_valid():
        period = form.save()
        if before != period.notes:
            record_finance_audit(
                actor=request.user,
                action=FinanceAuditLog.Action.UPDATED,
                instance=period,
                changes={"notes": {"before": before, "after": period.notes}},
            )
        messages.success(request, _("Fiscal-period notes updated."))
    else:
        messages.error(request, _("Fiscal-period notes could not be updated."))
    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
@transaction.atomic
@require_POST
def fiscal_year_notes_view(request, pk):
    fiscal_year = get_object_or_404(FiscalYear, pk=pk)
    before = fiscal_year.notes
    form = FiscalYearNotesForm(request.POST, instance=fiscal_year)
    if form.is_valid():
        fiscal_year = form.save()
        if before != fiscal_year.notes:
            record_finance_audit(
                actor=request.user,
                action=FinanceAuditLog.Action.UPDATED,
                instance=fiscal_year,
                changes={"notes": {"before": before, "after": fiscal_year.notes}},
            )
        messages.success(request, _("Fiscal-year notes updated."))
    else:
        messages.error(request, _("Fiscal-year notes could not be updated."))
    return redirect("finance:fiscal_period_list")


@login_required
@accountant_required
def audit_log_view(request):
    logs = FinanceAuditLog.objects.select_related("actor")
    action = request.GET.get("action", "")
    entity_type = request.GET.get("entity_type", "")
    query = request.GET.get("q", "").strip()

    if action in FinanceAuditLog.Action.values:
        logs = logs.filter(action=action)
    if entity_type:
        logs = logs.filter(entity_type=entity_type)
    if query:
        logs = logs.filter(
            Q(actor_label__icontains=query)
            | Q(object_repr__icontains=query)
            | Q(object_id__icontains=query)
            | Q(entity_label__icontains=query)
        )

    entity_types = list(
        FinanceAuditLog.objects.order_by("entity_label")
        .values_list("entity_type", "entity_label")
        .distinct()
    )
    export_type = request.GET.get("export")
    if export_type in {"xlsx", "pdf"}:
        export = export_response(
            request,
            export_type,
            "finance-audit-log",
            audit_log_document(
                logs,
                query=query,
                action_label=dict(FinanceAuditLog.Action.choices).get(action, ""),
                entity_label=dict(entity_types).get(entity_type, ""),
            ),
        )
        if export:
            return export

    page_obj = Paginator(logs, 10).get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj,
        "actions": FinanceAuditLog.Action.choices,
        "entity_types": entity_types,
        "selected_action": action,
        "selected_entity_type": entity_type,
        "query": query,
    }
    template_name = (
        "finance/partials/audit_log_results.html"
        if request.headers.get("x-requested-with") == "XMLHttpRequest"
        else "finance/audit_log.html"
    )
    return render(request, template_name, context)


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

    if statement:
        export = export_response(
            request,
            request.GET.get("export"),
            "customer-statement",
            party_statement_document(
                statement,
                "customer",
                form.cleaned_data["start_date"],
                form.cleaned_data["end_date"],
            ),
        )
        if export:
            return export

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

    if statement:
        export = export_response(
            request,
            request.GET.get("export"),
            "supplier-statement",
            party_statement_document(
                statement,
                "supplier",
                form.cleaned_data["start_date"],
                form.cleaned_data["end_date"],
            ),
        )
        if export:
            return export

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

    if report:
        export = export_response(
            request,
            request.GET.get("export"),
            "receivables-aging",
            aging_document(report, "receivable"),
        )
        if export:
            return export

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

    if report:
        export = export_response(
            request,
            request.GET.get("export"),
            "payables-aging",
            aging_document(report, "payable"),
        )
        if export:
            return export

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
