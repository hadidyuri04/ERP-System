from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import accountant_required

from .forms import PaymentVoucherForm, ReceiptVoucherForm
from .models import Account, JournalEntry, PaymentVoucher, ReceiptVoucher
from .reports import generate_general_ledger, generate_trial_balance
from .services import post_journal_entry, post_payment_voucher, post_receipt_voucher


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
