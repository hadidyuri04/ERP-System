from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from core.permissions import accountant_required

from .forms import SupplierForm
from .models import Supplier


@login_required
@accountant_required
def supplier_list_view(request):
    suppliers = Supplier.objects.order_by("code")
    query = request.GET.get("q", "").strip()
    if query:
        suppliers = suppliers.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(phone__icontains=query)
        )
    return render(request, "suppliers/supplier_list.html", {"suppliers": suppliers, "query": query})


@login_required
@accountant_required
def supplier_create_view(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Supplier created successfully."))
        return redirect("suppliers:list")
    return render(request, "suppliers/supplier_form.html", {"form": form, "title": _("New supplier")})


@login_required
@accountant_required
def supplier_update_view(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Supplier updated successfully."))
        return redirect("suppliers:list")
    return render(request, "suppliers/supplier_form.html", {"form": form, "title": _("Edit supplier")})
