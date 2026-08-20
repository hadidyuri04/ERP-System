from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from core.permissions import cashier_required
from inventory.models import Warehouse
from pos.models import POSPayment, POSSession

from .forms import QuotationForm, QuotationItemFormSet
from .models import Quotation
from .services import create_quotation, convert_quotation_to_pos_sale


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
        "payment_methods": POSPayment.PaymentMethod.choices,
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
                # Whole-quotation discount, on top of any per-line discount.
                discount_amount=form.cleaned_data.get("discount_amount") or 0,
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

        # A conversion is a real POS sale, so it needs the cashier's open till.
        session = POSSession.objects.filter(
            cashier=request.user,
            status=POSSession.SessionStatus.OPEN,
        ).first()
        if session is None:
            raise ValidationError(
                _("Open a register session before converting a quotation.")
            )

        sale = convert_quotation_to_pos_sale(
            quotation_id=quotation.id,
            warehouse=warehouse,
            cashier=request.user,
            session=session,
            payments_data=[{
                "payment_method": request.POST.get("payment_method", POSPayment.PaymentMethod.CASH),
                "amount": quotation.total,
                "reference_number": request.POST.get("reference_number", ""),
            }],
        )
        messages.success(request, _("Quotation converted to sale successfully."))
        return redirect("pos:sale_detail", pk=sale.pk)
    except (ValidationError, Warehouse.DoesNotExist) as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, message)
        return redirect("quotations:detail", pk=pk)
