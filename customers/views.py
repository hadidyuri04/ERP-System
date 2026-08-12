from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from core.permissions import accountant_required

from .forms import CustomerForm
from .models import Customer


@login_required
@accountant_required
def customer_list_view(request):
    customers = Customer.objects.order_by("code")
    query = request.GET.get("q", "").strip()
    if query:
        customers = customers.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(phone__icontains=query)
        )
    return render(request, "customers/customer_list.html", {"customers": customers, "query": query})


@login_required
@accountant_required
def customer_create_view(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Customer created successfully."))
        return redirect("customers:list")
    return render(request, "customers/customer_form.html", {"form": form, "title": _("New customer")})


@login_required
@accountant_required
def customer_update_view(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Customer updated successfully."))
        return redirect("customers:list")
    return render(request, "customers/customer_form.html", {"form": form, "title": _("Edit customer")})
