from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.utils.translation import get_language

from core.permissions import accountant_required
from finance.models import JournalEntry

from .forms import (
    CategoryForm,
    ProductForm,
    StockAdjustmentForm,
    StockAdjustmentItemFormSet,
    UnitForm,
    WarehouseForm,
    WarehouseTransferForm,
    WarehouseTransferItemFormSet,
    WasteLossForm,
    WasteLossItemFormSet,
)
from .models import (
    Category,
    Product,
    StockAdjustment,
    StockBalance,
    Unit,
    Warehouse,
    WarehouseTransfer,
    WasteLoss,
)
from .services import (
    confirm_stock_adjustment,
    confirm_warehouse_transfer,
    confirm_waste_loss,
    get_expiry_watchlist,
    mark_expired_batches,
)


@login_required
@accountant_required
def product_list_view(request):
    products = Product.objects.select_related("category", "unit").annotate(
        stock_quantity=Sum("balances__quantity")
    ).order_by("code")
    
    query = request.GET.get("q", "").strip()
    if query:
        # Search across both English and Arabic names
        products = products.filter(
            Q(code__icontains=query) | 
            Q(barcode__icontains=query) | 
            Q(name_en__icontains=query) | 
            Q(name_ar__icontains=query)
        )
        
    # Order categories safely based on the active language
    lang = get_language()
    category_order_field = "name_ar" if lang == "ar" else "name_en"

    return render(request, "inventory/product_list.html", {
        "products": products,
        "categories": Category.objects.filter(is_active=True).order_by(category_order_field),
        "query": query,
    })


@login_required
@accountant_required
def product_create_view(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Product created successfully."))
        return redirect("inventory:product_list")
    return render(request, "inventory/product_form.html", {"form": form, "title": _("New product")})


@login_required
@accountant_required
def product_update_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Product updated successfully."))
        return redirect("inventory:product_list")

    stock_rows = (
        StockBalance.objects
        .select_related("warehouse")
        .filter(product=product)
        .order_by("warehouse__name")
    )

    return render(request, "inventory/product_form.html", {
        "form": form,
        "title": _("Edit product"),
        "product": product,
        "stock_rows": stock_rows,
    })


@login_required
@accountant_required
def warehouse_list_view(request, pk=None):
    """List warehouses. The side panel creates a new one, or edits `pk` when given."""
    instance = get_object_or_404(Warehouse, pk=pk) if pk else None
    form = WarehouseForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        form.save()
        if instance:
            messages.success(request, _("Warehouse updated successfully."))
        else:
            messages.success(request, _("Warehouse created successfully."))
        return redirect("inventory:warehouse_list")

    return render(request, "inventory/warehouse_list.html", {
        "warehouses": Warehouse.objects.order_by("code"),
        "form": form,
        "editing": instance,
    })


@login_required
@accountant_required
def category_list_view(request, pk=None):
    """List categories. The side panel creates a new one, or edits `pk` when given."""
    instance = get_object_or_404(Category, pk=pk) if pk else None
    form = CategoryForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        form.save()
        if instance:
            messages.success(request, _("Category updated successfully."))
        else:
            messages.success(request, _("Category created successfully."))
        return redirect("inventory:category_list")

    order_field = "name_ar" if get_language() == "ar" else "name_en"

    return render(request, "inventory/category_list.html", {
        "categories": Category.objects.select_related("parent").order_by(order_field),
        "form": form,
        "editing": instance,
    })


@login_required
@accountant_required
def unit_list_view(request, pk=None):
    """List units of measure. The side panel creates a new one, or edits `pk` when given."""
    instance = get_object_or_404(Unit, pk=pk) if pk else None
    form = UnitForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        form.save()
        if instance:
            messages.success(request, _("Unit updated successfully."))
        else:
            messages.success(request, _("Unit created successfully."))
        return redirect("inventory:unit_list")

    order_field = "name_ar" if get_language() == "ar" else "name_en"

    return render(request, "inventory/unit_list.html", {
        "units": Unit.objects.order_by(order_field),
        "form": form,
        "editing": instance,
    })


@login_required
@accountant_required
@transaction.atomic
def product_stock_set_view(request, pk):
    """
    Quick stock correction straight from the product list.

    This is a shortcut, not a back door: it builds a real StockAdjustment and
    runs it through confirm_stock_adjustment(), so the movement, the batch and
    the balance are all written exactly as they would be from the full screen.
    """
    product = get_object_or_404(Product, pk=pk)
    warehouses = Warehouse.objects.filter(is_active=True).order_by("name")

    balances = {
        balance.warehouse_id: balance.quantity
        for balance in StockBalance.objects.filter(product=product)
    }

    if request.method == "POST":
        warehouse_id = request.POST.get("warehouse")
        raw_quantity = (request.POST.get("counted_quantity") or "").strip()
        note = (request.POST.get("notes") or "").strip()
        mode = request.POST.get("mode") or "set"

        warehouse = warehouses.filter(pk=warehouse_id).first()

        try:
            amount = Decimal(raw_quantity)
        except (InvalidOperation, TypeError):
            amount = None

        counted = None
        if warehouse is not None and amount is not None:
            current = balances.get(warehouse.id, Decimal("0.000"))
            if mode == "add":
                counted = current + amount
            elif mode == "subtract":
                counted = current - amount
            else:
                counted = amount

        if warehouse is None:
            messages.error(request, _("Select a warehouse."))
        elif amount is None or amount < 0:
            messages.error(request, _("Enter a quantity of zero or more."))
        elif counted < 0:
            messages.error(
                request,
                _("That would leave a negative balance. Current stock is %(current)s.") % {
                    "current": balances.get(warehouse.id, Decimal("0.000")),
                },
            )
        else:
            stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
            adjustment = StockAdjustment.objects.create(
                adjustment_number=f"ADJ-{stamp}",
                warehouse=warehouse,
                date=timezone.now().date(),
                notes=note or _("Quick stock correction from the item list."),
                created_by=request.user,
            )
            adjustment.items.create(
                product=product,
                counted_quantity=counted,
                system_quantity=balances.get(warehouse.id, Decimal("0.000")),
            )

            try:
                confirm_stock_adjustment(adjustment.id, request.user)
                messages.success(
                    request,
                    _("Stock updated. Adjustment %(number)s was created and confirmed.") % {
                        "number": adjustment.adjustment_number,
                    },
                )
                return redirect("inventory:product_list")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))

    warehouse_rows = [
        {"warehouse": w, "quantity": balances.get(w.id, Decimal("0.000"))}
        for w in warehouses
    ]

    return render(request, "inventory/product_stock_form.html", {
        "product": product,
        "warehouses": warehouses,
        "warehouse_rows": warehouse_rows,
    })


@login_required
@accountant_required
def adjustment_list_view(request):
    adjustments = (
        StockAdjustment.objects
        .select_related("warehouse", "created_by")
        .order_by("-date", "-id")
    )
    return render(request, "inventory/adjustment_list.html", {
        "adjustments": adjustments,
    })


@login_required
@accountant_required
@transaction.atomic
def adjustment_create_view(request):
    adjustment = StockAdjustment(created_by=request.user)
    form = StockAdjustmentForm(request.POST or None, instance=adjustment)
    formset = StockAdjustmentItemFormSet(
        request.POST or None, instance=adjustment, prefix="items"
    )

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        adjustment = form.save(commit=False)
        adjustment.created_by = request.user
        adjustment.save()

        items = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()

        for item in items:
            item.adjustment = adjustment
            # Snapshot what the system believes right now. Confirmation
            # re-reads this, so the draft figure is only a reference.
            balance = StockBalance.objects.filter(
                product=item.product, warehouse=adjustment.warehouse
            ).first()
            item.system_quantity = balance.quantity if balance else Decimal("0.000")
            item.variance = item.counted_quantity - item.system_quantity
            item.save()

        messages.success(request, _("Adjustment saved as draft."))
        return redirect("inventory:adjustment_detail", pk=adjustment.pk)

    return render(request, "inventory/adjustment_form.html", {
        "form": form,
        "formset": formset,
    })


@login_required
@accountant_required
def adjustment_detail_view(request, pk):
    adjustment = get_object_or_404(
        StockAdjustment.objects
        .select_related("warehouse", "created_by")
        .prefetch_related("items__product", "items__batch"),
        pk=pk,
    )
    journal = JournalEntry.objects.filter(
        source_type=JournalEntry.SourceType.STOCK_ADJUSTMENT,
        source_id=adjustment.pk,
    ).first()
    return render(request, "inventory/adjustment_detail.html", {
        "adjustment": adjustment,
        "journal": journal,
    })


@login_required
@accountant_required
@require_POST
def adjustment_confirm_view(request, pk):
    try:
        confirm_stock_adjustment(pk, request.user)
        messages.success(
            request,
            _("Adjustment confirmed. Stock and accounting are updated."),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("inventory:adjustment_detail", pk=pk)


@login_required
@accountant_required
def expiry_watchlist_view(request):
    """
    Expired stock and stock nearing expiry (spec 5.6, signed module 17).

    Marking runs on page load so the list is always current even if nobody has
    scheduled the management command yet.
    """
    from core.models import CompanySettings

    newly_expired = mark_expired_batches()
    if newly_expired:
        messages.warning(
            request,
            _("%(count)s batch(es) have just been marked as expired.") % {
                "count": newly_expired,
            },
        )

    warning_days = CompanySettings.load().expiration_warning_days
    today = timezone.now().date()
    batches = get_expiry_watchlist(warning_days=warning_days, today=today)

    return render(request, "inventory/expiry_watchlist.html", {
        "batches": batches,
        "warning_days": warning_days,
        "today": today,
        "expired_count": sum(1 for b in batches if b.expiration_date < today),
    })


@login_required
@accountant_required
def transfer_list_view(request):
    transfers = (
        WarehouseTransfer.objects
        .select_related("source_warehouse", "destination_warehouse", "created_by")
        .order_by("-date", "-id")
    )
    return render(request, "inventory/transfer_list.html", {"transfers": transfers})


@login_required
@accountant_required
@transaction.atomic
def transfer_create_view(request):
    transfer = WarehouseTransfer(created_by=request.user)
    form = WarehouseTransferForm(request.POST or None, instance=transfer)
    formset = WarehouseTransferItemFormSet(
        request.POST or None, instance=transfer, prefix="items"
    )

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        transfer = form.save(commit=False)
        transfer.created_by = request.user
        transfer.save()

        items = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for item in items:
            item.transfer = transfer
            item.save()

        messages.success(request, _("Transfer saved as draft."))
        return redirect("inventory:transfer_detail", pk=transfer.pk)

    return render(request, "inventory/transfer_form.html", {
        "form": form,
        "formset": formset,
    })


@login_required
@accountant_required
def transfer_detail_view(request, pk):
    transfer = get_object_or_404(
        WarehouseTransfer.objects
        .select_related("source_warehouse", "destination_warehouse", "created_by")
        .prefetch_related("items__product", "items__batch"),
        pk=pk,
    )
    return render(request, "inventory/transfer_detail.html", {"transfer": transfer})


@login_required
@accountant_required
@require_POST
def transfer_confirm_view(request, pk):
    try:
        confirm_warehouse_transfer(pk, request.user)
        messages.success(
            request, _("Transfer confirmed. Stock has moved between warehouses.")
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("inventory:transfer_detail", pk=pk)


@login_required
@accountant_required
def waste_list_view(request):
    documents = WasteLoss.objects.select_related("warehouse", "created_by").order_by("-date", "-id")
    return render(request, "inventory/waste_loss_list.html", {"documents": documents})


@login_required
@accountant_required
@transaction.atomic
def waste_create_view(request):
    waste = WasteLoss(created_by=request.user)
    form = WasteLossForm(request.POST or None, instance=waste)
    formset = WasteLossItemFormSet(request.POST or None, instance=waste, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        waste = form.save(commit=False)
        waste.created_by = request.user
        waste.save()
        items = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for item in items:
            item.waste_loss = waste
            item.total_cost = item.quantity * item.unit_cost
            item.save()
        messages.success(request, _("Waste document saved as draft."))
        return redirect("inventory:waste_detail", pk=waste.pk)
    return render(request, "inventory/waste_loss_form.html", {"form": form, "formset": formset})


@login_required
@accountant_required
def waste_detail_view(request, pk):
    waste = get_object_or_404(WasteLoss.objects.select_related("warehouse", "created_by").prefetch_related("items__product"), pk=pk)
    return render(request, "inventory/waste_loss_detail.html", {"waste": waste})


@login_required
@accountant_required
@require_POST
def waste_confirm_view(request, pk):
    try:
        confirm_waste_loss(pk, request.user)
        messages.success(request, _("Waste document confirmed and posted."))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("inventory:waste_detail", pk=pk)
