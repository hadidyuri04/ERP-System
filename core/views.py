from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from core.permissions import admin_required

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