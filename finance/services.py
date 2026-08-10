from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    Account,
    JournalEntry,
    JournalEntryLine,
    ReceiptVoucher,
    PaymentVoucher,
)


@transaction.atomic
def post_journal_entry(entry_id):
    # Lock the journal entry while we are posting it
    entry = JournalEntry.objects.select_for_update().get(pk=entry_id)

    # 1. Only draft entries can be posted
    if entry.status != JournalEntry.Status.DRAFT:
        raise ValidationError(
            "Only draft journal entries can be posted."
        )

    # Get all journal lines
    lines = list(
        entry.lines.select_related("account").all()
    )

    # 2. A journal entry needs at least two lines
    if len(lines) < 2:
        raise ValidationError(
            "A journal entry must contain at least two lines."
        )

    total_debit = Decimal("0.000")
    total_credit = Decimal("0.000")

    # 3. Validate every line
    for line in lines:
        line.full_clean()

        if not line.account.is_active:
            raise ValidationError(
                f"Account {line.account.code} is inactive."
            )

        if not line.account.allow_posting:
            raise ValidationError(
                f"Account {line.account.code} does not allow posting."
            )

        total_debit += line.debit
        total_credit += line.credit

    # 4. Debit must equal credit
    if total_debit != total_credit:
        raise ValidationError(
            f"Journal entry is not balanced. "
            f"Debit = {total_debit}, "
            f"Credit = {total_credit}."
        )

    # 5. Total cannot be zero
    if total_debit == Decimal("0.000"):
        raise ValidationError(
            "Journal entry total cannot be zero."
        )

    # 6. Everything is valid → POST it
    entry.status = JournalEntry.Status.POSTED

    entry.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return entry


@transaction.atomic
def post_receipt_voucher(voucher_id, user):
    voucher = (
        ReceiptVoucher.objects
        .select_for_update()
        .select_related("customer", "account")
        .get(pk=voucher_id)
    )

    # 1. Only draft vouchers can be posted
    if voucher.status != ReceiptVoucher.Status.DRAFT:
        raise ValidationError(
            "Only draft receipt vouchers can be posted."
        )

    # 2. Amount must be greater than zero
    if voucher.amount <= 0:
        raise ValidationError(
            "Receipt voucher amount must be greater than zero."
        )

    # 3. Cash/Bank account must be active and allow posting
    if not voucher.account.is_active:
        raise ValidationError(
            "The selected cash/bank account is inactive."
        )

    if not voucher.account.allow_posting:
        raise ValidationError(
            "The selected cash/bank account does not allow posting."
        )

    # 4. Find Accounts Receivable
    receivable_account = Account.objects.get(code="1300")

    # 5. Create journal entry
    journal = JournalEntry.objects.create(
        entry_number=f"RV-{voucher.voucher_number}",
        date=voucher.date,
        description=f"Receipt Voucher {voucher.voucher_number}",
        source_type=JournalEntry.SourceType.RECEIPT,
        source_id=voucher.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Cash / Bank increases → Debit
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=voucher.account,
        debit=voucher.amount,
        credit=Decimal("0.000"),
        description=f"Receipt from {voucher.received_from}",
    )

    # Accounts Receivable decreases → Credit
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=receivable_account,
        customer=voucher.customer,
        debit=Decimal("0.000"),
        credit=voucher.amount,
        description=f"Customer receipt {voucher.voucher_number}",
    )

    # 6. Post the journal using your existing validation
    post_journal_entry(journal.id)

    # 7. Confirm the voucher
    voucher.status = ReceiptVoucher.Status.CONFIRMED
    voucher.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return voucher



@transaction.atomic
def post_payment_voucher(voucher_id, user):
    voucher = (
        PaymentVoucher.objects
        .select_for_update()
        .select_related("supplier", "account")
        .get(pk=voucher_id)
    )

    # 1. Only draft vouchers can be posted
    if voucher.status != PaymentVoucher.Status.DRAFT:
        raise ValidationError(
            "Only draft payment vouchers can be posted."
        )

    # 2. Amount must be greater than zero
    if voucher.amount <= 0:
        raise ValidationError(
            "Payment voucher amount must be greater than zero."
        )

    # 3. Cash/Bank account must be active and allow posting
    if not voucher.account.is_active:
        raise ValidationError(
            "The selected cash/bank account is inactive."
        )

    if not voucher.account.allow_posting:
        raise ValidationError(
            "The selected cash/bank account does not allow posting."
        )

    # 4. Find Accounts Payable
    payable_account = Account.objects.get(code="2100")

    # 5. Create journal entry
    journal = JournalEntry.objects.create(
        entry_number=f"PV-{voucher.voucher_number}",
        date=voucher.date,
        description=f"Payment Voucher {voucher.voucher_number}",
        source_type=JournalEntry.SourceType.PAYMENT,
        source_id=voucher.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Accounts Payable decreases → Debit
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=payable_account,
        supplier=voucher.supplier,
        debit=voucher.amount,
        credit=Decimal("0.000"),
        description=f"Payment to {voucher.paid_to}",
    )

    # Cash / Bank decreases → Credit
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=voucher.account,
        debit=Decimal("0.000"),
        credit=voucher.amount,
        description=f"Payment voucher {voucher.voucher_number}",
    )

    # 6. Post the journal using existing validation
    post_journal_entry(journal.id)

    # 7. Confirm the voucher
    voucher.status = PaymentVoucher.Status.CONFIRMED
    voucher.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return voucher