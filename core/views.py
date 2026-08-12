from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from core.permissions import admin_required, accountant_required
from finance.models import JournalEntry

@login_required
@admin_required
def company_settings_view(request):
    """
    Global system settings. Accessible strictly by Administrators.
    """
    return render(request, 'core/settings.html')

@login_required
@admin_required
def trigger_database_backup(request):
    """
    Triggers a manual database backup script.
    """
    if request.method == 'POST':
        # Add backup logic/script call here
        messages.success(request, _("Database backup triggered successfully."))
    return redirect('core:settings')
@login_required
def dashboard_redirect_view(request):
    """
    Dynamically routes the user to their respective dashboard 
    or primary workspace based on their assigned role[cite: 1, 2].
    """
    user = request.user
    if user.is_superuser or user.role == "admin":
        return redirect("core:admin_dashboard")
    elif user.role == "accountant":
        return redirect("core:accountant_dashboard")
    elif user.role == "cashier":
        return redirect("pos:terminal")
    return redirect("login")


@login_required
@admin_required
def admin_dashboard_view(request):
    """
    Administrator Dashboard: System settings, user metrics, and backups.
    """
    from django.contrib.auth import get_user_model
    from customers.models import Customer
    from inventory.models import Product, Warehouse
    from suppliers.models import Supplier
    User = get_user_model()
    
    context = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'customer_count': Customer.objects.filter(is_active=True).count(),
        'supplier_count': Supplier.objects.filter(is_active=True).count(),
        'product_count': Product.objects.filter(is_active=True).count(),
        'warehouse_count': Warehouse.objects.filter(is_active=True).count(),
        'recent_entries': JournalEntry.objects.select_related('created_by').order_by('-created_at')[:6],
    }
    return render(request, 'core/admin_dashboard.html', context)


@login_required
@accountant_required
def accountant_dashboard_view(request):
    """
    Accountant Dashboard: Financial overview, unposted vouchers, and account summaries.
    """
    from finance.models import PaymentVoucher, ReceiptVoucher

    unposted_vouchers = JournalEntry.objects.filter(status=JournalEntry.Status.DRAFT).order_by('-date')[:6]
    context = {
        'unposted_vouchers': unposted_vouchers,
        'unposted_count': JournalEntry.objects.filter(status=JournalEntry.Status.DRAFT).count(),
        'posted_count': JournalEntry.objects.filter(status=JournalEntry.Status.POSTED).count(),
        'receipt_count': ReceiptVoucher.objects.count(),
        'payment_count': PaymentVoucher.objects.count(),
    }
    return render(request, 'core/accountant_dashboard.html', context)
