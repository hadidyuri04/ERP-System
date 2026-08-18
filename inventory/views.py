from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.utils.translation import get_language

from core.permissions import accountant_required

from .forms import (
    CategoryForm,
    ProductForm,
    UnitForm,
    WarehouseForm,
    WasteLossForm,
    WasteLossItemFormSet,
)
from .models import Category, Product, StockBalance, Unit, Warehouse, WasteLoss
from .services import confirm_waste_loss


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
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Product created successfully."))
        return redirect("inventory:product_list")
    return render(request, "inventory/product_form.html", {"form": form, "title": _("New product")})


@login_required
@accountant_required
def product_update_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Product updated successfully."))
        return redirect("inventory:product_list")
    return render(request, "inventory/product_form.html", {"form": form, "title": _("Edit product")})


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
