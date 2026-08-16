import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
from django.utils.translation import gettext as _
from .models import POSSession, POSCashTransaction
from core.permissions import cashier_required
from customers.models import Customer
from inventory.models import Product, Warehouse, StockBalance
from django.utils import timezone
from .models import POSSale
from .services import complete_sale


@login_required
@cashier_required
def pos_terminal(request):
    """Renders the modern POS terminal screen with active customers and warehouses."""
    return render(request, "pos/pos_screen.html", {
        "customers": Customer.objects.filter(is_active=True).order_by("name"),
        "warehouses": Warehouse.objects.filter(is_active=True).order_by("name"),
    })


@login_required
@cashier_required
@require_GET
def search_product(request):
    """
    Fast API endpoint for barcode scanners and manual searches,
    checking stock levels specifically for the selected warehouse.
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
        )[:20]  # Limit payload size

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
            "tax_rate": "0.00",
            "available_stock": str(available_qty),
            "track_expiration": product.track_expiration
        })

    if not results:
        return JsonResponse({"ok": False, "error": str(_("Product not found or out of stock."))}, status=404)

    return JsonResponse({"ok": True, "results": results})


@login_required
@cashier_required
def sale_list(request):
    """Renders the list of past POS sales with optimized querysets."""
    sales = POSSale.objects.select_related("customer", "warehouse", "cashier").order_by("-date")
    return render(request, "pos/sale_list.html", {"sales": sales})


@login_required
@cashier_required
def sale_detail(request, pk):
    """Renders detailed receipt view for a single transaction."""
    sale = get_object_or_404(
        POSSale.objects.select_related("customer", "warehouse", "cashier").prefetch_related("items__product", "payments"),
        pk=pk,
    )
    return render(request, "pos/sale_detail.html", {"sale": sale})


@login_required
@cashier_required
@require_POST
def complete_sale_view(request):
    """Handles checkout payload, processes inventory/finance services, and returns receipt metadata."""
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
@login_required
def session_list(value):
    """List all POS register sessions."""
    sessions = POSSession.objects.all().order_by('-opened_at')
    context = {'sessions': sessions}
    return render(value, 'pos/session_list.html', context)

@login_required
def open_session(request):
    """Open a new register session with a starting cash float."""
    if request.method == 'POST':
        warehouse_id = request.POST.get('warehouse_id')
        opening_balance = request.POST.get('opening_balance', '0.000')
        notes = request.POST.get('notes', '')

        # Check if cashier already has an open session
        existing_open = POSSession.objects.filter(cashier=request.user, status=POSSession.SessionStatus.OPEN).exists()
        if existing_open:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': 'You already have an open register session.'}, status=400)
            return redirect('pos:session_list')

        session_number = f"SES-{timezone.now().strftime('%Y%m%d%H%M%S')}-{request.user.id}"
        
        session = POSSession.objects.create(
            session_number=session_number,
            cashier=request.user,
            warehouse_id=warehouse_id,
            opening_balance=opening_balance,
            status=POSSession.SessionStatus.OPEN,
            notes=notes
        )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'session_id': session.pk})
        return redirect('pos:terminal')

    return redirect('pos:session_list')

@login_required
def close_session(request, pk):
    """Close and reconcile a register session."""
    session = get_object_or_404(POSSession, pk=pk, cashier=request.user, status=POSSession.SessionStatus.OPEN)
    
    if request.method == 'POST':
        actual_cash = float(request.POST.get('closing_balance_actual', 0))
        
        # Calculate expected cash: opening balance + cash sales + cash in - cash out
        cash_sales_total = sum(
            payment.amount for sale in session.sales.filter(status='COMPLETED') 
            for payment in sale.payments.filter(payment_method='cash')
        )
        cash_in_total = sum(t.amount for t in session.cash_transactions.filter(transaction_type='IN'))
        cash_out_total = sum(t.amount for t in session.cash_transactions.filter(transaction_type='OUT'))
        
        expected_cash = float(session.opening_balance) + float(cash_sales_total) + float(cash_in_total) - float(cash_out_total)
        difference = actual_cash - expected_cash

        session.closing_balance_expected = expected_cash
        session.closing_balance_actual = actual_cash
        session.difference = difference
        session.status = POSSession.SessionStatus.CLOSED
        session.closed_at = timezone.now()
        session.save()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True})
        return redirect('pos:session_list')

    return redirect('pos:session_list')

@login_required
def cash_transaction(request, pk):
    """Log a manual cash drop or cash-in during an active session."""
    session = get_object_or_404(POSSession, pk=pk, status=POSSession.SessionStatus.OPEN)
    if request.method == 'POST':
        POSCashTransaction.objects.create(
            session=session,
            user=request.user,
            transaction_type=request.POST.get('transaction_type'),
            amount=request.POST.get('amount'),
            reason=request.POST.get('reason', '')
        )
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False}, status=400)