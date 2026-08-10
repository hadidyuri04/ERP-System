from django.contrib import admin

from .models import (
    Account,
    JournalEntry,
    JournalEntryLine,
    ReceiptVoucher,
    PaymentVoucher,
)

from .services import (
    post_receipt_voucher,
    post_payment_voucher,
)


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
# JOURNAL ENTRY ADMIN
# =========================

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
    readonly_fields = (
        "status",
        "created_at",
        "updated_at",
    )


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
    readonly_fields = (
        "status",
        "created_at",
        "updated_at",
    )