from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from core.permissions import admin_required, accountant_required
from django.utils import timezone
from datetime import timedelta
from core.models import CompanySettings
from inventory.models import StockBatch

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
    or primary workspace based on their assigned role.
    """
    user = request.user
    if user.is_superuser or user.role == 'ADMIN':
        return redirect('core:home_dashboard') # Pointing to the full dashboard template
    elif user.role == 'ACCOUNTANT':
        return redirect('core:accountant_dashboard')
    elif user.role == 'CASHIER':
        return redirect('pos:terminal')
    else:
        return redirect('core:home_dashboard')


@login_required
@admin_required
def admin_dashboard_view(request):
    """
    Administrator Dashboard: System settings, user metrics, and backups.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    context = {
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
    }
    return render(request, 'core/admin_dashboard.html', context)


@login_required
@accountant_required
def accountant_dashboard_view(request):
    """
    Accountant Dashboard: Financial overview, unposted vouchers, and account summaries.
    """
    from finance.models import JournalEntry
    
    unposted_vouchers = JournalEntry.objects.filter(status='DRAFT').order_by('-date')[:5]
    context = {
        'unposted_vouchers': unposted_vouchers,
        'unposted_count': JournalEntry.objects.filter(status='DRAFT').count(),
    }
    return render(request, 'core/accountant_dashboard.html', context)
@login_required
def main_dashboard_view(request):
    """
    Main business dashboard view rendering core metrics, expiring batches, and recent activity.
    """
    company = CompanySettings.load()
    warning_days = company.expiration_warning_days
    warning_threshold = timezone.now().date() + timedelta(days=warning_days)

    # Query active batches expiring within the company's warning window
    expiring_batches = StockBatch.objects.filter(
        status=StockBatch.BatchStatus.ACTIVE,
        expiration_date__isnull=False,
        expiration_date__lte=warning_threshold
    ).order_by('expiration_date')[:5]

    context = {
        'nav_active': 'dashboard',
        'company': company,
        'expiring_batches': expiring_batches,
        # Placeholders or real queries for remaining metrics
        'purchases_month': 8420.500, 
        'recent_transactions': [], # Wire up to your transaction/journal models later
    }
    return render(request, 'core/dashboard.html', context)