import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.permissions import cashier_required
from customers.models import Customer
from inventory.models import Product, Warehouse

from .models import POSSale
from .services import complete_sale


@login_required
@cashier_required
def pos_terminal(request):
    products = Product.objects.filter(is_active=True).select_related("category", "unit").annotate(
        available_stock=Sum("balances__quantity")
    ).order_by("name")
    return render(request, "pos/pos_screen.html", {
        "products": products,
        "customers": Customer.objects.filter(is_active=True).order_by("name"),
        "warehouses": Warehouse.objects.filter(is_active=True).order_by("name"),
    })


@login_required
@cashier_required
def sale_list(request):
    sales = POSSale.objects.select_related("customer", "warehouse", "cashier").order_by("-date")
    return render(request, "pos/sale_list.html", {"sales": sales})


@login_required
@cashier_required
def sale_detail(request, pk):
    sale = get_object_or_404(
        POSSale.objects.select_related("customer", "warehouse", "cashier").prefetch_related("items__product", "payments"),
        pk=pk,
    )
    return render(request, "pos/sale_detail.html", {"sale": sale})


@login_required
@cashier_required
@require_POST
def complete_sale_view(request):
    try:
        payload = json.loads(request.body or "{}")
        warehouse = Warehouse.objects.get(pk=payload["warehouse_id"], is_active=True)
        customer_id = payload.get("customer_id")
        customer = Customer.objects.get(pk=customer_id, is_active=True) if customer_id else None
        items_data = []
        for item in payload.get("items", []):
            product = Product.objects.get(pk=item["product_id"], is_active=True)
            items_data.append({
                "product": product,
                "quantity": item["quantity"],
                "unit_price": item.get("unit_price", product.selling_price),
                "discount_amount": item.get("discount_amount", "0.000"),
                "tax_amount": item.get("tax_amount", "0.000"),
            })
        sale = complete_sale(
            warehouse=warehouse,
            cashier=request.user,
            items_data=items_data,
            payments_data=payload.get("payments", []),
            customer=customer,
            notes=payload.get("notes", ""),
        )
        return JsonResponse({
            "ok": True,
            "sale_id": sale.id,
            "sale_number": sale.sale_number,
            "detail_url": reverse("pos:sale_detail", args=[sale.id]),
        })
    except (KeyError, ValueError, json.JSONDecodeError, ValidationError, Product.DoesNotExist, Warehouse.DoesNotExist, Customer.DoesNotExist) as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)
