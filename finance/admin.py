from django.contrib import admin

from .models import Account, JournalEntry, JournalEntryLine


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "account_type",
        "parent",
        "allow_posting",
        "is_active",
    )

    list_filter = (
        "account_type",
        "allow_posting",
        "is_active",
    )

    search_fields = (
        "code",
        "name",
    )


class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    extra = 2


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = (
        "entry_number",
        "date",
        "source_type",
        "status",
        "created_by",
    )

    list_filter = (
        "status",
        "source_type",
        "date",
    )

    search_fields = (
        "entry_number",
        "description",
    )

    inlines = [
        JournalEntryLineInline,
    ]