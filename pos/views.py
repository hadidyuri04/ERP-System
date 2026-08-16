import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
from django.utils.translation import gettext as _

from core.permissions import cashier_required
from customers.models import Customer
from inventory.models import Product, Warehouse, StockBalance

from .models import POSSale
from .services import complete_sale


@login_required
@cashier_required
def pos_terminal(request):
    # MODERNIZATION: We no longer load all products. The frontend will fetch them via API.
    return render(request, "pos/pos_screen.html", {
        "customers": Customer.objects.filter(is_active=True).order_by("name"),
        "warehouses": Warehouse.objects.filter(is_active=True).order_by("name"),
    })


@login_required
@cashier_required
@require_GET
def search_product(request):
    """
    Fast API endpoint for barcode scanners and manual searches.
    """
    query = request.GET.get("q", "").strip()
    warehouse_id = request.GET.get("warehouse_id")

    if not query or not warehouse_id:
        return JsonResponse({"ok": False, "error": str(_("Search query and warehouse ID are required."))}, status=400)

    # 1. Prioritize Exact Barcode Match (Fastest for Supermarkets)
    products = Product.objects.filter(barcode=query, is_active=True)
    
    # 2. Fallback to Name or Product Code if barcode not found
    if not products.exists():
        products = Product.objects.filter(
            Q(name__icontains=query) | Q(code__icontains=query),
            is_active=True
        )[:20] # Limit to 20 to prevent payload bloat

    results = []
    for product in products:
        # Fetch available stock specifically for the selected warehouse
        stock = StockBalance.objects.filter(product=product, warehouse_id=warehouse_id).first()
        available_qty = (stock.quantity - stock.reserved_quantity) if stock else 0

        results.append({
            "id": product.id,
            "name": product.name,
            "code": product.code,
            "barcode": product.barcode,
            "selling_price": str(product.selling_price),
            "tax_rate": "0.00", # Placeholder: Expand this when tax logic is added
            "available_stock": str(available_qty),
            "track_expiration": product.track_expiration
        })

    if not results:
        return JsonResponse({"ok": False, "error": str(_("Product not found or out of stock."))}, status=404)

    return JsonResponse({"ok": True, "results": results})


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
    # (Keep your existing complete_sale_view implementation here)
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