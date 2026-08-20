from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import cashier_required
from finance.models import Account
from inventory.models import Warehouse
from sales.models import SalesInvoice
from sales.services import create_invoice_from_quotation

from .forms import QuotationForm, QuotationItemFormSet
from .models import Quotation
from .services import create_quotation


@login_required
@cashier_required
def quotation_list(request):
    quotations = Quotation.objects.select_related("customer", "created_by").order_by("-created_at")
    return render(request, "quotations/quotation_list.html", {"quotations": quotations})


@login_required
@cashier_required
def quotation_detail(request, pk):
    quotation = get_object_or_404(Quotation.objects.select_related("customer").prefetch_related("items__product"), pk=pk)
    return render(request, "quotations/quotation_detail.html", {
        "quotation": quotation,
        "warehouses": Warehouse.objects.filter(is_active=True).order_by("name"),
        "payment_types": SalesInvoice.PaymentType.choices,
        "payment_accounts": Account.objects.filter(
            is_active=True, allow_posting=True, is_cash_equivalent=True,
        ).order_by("code"),
    })


@login_required
@cashier_required
def quotation_create(request):
    form = QuotationForm(request.POST or None)
    formset = QuotationItemFormSet(request.POST or None, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        items_data = []
        for item_form in formset.forms:
            data = item_form.cleaned_data
            if not data or data.get("DELETE"):
                continue
            product = data["product"]
            discount = data.get("discount_amount") or 0
            gross = data["quantity"] * data["unit_price"]

            items_data.append({
                "product": product,
                "quantity": data["quantity"],
                "unit_price": data["unit_price"],
                "discount_amount": discount,
                # Derived from the product's tax rate, not typed in.
                "tax_amount": product.tax_for(gross - discount),
            })
        try:
            quotation = create_quotation(
                customer=form.cleaned_data["customer"],
                date=form.cleaned_data["date"],
                expiry_date=form.cleaned_data["expiry_date"],
                items_data=items_data,
                user=request.user,
                discount_amount=form.cleaned_data.get("discount_amount") or 0,
                # Header tax removed: totals come from the line rates only.
                tax_amount=sum(i["tax_amount"] for i in items_data),
                notes=form.cleaned_data.get("notes") or "",
            )
            messages.success(request, _("Quotation created successfully."))
            return redirect("quotations:detail", pk=quotation.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "quotations/quotation_form.html", {"form": form, "formset": formset})


@login_required
@cashier_required
@require_POST
def convert_to_sale_view(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    try:
        warehouse = Warehouse.objects.get(pk=request.POST.get("warehouse"), is_active=True)
        invoice_date = timezone.datetime.strptime(
            request.POST.get("invoice_date", ""), "%Y-%m-%d"
        ).date()
        due_date = timezone.datetime.strptime(
            request.POST.get("due_date", ""), "%Y-%m-%d"
        ).date()
        payment_type = request.POST.get("payment_type", SalesInvoice.PaymentType.CREDIT)
        if payment_type not in SalesInvoice.PaymentType.values:
            raise ValidationError(_("Invalid payment type."))
        account_id = request.POST.get("payment_account")
        payment_account = None
        if account_id:
            payment_account = Account.objects.get(
                pk=account_id, is_active=True, allow_posting=True,
                is_cash_equivalent=True,
            )
        invoice = create_invoice_from_quotation(
            quotation_id=quotation.id,
            warehouse=warehouse,
            invoice_date=invoice_date,
            due_date=due_date,
            payment_type=payment_type,
            payment_account=payment_account,
            user=request.user,
        )
        messages.success(request, _("Quotation converted to a draft sales invoice."))
        return redirect("sales:invoice_detail", pk=invoice.pk)
    except (ValidationError, ValueError, Warehouse.DoesNotExist, Account.DoesNotExist) as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, message)
        return redirect("quotations:detail", pk=pk)
