from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from core.permissions import cashier_required
from .models import POSSale

@login_required
@cashier_required
def pos_terminal(request):
    """
    Main POS terminal interface. Accessible by Cashiers, Accountants, and Admins.
    """
    return render(request, 'pos/terminal.html')

@login_required
@cashier_required
def sale_list(request):
    """
    List of all POS sales.
    """
    sales = POSSale.objects.all().order_by('-date')
    return render(request, 'pos/sale_list.html', {'sales': sales})

@login_required
@cashier_required
def sale_detail(request, pk):
    """
    Detailed view of a specific POS sale.
    """
    sale = get_object_or_404(POSSale.objects.prefetch_related('items'), pk=pk)
    return render(request, 'pos/sale_detail.html', {'sale': sale})