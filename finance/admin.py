from django.contrib import admin

from .models import (
    Account,
    JournalEntry,
    JournalEntryLine,
    ReceiptVoucher,
    PaymentVoucher,
    TaxRate,
)

from .services import (
    post_receipt_voucher,
    post_payment_voucher,
)


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "rate", "subject_to_tax", "is_active")
    list_filter = ("subject_to_tax", "is_active")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")


# =========================
# ADMIN ACTIONS
# =========================

@admin.action(description="Post selected receipt vouchers")
def post_selected_receipts(modeladmin, request, queryset):
    for voucher in queryset:
        try:
            post_receipt_voucher(
                voucher.id,
                request.user,
            )

            modeladmin.message_user(
                request,
                f"{voucher.voucher_number} posted successfully.",
            )

        except Exception as exc:
            modeladmin.message_user(
                request,
                f"{voucher.voucher_number}: {exc}",
                level="ERROR",
            )


@admin.action(description="Post selected payment vouchers")
def post_selected_payments(modeladmin, request, queryset):
    for voucher in queryset:
        try:
            post_payment_voucher(
                voucher.id,
                request.user,
            )

            modeladmin.message_user(
                request,
                f"{voucher.voucher_number} posted successfully.",
            )

        except Exception as exc:
            modeladmin.message_user(
                request,
                f"{voucher.voucher_number}: {exc}",
                level="ERROR",
            )


# =========================
# ACCOUNT ADMIN
# =========================

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


# =========================
# JOURNAL ENTRY LINE INLINE
# =========================

class JournalEntryLineInline(admin.TabularInline):
    model = JournalEntryLine
    extra = 2

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status in (
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ):
            return (
                "account",
                "customer",
                "supplier",
                "description",
                "debit",
                "credit",
                "created_at",
            )

        return (
            "created_at",
        )

    def has_add_permission(self, request, obj=None):
        if obj and obj.status in (
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ):
            return False

        return super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in (
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ):
            return False

        return super().has_delete_permission(request, obj)


# =========================
# JOURNAL ENTRY ADMIN
# =========================

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = (
        "entry_number",
        "date",
        "source_type",
        "status",
        "created_by",
        "approved_by",
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

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status in (
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ):
            return (
                "entry_number",
                "date",
                "description",
                "source_type",
                "source_id",
                "status",
                "created_by",
                "approved_by",
                "reversal_of",
                "reversal_reason",
                "reversed_by",
                "reversed_at",
                "created_at",
                "updated_at",
            )

        return (
            "status",
            "approved_by",
            "reversal_of",
            "reversal_reason",
            "reversed_by",
            "reversed_at",
            "created_at",
            "updated_at",
        )

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status in (
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ):
            return False

        return super().has_delete_permission(request, obj)


# =========================
# RECEIPT VOUCHER ADMIN
# =========================

@admin.register(ReceiptVoucher)
class ReceiptVoucherAdmin(admin.ModelAdmin):
    list_display = (
        "voucher_number",
        "date",
        "received_from",
        "customer",
        "account",
        "amount",
        "payment_method",
        "status",
    )

    list_filter = (
        "status",
        "payment_method",
        "date",
    )

    search_fields = (
        "voucher_number",
        "received_from",
        "customer__name",
        "reference",
    )

    actions = [
        post_selected_receipts,
    ]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == ReceiptVoucher.Status.CONFIRMED:
            return (
                "voucher_number",
                "date",
                "customer",
                "received_from",
                "account",
                "amount",
                "payment_method",
                "reference",
                "description",
                "status",
                "created_by",
                "created_at",
                "updated_at",
            )

        return (
            "status",
            "created_at",
            "updated_at",
        )

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == ReceiptVoucher.Status.CONFIRMED:
            return False

        return super().has_delete_permission(request, obj)


# =========================
# PAYMENT VOUCHER ADMIN
# =========================

@admin.register(PaymentVoucher)
class PaymentVoucherAdmin(admin.ModelAdmin):
    list_display = (
        "voucher_number",
        "date",
        "paid_to",
        "supplier",
        "account",
        "amount",
        "payment_method",
        "status",
    )

    list_filter = (
        "status",
        "payment_method",
        "date",
    )

    search_fields = (
        "voucher_number",
        "paid_to",
        "supplier__name",
        "reference",
    )

    actions = [
        post_selected_payments,
    ]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == PaymentVoucher.Status.CONFIRMED:
            return (
                "voucher_number",
                "date",
                "supplier",
                "paid_to",
                "account",
                "amount",
                "payment_method",
                "reference",
                "description",
                "status",
                "created_by",
                "created_at",
                "updated_at",
            )

        return (
            "status",
            "created_at",
            "updated_at",
        )

    def has_delete_permission(self, request, obj=None):
        if obj and obj.status == PaymentVoucher.Status.CONFIRMED:
            return False

        return super().has_delete_permission(request, obj)
