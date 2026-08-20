import json
from .forms import DiscountCodeForm
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render, redirect
from django.template import context
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
from django.utils.translation import gettext as _
from django.utils import timezone
from django.contrib import messages
from django.core.paginator import Paginator
from core.permissions import cashier_required
from customers.models import Customer
from inventory.models import Product, Warehouse, StockBalance
from .models import POSSession, POSCashTransaction, POSSale, POSPayment, DiscountCode
from .services import close_pos_session, complete_sale, hold_sale

from django.shortcuts import render, redirect
from django.http import JsonResponse

@login_required
def discount_code_list(request):
    """List all discount codes and handle the creation of new discount codes."""
    if not (request.user.is_superuser or getattr(request.user, 'role', '') == 'admin'):
        messages.error(request, _("You do not have permission to access discount code management."))
        return redirect("pos:terminal")

    if request.method == "POST":
        form = DiscountCodeForm(request.POST)
        if form.is_valid():
            discount_code = form.save()
            messages.success(request, _("Discount code '%(code)s' created successfully.") % {'code': discount_code.code})
            return redirect("pos:discount_code_list")
        else:
            messages.error(request, _("Please correct the errors below."))
    else:
        form = DiscountCodeForm()

    discount_codes = DiscountCode.objects.all().order_by('-id')
    return render(request, "pos/discount_code_list.html", {
        "discount_codes": discount_codes,
        "form": form,
    })

@login_required
@require_POST
def toggle_discount_code_status(request, pk):
    """Toggle the active status of a discount code."""
    if not (request.user.is_superuser or getattr(request.user, 'role', '') == 'admin'):
        return JsonResponse({"ok": False, "error": str(_("Unauthorized"))}, status=403)

    discount = get_object_or_404(DiscountCode, pk=pk)
    discount.is_active = not discount.is_active
    discount.save(update_fields=['is_active'])
    return JsonResponse({"ok": True, "is_active": discount.is_active})

@login_required
@cashier_required
@require_GET
def validate_discount(request):
    """API endpoint to validate a coupon/discount code and return calculated discount."""
    code_str = request.GET.get("code", "").strip().upper()
    subtotal_str = request.GET.get("subtotal", "0.000")

    if not code_str:
        return JsonResponse({"ok": False, "error": str(_("Please enter a discount code."))}, status=400)

    try:
        subtotal = Decimal(subtotal_str)
        discount_obj = DiscountCode.objects.get(code__iexact=code_str)
        
        is_valid, err_msg = discount_obj.is_valid(subtotal)
        if not is_valid:
            return JsonResponse({"ok": False, "error": str(err_msg)}, status=400)

        discount_val = discount_obj.calculate_discount(subtotal)
        return JsonResponse({
            "ok": True,
            "code": discount_obj.code,
            "discount_amount": str(discount_val),
            "discount_type": discount_obj.discount_type,
            "value": str(discount_obj.value),
            "min_order_amount": str(discount_obj.min_order_amount),
            "max_discount_amount": str(discount_obj.max_discount_amount) if discount_obj.max_discount_amount else None
        })
    except DiscountCode.DoesNotExist:
        return JsonResponse({"ok": False, "error": str(_("Invalid discount code."))}, status=404)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    
@login_required
@cashier_required
@require_POST
def hold_sale_view(request):
    try:
        payload = json.loads(request.body or "{}")
        warehouse = Warehouse.objects.get(pk=payload["warehouse_id"], is_active=True)
        customer_id = payload.get("customer_id")
        customer = Customer.objects.get(pk=customer_id, is_active=True) if customer_id else None

        active_session = POSSession.objects.filter(
            cashier=request.user,
            status=POSSession.SessionStatus.OPEN
        ).first()

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

        sale = hold_sale(
            warehouse=warehouse,
            cashier=request.user,
            session=active_session,
            items_data=items_data,
            customer=customer,
            notes=payload.get("notes", ""),
        )

        return JsonResponse({"ok": True, "sale_id": sale.id})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


@login_required
@cashier_required
@require_GET
def list_held_sales_view(request):
    # Added prefetch_related to optimize database queries for the items
    sales = POSSale.objects.filter(status=POSSale.SaleStatus.HELD).order_by('-date').prefetch_related('items__product')
    data = []
    for s in sales:
        # Construct a detailed list for the items in this specific held sale
        items_detail = []
        for item in s.items.all():
            items_detail.append({
                "name": item.product.name,
                "quantity": str(item.quantity),
                "price": str(item.unit_price)
            })
            
        data.append({
            "id": s.id,
            "sale_number": s.sale_number,
            "customer_name": s.customer.name if s.customer else "Walk-in",
            "created_at": s.date.strftime("%Y-%m-%d %H:%M"),
            "items_count": s.items.count(),
            "total": str(s.total),
            "items_detail": items_detail  # Added the new items detail payload
        })
    return JsonResponse({"ok": True, "held_sales": data})


@login_required
@cashier_required
@require_POST
def recall_held_sale_view(request, pk):
    sale = get_object_or_404(POSSale, pk=pk, status=POSSale.SaleStatus.HELD)
    
    items = []
    for item in sale.items.all():
        stock = StockBalance.objects.filter(product=item.product, warehouse=sale.warehouse).first()
        available = (stock.quantity - stock.reserved_quantity) if stock else 0
        
        items.append({
            "product_id": item.product.id,
            "name": item.product.name,
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
            "available_stock": str(available)
        })
        
    customer_id = sale.customer_id
    # Discard the hold since it's now back in the active cart
    sale.delete() 
    
    return JsonResponse({
        "ok": True,
        "items": items,
        "customer_id": customer_id
    })


@login_required
@cashier_required
@require_POST
def cancel_held_sale_view(request, pk):
    sale = get_object_or_404(POSSale, pk=pk, status=POSSale.SaleStatus.HELD)
    sale.delete()
    return JsonResponse({"ok": True})
@login_required
@cashier_required
def pos_terminal(request):
    """
    Renders the modern POS terminal screen. Enforces that a cashier
    must have an open register session before accessing the terminal.
    """
    active_session = POSSession.objects.filter(
        cashier=request.user, 
        status=POSSession.SessionStatus.OPEN
    ).first()

    if not active_session:
        return redirect("pos:session_list")

    # NEW LOGIC: Fetch products and map real-time stock for the active session's warehouse
    from inventory.models import Product, StockBalance
    
    # Only sellable items reach the till, so a cashier cannot add something
    # that checkout would refuse.
    products = list(
        Product.objects
        .select_related("tax_rate")
        .filter(is_active=True, is_sellable=True)
        .order_by("code", "pk")
    )
    stock_balances = StockBalance.objects.filter(warehouse=active_session.warehouse)
    reserved = {sb.product_id: sb.reserved_quantity for sb in stock_balances}

    # Availability must come from the batches, because that is what the sale
    # actually draws from. Reading StockBalance here made items look in stock
    # and then fail at checkout with "insufficient unexpired stock".
    from inventory.models import StockBatch

    today = timezone.now().date()
    sellable = (
        StockBatch.objects
        .filter(
            warehouse=active_session.warehouse,
            status=StockBatch.BatchStatus.ACTIVE,
            quantity_remaining__gt=0,
        )
        .filter(Q(expiration_date__isnull=True) | Q(expiration_date__gte=today))
        .values("product_id")
        .annotate(total=Sum("quantity_remaining"))
    )

    stock_dict = {
        row["product_id"]: row["total"] - reserved.get(row["product_id"], 0)
        for row in sellable
    }

    for p in products:
        p.available_stock = stock_dict.get(p.id, 0)

    # Setup 8-item pagination
    paginator = Paginator(products, 8)
    page_number = request.GET.get('page', 1)
    products_page = paginator.get_page(page_number)

    return render(request, "pos/pos_screen.html", {
        "active_session": active_session,
        "products": products_page,
        "customers": Customer.objects.filter(is_active=True).order_by("name"),
        # "warehouses" query is removed as it's no longer needed
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

    # 1. Prioritize Exact Barcode Match
    products = Product.objects.filter(
        barcode=query, is_active=True, is_sellable=True
    ).select_related("tax_rate")

    # 2. Fallback to Name or Product Code
    #
    # Product.name is a Python property, not a column, so filtering on "name"
    # raises FieldError. The searchable columns are name_en and name_ar.
    if not products.exists():
        products = Product.objects.filter(
            Q(name_en__icontains=query)
            | Q(name_ar__icontains=query)
            | Q(code__icontains=query),
            is_active=True,
            is_sellable=True,
        ).select_related("tax_rate")[:20]

    results = []
    for product in products:
        stock = StockBalance.objects.filter(product=product, warehouse_id=warehouse_id).first()
        available_qty = (stock.quantity - stock.reserved_quantity) if stock else 0

        results.append({
            "id": product.id,
            "name": product.name,
            "code": product.code,
            "barcode": product.barcode,
            # str() on a Decimal is deliberate: it never localises, so the
            # browser always receives "0.350" and never "0,350".
            "selling_price": str(product.selling_price),
            "tax_rate": str(
                product.tax_rate.rate
                if product.tax_rate and product.tax_rate.subject_to_tax
                else "0.000"
            ),
            "available_stock": str(available_qty),
            "track_expiration": product.track_expiration
        })

    if not results:
        return JsonResponse({"ok": False, "error": str(_("Product not found or out of stock."))}, status=404)

    return JsonResponse({"ok": True, "results": results})


@login_required
@cashier_required
def sale_list(request):
    sales_query = POSSale.objects.all().order_by('-date')
    
    # Show 20 sales per page
    paginator = Paginator(sales_query, 20) 
    page_number = request.GET.get('page')
    sales_page = paginator.get_page(page_number)
    
    return render(request, "pos/sale_list.html", {"sales": sales_page})


@login_required
@cashier_required
def sale_detail(request, pk):
    """Renders detailed receipt view for a single transaction."""
    sale = get_object_or_404(
        POSSale.objects.select_related("customer", "warehouse", "cashier", "session").prefetch_related("items__product", "payments"),
        pk=pk,
    )
    return render(request, "pos/sale_detail.html", {"sale": sale})


@login_required
@cashier_required
@require_POST
def complete_sale_view(request):
    try:
        active_session = POSSession.objects.filter(
            cashier=request.user,
            status=POSSession.SessionStatus.OPEN
        ).first()

        if not active_session:
            return JsonResponse({"ok": False, "error": str(_("No active register session found. Please open a session first."))})

        payload = json.loads(request.body or "{}")
        warehouse_id = payload.get("warehouse_id")
        if not warehouse_id:
            return JsonResponse({"ok": False, "error": str(_("Warehouse selection is required."))})

        warehouse = Warehouse.objects.get(pk=warehouse_id, is_active=True)
        customer_id = payload.get("customer_id")
        customer = Customer.objects.get(pk=customer_id, is_active=True) if customer_id else None

        discount_code_str = payload.get("discount_code", "").strip()
        discount_code_obj = None
        if discount_code_str:
            discount_code_obj = DiscountCode.objects.filter(code__iexact=discount_code_str).first()

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

        payments_data = []
        for p in payload.get("payments", []):
            payments_data.append({
                "payment_method": p["payment_method"],
                "amount": str(p["amount"]),
                "reference_number": p.get("reference_number", "")
            })

        sale = complete_sale(
            warehouse=warehouse,
            cashier=request.user,
            session=active_session,
            items_data=items_data,
            payments_data=payments_data,
            customer=customer,
            notes=payload.get("notes", ""),
            discount_code_obj=discount_code_obj
        )
        return JsonResponse({
            "ok": True,
            "sale_id": sale.id,
            "sale_number": sale.sale_number,
            "detail_url": reverse("pos:sale_detail", args=[sale.id]),
        })
    except ValidationError as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message})
    except (KeyError, ValueError, json.JSONDecodeError, Product.DoesNotExist, Warehouse.DoesNotExist, Customer.DoesNotExist) as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)})


@login_required
@cashier_required
def session_list(request):
    sessions_query = POSSession.objects.all().order_by('-opened_at')
    
    # Show 15 sessions per page
    paginator = Paginator(sessions_query, 15)
    page_number = request.GET.get('page')
    sessions_page = paginator.get_page(page_number)

    # Fetch active session if any
    active_session = POSSession.objects.filter(cashier=request.user, status='OPEN').first()
    
    # FETCH ALL WAREHOUSES FOR THE DROPDOWN
    warehouses = Warehouse.objects.all() # Or filter by active/company if needed
    
    context = {
        'sessions': sessions_page,
        'active_session': active_session,
        'warehouses': warehouses,  # <-- Pass warehouses into the context here!
    }
    
    return render(request, "pos/session_list.html", context)

@login_required
@cashier_required
def open_session(request):
    """Open a new register session with a starting cash float."""
    if request.method == 'POST':
        warehouse_id = request.POST.get('warehouse_id')
        opening_balance = Decimal(request.POST.get('opening_balance', '0.000') or '0.000')
        notes = request.POST.get('notes', '')

        if not warehouse_id:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': str(_('Please select a warehouse.'))}, status=400)
            return redirect('pos:session_list')

        # Check if cashier already has an open session
        existing_open = POSSession.objects.filter(cashier=request.user, status=POSSession.SessionStatus.OPEN).exists()
        if existing_open:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': str(_('You already have an active register session.'))}, status=400)
            return redirect('pos:terminal')

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
            return JsonResponse({'ok': True, 'session_id': session.pk, 'redirect_url': reverse('pos:terminal')})
        return redirect('pos:terminal')

    return redirect('pos:session_list')


@login_required
@cashier_required
def close_session(request, pk):
    """Close, reconcile, and post one Finance journal for the register."""
    session = get_object_or_404(POSSession, pk=pk, cashier=request.user, status=POSSession.SessionStatus.OPEN)
    
    if request.method == 'POST':
        try:
            actual_cash = Decimal(request.POST.get('closing_balance_actual', '0.000') or '0.000')
            close_pos_session(session.pk, request.user, actual_cash)
        except (ValidationError, InvalidOperation, ValueError) as exc:
            error = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': error}, status=400)
            messages.error(request, error)
            return redirect('pos:session_list')

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'redirect_url': reverse('pos:session_list')})
        messages.success(request, _("Register closed successfully."))
        return redirect('pos:session_list')

    return redirect('pos:session_list')


@login_required
@cashier_required
@require_POST
def cash_transaction(request, pk):
    """Log a manual cash drop or cash-in float during an active session."""
    session = get_object_or_404(POSSession, pk=pk, status=POSSession.SessionStatus.OPEN)
    
    try:
        amount = Decimal(request.POST.get('amount', '0.000'))
        trans_type = request.POST.get('transaction_type')
        reason = request.POST.get('reason', '').strip()

        if amount <= 0 or trans_type not in POSCashTransaction.TransactionType.values:
            return JsonResponse({'ok': False, 'error': str(_('Invalid transaction amount or type.'))}, status=400)

        POSCashTransaction.objects.create(
            session=session,
            user=request.user,
            transaction_type=trans_type,
            amount=amount,
            reason=reason
        )
        return JsonResponse({'ok': True})
    except (ValueError, TypeError, ValidationError) as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
