from django.contrib import admin
from .models import CompanySettings

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # Prevent creating multiple setting records (Singleton check)
        return not CompanySettings.objects.exists()