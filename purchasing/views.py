import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import accountant_required

from inventory.models import Product

from .forms import PurchaseInvoiceForm, PurchaseInvoiceItemFormSet
from .models import PurchaseInvoice
from .services import confirm_purchase


@login_required
@accountant_required
def purchase_list_view(request):
    invoices = PurchaseInvoice.objects.select_related("supplier", "warehouse").order_by("-invoice_date", "-id")
    return render(request, "purchasing/purchase_invoice_list.html", {"invoices": invoices})


def _save_invoice_with_totals(form, formset, invoice, user):
    """
    Total up the lines and save. Shared by create and edit so the two can never
    drift apart. Returns True on success, or adds a form error and returns False.

    Every derived figure is assigned, never accumulated, so re-saving the same
    invoice produces the same numbers instead of compounding them.
    """
    subtotal = Decimal("0.000")
    line_discount = Decimal("0.000")
    line_tax = Decimal("0.000")

    for item_form in formset.forms:
        if not item_form.cleaned_data or item_form.cleaned_data.get("DELETE"):
            continue

        quantity = item_form.cleaned_data["quantity"]
        unit_cost = item_form.cleaned_data["unit_cost"]
        discount = item_form.cleaned_data.get("discount_amount") or Decimal("0.000")
        product = item_form.cleaned_data["product"]

        gross = quantity * unit_cost
        # Tax comes from the product's rate, never from typing. Discount comes
        # off first, so tax is charged on what is actually paid.
        tax = product.tax_for(gross - discount)
        item_form.instance.tax_amount = tax

        subtotal += gross
        line_discount += discount
        line_tax += tax

    if subtotal <= 0:
        form.add_error(None, _("Purchase invoice must contain at least one positive item."))
        return False

    invoice.subtotal = subtotal
    invoice.discount_amount = line_discount
    invoice.tax_amount = line_tax
    invoice.total = (
        invoice.subtotal + invoice.tax_amount + invoice.additional_expenses
        - invoice.discount_amount
    )
    invoice.save()

    items = formset.save(commit=False)
    for deleted in formset.deleted_objects:
        deleted.delete()
    for item in items:
        item.purchase_invoice = invoice
        item.line_total = (
            item.quantity * item.unit_cost - item.discount_amount + item.tax_amount
        )
        item.save()

    return True


def _tax_rates_json():
    """product id -> tax percentage, so the form can preview tax as you type."""
    return json.dumps({
        str(p.pk): float(p.tax_rate.rate) if p.tax_rate and p.tax_rate.subject_to_tax else 0.0
        for p in Product.objects.select_related("tax_rate").filter(is_active=True)
    })


@login_required
@accountant_required
@transaction.atomic
def purchase_create_view(request):
    invoice = PurchaseInvoice(created_by=request.user)
    form = PurchaseInvoiceForm(request.POST or None, instance=invoice)
    formset = PurchaseInvoiceItemFormSet(request.POST or None, instance=invoice, prefix="items")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        invoice = form.save(commit=False)
        invoice.created_by = request.user
        invoice.invoice_number = f"PI-{timezone.now().strftime('%Y%m%d%H%M%S%f')}"

        if _save_invoice_with_totals(form, formset, invoice, request.user):
            messages.success(request, _("Purchase invoice saved as draft."))
            return redirect("purchasing:detail", pk=invoice.pk)

    return render(request, "purchasing/purchase_invoice_form.html", {
        "form": form,
        "formset": formset,
        "tax_rates_json": _tax_rates_json(),
    })


@login_required
@accountant_required
@transaction.atomic
def purchase_update_view(request, pk):
    """Edit a draft invoice. Spec 10: confirmed documents are locked."""
    invoice = get_object_or_404(PurchaseInvoice, pk=pk)

    if invoice.status != PurchaseInvoice.Status.DRAFT:
        messages.error(request, _("Only draft invoices can be edited."))
        return redirect("purchasing:detail", pk=invoice.pk)

    form = PurchaseInvoiceForm(request.POST or None, instance=invoice)
    formset = PurchaseInvoiceItemFormSet(request.POST or None, instance=invoice, prefix="items")

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        invoice = form.save(commit=False)

        if _save_invoice_with_totals(form, formset, invoice, request.user):
            messages.success(request, _("Purchase invoice updated."))
            return redirect("purchasing:detail", pk=invoice.pk)

    return render(request, "purchasing/purchase_invoice_form.html", {
        "form": form,
        "formset": formset,
        "invoice": invoice,
        "tax_rates_json": _tax_rates_json(),
    })


@login_required
@accountant_required
def purchase_detail_view(request, pk):
    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related("supplier", "warehouse", "created_by").prefetch_related("items__product"),
        pk=pk,
    )
    return render(request, "purchasing/purchase_invoice_detail.html", {"invoice": invoice})


@login_required
@accountant_required
@require_POST
def purchase_confirm_view(request, pk):
    try:
        confirm_purchase(pk, request.user)
        messages.success(request, _("Purchase invoice confirmed and posted."))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("purchasing:detail", pk=pk)
