import json
from decimal import Decimal

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

from core.permissions import accountant_required, cashier_required
from finance.exports import export_response, sales_invoice_document
from inventory.models import Product

from .forms import (
    CustomerInvoicePaymentForm,
    SalesCreditNoteForm,
    SalesInvoiceForm,
    SalesInvoiceItemFormSet,
)
from .models import SalesCreditNote, SalesInvoice
from .services import (
    cancel_draft_invoice,
    confirm_sales_invoice,
    create_and_post_full_credit_note,
    generate_invoice_number,
    record_invoice_payment,
)


def _tax_rates_json():
    return json.dumps({
        str(product.pk): float(product.tax_rate.rate)
        if product.tax_rate and product.tax_rate.subject_to_tax else 0.0
        for product in Product.objects.select_related("tax_rate").filter(is_active=True, is_sellable=True)
    })


def _save_invoice(form, formset, invoice):
    subtotal = Decimal("0.000")
    discount = Decimal("0.000")
    tax = Decimal("0.000")
    line_count = 0
    for item_form in formset.forms:
        data = item_form.cleaned_data
        if not data or data.get("DELETE"):
            continue
        quantity = data["quantity"]
        unit_price = data["unit_price"]
        line_discount = data.get("discount_amount") or Decimal("0.000")
        product = data["product"]
        gross = quantity * unit_price
        line_tax = product.tax_for(gross - line_discount)
        item_form.instance.tax_amount = line_tax
        subtotal += gross
        discount += line_discount
        tax += line_tax
        line_count += 1
    if not line_count or subtotal <= 0:
        form.add_error(None, _("Sales invoice must contain at least one positive item."))
        return False
    invoice.subtotal = subtotal
    invoice.discount_amount = discount
    invoice.tax_amount = tax
    invoice.total = subtotal - discount + tax
    invoice.save()
    items = formset.save(commit=False)
    for deleted in formset.deleted_objects:
        deleted.delete()
    for item in items:
        item.invoice = invoice
        item.line_total = item.quantity * item.unit_price - item.discount_amount + item.tax_amount
        item.save()
    return True


@login_required
@cashier_required
def invoice_list(request):
    invoices = SalesInvoice.objects.select_related("customer", "warehouse").order_by("-invoice_date", "-id")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    payment = request.GET.get("payment", "")
    if query:
        invoices = invoices.filter(Q(invoice_number__icontains=query) | Q(customer__name__icontains=query))
    if status in SalesInvoice.Status.values:
        invoices = invoices.filter(status=status)
    invoice_rows = list(invoices)
    if payment:
        invoice_rows = [row for row in invoice_rows if row.payment_status_code == payment]
    page_obj = Paginator(invoice_rows, 20).get_page(request.GET.get("page"))
    return render(request, "sales/invoice_list.html", {
        "page_obj": page_obj,
        "statuses": SalesInvoice.Status.choices,
        "query": query,
        "selected_status": status,
        "selected_payment": payment,
    })


@login_required
@cashier_required
@transaction.atomic
def invoice_create(request):
    invoice = SalesInvoice(created_by=request.user, invoice_date=timezone.localdate(), due_date=timezone.localdate())
    form = SalesInvoiceForm(request.POST or None, instance=invoice)
    formset = SalesInvoiceItemFormSet(request.POST or None, instance=invoice, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        invoice = form.save(commit=False)
        invoice.created_by = request.user
        invoice.invoice_number = generate_invoice_number()
        if _save_invoice(form, formset, invoice):
            messages.success(request, _("Sales invoice saved as draft."))
            return redirect("sales:invoice_detail", pk=invoice.pk)
    return render(request, "sales/invoice_form.html", {
        "form": form, "formset": formset, "invoice": None,
        "tax_rates_json": _tax_rates_json(),
    })


@login_required
@cashier_required
@transaction.atomic
def invoice_update(request, pk):
    invoice = get_object_or_404(SalesInvoice, pk=pk)
    if invoice.status != SalesInvoice.Status.DRAFT:
        messages.error(request, _("Only draft sales invoices can be edited."))
        return redirect("sales:invoice_detail", pk=pk)
    form = SalesInvoiceForm(request.POST or None, instance=invoice)
    formset = SalesInvoiceItemFormSet(request.POST or None, instance=invoice, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        invoice = form.save(commit=False)
        if _save_invoice(form, formset, invoice):
            messages.success(request, _("Sales invoice updated."))
            return redirect("sales:invoice_detail", pk=pk)
    return render(request, "sales/invoice_form.html", {
        "form": form, "formset": formset, "invoice": invoice,
        "tax_rates_json": _tax_rates_json(),
    })


@login_required
@cashier_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(
        SalesInvoice.objects.select_related("customer", "warehouse", "quotation", "created_by")
        .prefetch_related("items__product"), pk=pk,
    )
    export = export_response(
        request, request.GET.get("export"), invoice.invoice_number,
        sales_invoice_document(invoice),
    )
    if export:
        return export
    allocations = []
    if invoice.open_item:
        allocations = invoice.open_item.allocations.select_related(
            "journal_line__journal_entry", "created_by"
        )
    return render(request, "sales/invoice_detail.html", {
        "invoice": invoice, "allocations": allocations,
    })


@login_required
@cashier_required
@require_POST
def invoice_confirm(request, pk):
    try:
        confirm_sales_invoice(pk, request.user)
        messages.success(request, _("Sales invoice confirmed, stock issued and Finance posted."))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("sales:invoice_detail", pk=pk)


@login_required
@cashier_required
def invoice_payment(request, pk):
    invoice = get_object_or_404(SalesInvoice, pk=pk)
    form = CustomerInvoicePaymentForm(request.POST or None, invoice=invoice, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            record_invoice_payment(
                invoice.id, request.user,
                payment_date=form.cleaned_data["date"],
                account=form.cleaned_data["account"],
                amount=form.cleaned_data["amount"],
                payment_method=form.cleaned_data["payment_method"],
                reference=form.cleaned_data["reference"],
            )
            messages.success(request, _("Customer payment recorded and allocated to the invoice."))
            return redirect("sales:invoice_detail", pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "sales/invoice_payment_form.html", {"form": form, "invoice": invoice})


@login_required
@accountant_required
@require_POST
def invoice_cancel(request, pk):
    try:
        cancel_draft_invoice(pk, request.user, request.POST.get("reason", ""))
        messages.success(request, _("Draft sales invoice cancelled."))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("sales:invoice_detail", pk=pk)


@login_required
@accountant_required
def credit_note_create(request, pk):
    invoice = get_object_or_404(SalesInvoice, pk=pk)
    note = SalesCreditNote(invoice=invoice, created_by=request.user, date=timezone.localdate())
    form = SalesCreditNoteForm(request.POST or None, instance=note)
    if request.method == "POST" and form.is_valid():
        try:
            create_and_post_full_credit_note(
                invoice.id, request.user,
                note_date=form.cleaned_data["date"], reason=form.cleaned_data["reason"],
            )
            messages.success(request, _("Full credit note posted and inventory restored."))
            return redirect("sales:invoice_detail", pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "sales/credit_note_form.html", {"form": form, "invoice": invoice})
