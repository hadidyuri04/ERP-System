from django.urls import path
from .views import dashboard_redirect_view, admin_dashboard_view, accountant_dashboard_view, company_settings_view, trigger_database_backup

app_name = 'core'

urlpatterns = [
    path('dashboard/', dashboard_redirect_view, name='dashboard'),
    path('dashboard/admin/', admin_dashboard_view, name='admin_dashboard'),
    path('dashboard/accountant/', accountant_dashboard_view, name='accountant_dashboard'),
    path('settings/', company_settings_view, name='settings'),
    path('settings/backup/', trigger_database_backup, name='backup'),
]