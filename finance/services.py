import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    Account,
    JournalEntry,
    JournalEntryLine,
    ReceiptVoucher,
    PaymentVoucher,
)


def ensure_not_posted(source_type, source_id):
    if JournalEntry.objects.filter(
        source_type=source_type,
        source_id=source_id,
    ).exists():
        raise ValidationError(
            _("This document has already been posted to accounting.")
        )


def get_posting_account(code, expected_type):
    try:
        account = Account.objects.get(code=code)

    except Account.DoesNotExist as exc:
        raise ValidationError(
            _("Required accounting account %(code)s does not exist.") % {
                "code": code,
            }
        ) from exc

    if not account.is_active:
        raise ValidationError(
            _("Accounting account %(code)s is inactive.") % {
                "code": code,
            }
        )

    if not account.allow_posting:
        raise ValidationError(
            _("Accounting account %(code)s does not allow posting.") % {
                "code": code,
            }
        )

    if account.account_type != expected_type:
        raise ValidationError(
            _(
                "Accounting account %(code)s has the wrong type. "
                "Expected %(expected)s but found %(actual)s."
            ) % {
                "code": code,
                "expected": expected_type,
                "actual": account.account_type,
            }
        )

    return account


def validate_cash_bank_account(account):
    if account.account_type != Account.AccountType.ASSET:
        raise ValidationError(
            _("The selected cash or bank account must be an asset account.")
        )

    if not account.is_active:
        raise ValidationError(
            _("The selected cash or bank account is inactive.")
        )

    if not account.allow_posting:
        raise ValidationError(
            _("The selected cash or bank account does not allow posting.")
        )


@transaction.atomic
def post_journal_entry(entry_id, user):
    # Lock the journal entry while we are posting it
    entry = JournalEntry.objects.select_for_update().get(pk=entry_id)

    # 1. Only draft entries can be posted
    if entry.status != JournalEntry.Status.DRAFT:
        raise ValidationError(
            _("Only draft journal entries can be posted.")
        )

    # Get all journal lines
    lines = list(
        entry.lines.select_related("account").all()
    )

    # 2. A journal entry needs at least two lines
    if len(lines) < 2:
        raise ValidationError(
            _("A journal entry must contain at least two lines.")
        )

    total_debit = Decimal("0.000")
    total_credit = Decimal("0.000")

    # 3. Validate every line
    for line in lines:
        line.full_clean()

        if not line.account.is_active:
            raise ValidationError(
                _("Account %(code)s is inactive.") % {"code": line.account.code}
            )

        if not line.account.allow_posting:
            raise ValidationError(
                _("Account %(code)s does not allow posting.") % {
                    "code": line.account.code,
                }
            )

        total_debit += line.debit
        total_credit += line.credit

    # 4. Debit must equal credit
    if total_debit != total_credit:
        raise ValidationError(
            _(
                "Journal entry is not balanced. Debit = %(debit)s, "
                "Credit = %(credit)s."
            ) % {"debit": total_debit, "credit": total_credit}
        )

    # 5. Total cannot be zero
    if total_debit == Decimal("0.000"):
        raise ValidationError(
            _("Journal entry total cannot be zero.")
        )

    # 6. Everything is valid → POST it
    entry.status = JournalEntry.Status.POSTED
    entry.approved_by = user

    entry.save(
        update_fields=[
            "status",
            "approved_by",
            "updated_at",
        ]
    )

    return entry


@transaction.atomic
def post_receipt_voucher(voucher_id, user):
    voucher = (
        ReceiptVoucher.objects
        .select_for_update()
        .get(pk=voucher_id)
    )

    # 1. Only draft vouchers can be posted
    if voucher.status != ReceiptVoucher.Status.DRAFT:
        raise ValidationError(
            _("Only draft receipt vouchers can be posted.")
        )

    # 2. Amount must be greater than zero
    if voucher.amount <= 0:
        raise ValidationError(
            _("Receipt voucher amount must be greater than zero.")
        )

    if voucher.customer is None:
        raise ValidationError(
            _("A customer is required for an accounts receivable receipt.")
        )

    validate_cash_bank_account(voucher.account)
    ensure_not_posted(JournalEntry.SourceType.RECEIPT, voucher.id)

    # 4. Find Accounts Receivable
    receivable_account = get_posting_account(
        "1300",
        Account.AccountType.ASSET,
    )

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
    post_journal_entry(journal.id, user)

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
        .get(pk=voucher_id)
    )

    # 1. Only draft vouchers can be posted
    if voucher.status != PaymentVoucher.Status.DRAFT:
        raise ValidationError(
            _("Only draft payment vouchers can be posted.")
        )

    # 2. Amount must be greater than zero
    if voucher.amount <= 0:
        raise ValidationError(
            _("Payment voucher amount must be greater than zero.")
        )

    if voucher.supplier is None:
        raise ValidationError(
            _("A supplier is required for an accounts payable payment.")
        )

    validate_cash_bank_account(voucher.account)
    ensure_not_posted(JournalEntry.SourceType.PAYMENT, voucher.id)

    # 4. Find Accounts Payable
    payable_account = get_posting_account(
        "2100",
        Account.AccountType.LIABILITY,
    )

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
    post_journal_entry(journal.id, user)

    # 7. Confirm the voucher
    voucher.status = PaymentVoucher.Status.CONFIRMED
    voucher.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return voucher

@transaction.atomic
def post_purchase_invoice(invoice_id, user):
    from purchasing.models import PurchaseInvoice

    invoice = (
        PurchaseInvoice.objects
        .select_for_update()
        .select_related("supplier", "warehouse")
        .prefetch_related("items")
        .get(pk=invoice_id)
    )

    # 1. Only confirmed purchases can be posted
    if invoice.status != PurchaseInvoice.Status.CONFIRMED:
        raise ValidationError(
            _("Only confirmed purchase invoices can be posted to accounting.")
        )

    # 2. Prevent duplicate posting
    ensure_not_posted(
        JournalEntry.SourceType.PURCHASE,
        invoice.id,
    )

    # 3. Calculate inventory value from items
    inventory_value = sum(
        (
            item.quantity * item.unit_cost
            for item in invoice.items.all()
        ),
        Decimal("0.000"),
    )

    if inventory_value <= 0:
        raise ValidationError(
            _("Purchase inventory value must be greater than zero.")
        )

    # 4. Get posting accounts
    inventory_account = get_posting_account(
        "1400",
        Account.AccountType.ASSET,
    )

    if invoice.payment_type == PurchaseInvoice.PaymentType.CASH:
        credit_account = get_posting_account(
            "1100",
            Account.AccountType.ASSET,
        )
    else:
        credit_account = get_posting_account(
            "2100",
            Account.AccountType.LIABILITY,
        )

    # Optional purchase tax account
    purchase_tax_account = None

    if invoice.tax_amount > 0:
        purchase_tax_account = get_posting_account(
            "1500",
            Account.AccountType.ASSET,
        )

    # 5. Calculate final accounting total
    accounting_total = (
        inventory_value
        + invoice.tax_amount
        + invoice.additional_expenses
        - invoice.discount_amount
    )

    if accounting_total <= 0:
        raise ValidationError(
            _("Purchase accounting total must be greater than zero.")
        )
    if accounting_total != invoice.total:
        raise ValidationError(
            _(
                "Purchase accounting total does not match invoice total. "
                "Calculated: %(calculated)s, Invoice total: %(invoice_total)s"
            ) % {
                "calculated": accounting_total,
                "invoice_total": invoice.total,
            }
        )

    # 6. Create journal entry
    journal = JournalEntry.objects.create(
        entry_number=f"PI-{invoice.invoice_number}",
        date=invoice.invoice_date,
        description=_(
            "Purchase Invoice %(invoice_number)s from %(supplier_name)s"
        ) % {
            "invoice_number": invoice.invoice_number,
            "supplier_name": invoice.supplier.name,
        },
        source_type=JournalEntry.SourceType.PURCHASE,
        source_id=invoice.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # 7. Debit Inventory
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=inventory_account,
        description=_(
            "Inventory purchase from %(supplier_name)s"
        ) % {
            "supplier_name": invoice.supplier.name
        },
        debit=inventory_value,
        credit=Decimal("0.000"),
    )

    # 8. Debit Purchase Tax if applicable
    if invoice.tax_amount > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=purchase_tax_account,
            description=_(
                "Purchase tax for invoice %(invoice_number)s"
            ) % {
                "invoice_number": invoice.invoice_number
            },
            debit=invoice.tax_amount,
            credit=Decimal("0.000"),
        )

    # 9. Handle additional expenses
    if invoice.additional_expenses > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory_account,
            description=_(
                "Additional purchase expenses for %(invoice_number)s"
            ) % {
                "invoice_number": invoice.invoice_number
            },
            debit=invoice.additional_expenses,
            credit=Decimal("0.000"),
        )

    # 10. Handle discount
    if invoice.discount_amount > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory_account,
            description=_(
                "Purchase discount for %(invoice_number)s"
            ) % {
                "invoice_number": invoice.invoice_number
            },
            debit=Decimal("0.000"),
            credit=invoice.discount_amount,
        )

    # 11. Credit Cash or Accounts Payable
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=credit_account,
        supplier=(
            invoice.supplier
            if invoice.payment_type == PurchaseInvoice.PaymentType.CREDIT
            else None
        ),
        description=_(
            "Purchase invoice %(invoice_number)s settlement"
        ) % {
            "invoice_number": invoice.invoice_number
        },
        debit=Decimal("0.000"),
        credit=accounting_total,
    )

    # 12. Post the journal
    return post_journal_entry(
        journal.id,
        user,
    )
@transaction.atomic
def post_pos_sale(sale_id, user):
    from pos.models import POSSale, POSPayment

    sale = (
        POSSale.objects
        .select_for_update()
        .prefetch_related("items", "payments")
        .get(pk=sale_id)
    )

    # Only completed sales can reach accounting
    if sale.status != POSSale.SaleStatus.COMPLETED:
        raise ValidationError(
            _("Only completed POS sales can be posted.")
        )

    entry_number = f"POS-{sale.sale_number}"
    ensure_not_posted(JournalEntry.SourceType.POS_SALE, sale.id)

    sales_revenue_account = get_posting_account(
        "4100",
        Account.AccountType.REVENUE,
    )
    cogs_account = get_posting_account(
        "5100",
        Account.AccountType.EXPENSE,
    )
    inventory_account = get_posting_account(
        "1400",
        Account.AccountType.ASSET,
    )

    payments = list(sale.payments.all())

    if not payments:
        raise ValidationError(
            _("A completed POS sale must contain at least one payment.")
        )

    # Calculate COGS
    total_cogs = sum(
        (
            item.quantity * item.unit_cost
            for item in sale.items.all()
        ),
        Decimal("0.000"),
    )
    total_tax = sum(
        (item.tax_amount for item in sale.items.all()),
        Decimal("0.000"),
    )
    net_revenue = sale.total - total_tax

    if net_revenue < 0:
        raise ValidationError(
            _("POS sale tax cannot exceed the sale total.")
        )

    journal = JournalEntry.objects.create(
        entry_number=entry_number,
        date=sale.date.date(),
        description=_(
            "POS Sale %(sale_number)s"
        ) % {
            "sale_number": sale.sale_number
        },
        source_type=JournalEntry.SourceType.POS_SALE,
        source_id=sale.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Amount of change given back to customer.
    # Change must reduce the accounting value of CASH received.
    remaining_change = sale.change_amount

    total_payment_posted = Decimal("0.000")

    for payment in payments:
        amount = payment.amount

        # If customer gave extra physical cash,
        # subtract the change from the cash debit.
        if (
            payment.payment_method == POSPayment.PaymentMethod.CASH
            and remaining_change > 0
        ):
            change_from_this_payment = min(
                amount,
                remaining_change,
            )

            amount -= change_from_this_payment
            remaining_change -= change_from_this_payment

        if amount <= 0:
            continue

        if payment.payment_method == POSPayment.PaymentMethod.CASH:
            debit_account = get_posting_account(
                "1100",
                Account.AccountType.ASSET,
            )

        elif payment.payment_method == POSPayment.PaymentMethod.CARD:
            debit_account = get_posting_account(
                "1210",
                Account.AccountType.ASSET,
            )

        elif payment.payment_method == POSPayment.PaymentMethod.BANK:
            debit_account = get_posting_account(
                "1200",
                Account.AccountType.ASSET,
            )

        elif payment.payment_method == POSPayment.PaymentMethod.CREDIT:
            if sale.customer is None:
                raise ValidationError(
                    _(
                        "Credit sales require a customer."
                    )
                )

            debit_account = get_posting_account(
                "1300",
                Account.AccountType.ASSET,
            )

        else:
            raise ValidationError(
                _(
                    "Unsupported payment method: %(method)s"
                ) % {
                    "method": payment.payment_method
                }
            )

        total_payment_posted += amount

        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=debit_account,
            customer=(
                sale.customer
                if payment.payment_method
                == POSPayment.PaymentMethod.CREDIT
                else None
            ),
            description=_(
                "%(method)s payment for POS sale %(sale)s"
            ) % {
                "method": payment.get_payment_method_display(),
                "sale": sale.sale_number,
            },
            debit=amount,
            credit=Decimal("0.000"),
        )

    if remaining_change > 0:
        raise ValidationError(
            _(
                "Sale change exceeds the available cash payment."
            )
        )

    # After accounting for change, payments must equal sale total
    if total_payment_posted != sale.total:
        raise ValidationError(
            _(
                "POS payment total does not match sale total. "
                "Payments: %(payments)s, Sale total: %(total)s"
            ) % {
                "payments": total_payment_posted,
                "total": sale.total,
            }
        )

    # Sales Revenue
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=sales_revenue_account,
        description=_(
            "Sales revenue for %(sale)s"
        ) % {
            "sale": sale.sale_number,
        },
        debit=Decimal("0.000"),
        credit=net_revenue,
    )

    if total_tax > 0:
        tax_payable_account = get_posting_account(
            "2200",
            Account.AccountType.LIABILITY,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=tax_payable_account,
            description=_("Sales tax for %(sale)s") % {
                "sale": sale.sale_number,
            },
            debit=Decimal("0.000"),
            credit=total_tax,
        )

    # Cost of Goods Sold
    if total_cogs > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=cogs_account,
            description=_(
                "COGS for %(sale)s"
            ) % {
                "sale": sale.sale_number,
            },
            debit=total_cogs,
            credit=Decimal("0.000"),
        )

        # Inventory reduction
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory_account,
            description=_(
                "Inventory reduction for %(sale)s"
            ) % {
                "sale": sale.sale_number,
            },
            debit=Decimal("0.000"),
            credit=total_cogs,
        )

    post_journal_entry(journal.id, user)

    return journal

@transaction.atomic
def post_waste_loss(waste_id, user):
    """
    Creates and posts a journal entry for confirmed Waste & Loss:
    Debit: Waste & Loss Expense (Account 6300)
    Credit: Inventory (Account 1400)
    """
    from inventory.models import WasteLoss
    waste = (
        WasteLoss.objects
        .select_for_update()
        .prefetch_related("items")
        .get(pk=waste_id)
    )

    if waste.status != WasteLoss.Status.CONFIRMED:
        raise ValidationError(_("Only confirmed waste and loss documents can be posted."))

    ensure_not_posted(JournalEntry.SourceType.WASTE, waste.id)

    waste_account = get_posting_account(
        "6300",
        Account.AccountType.EXPENSE,
    )
    inventory_account = get_posting_account(
        "1400",
        Account.AccountType.ASSET,
    )

    total_waste_cost = sum((item.total_cost for item in waste.items.all()), Decimal("0.000"))

    if total_waste_cost <= 0:
        raise ValidationError(_("Waste document total cost cannot be zero."))

    journal = JournalEntry.objects.create(
        entry_number=f"WST-{waste.document_number}",
        date=waste.date,
        description=_("Waste & Loss document %(document_number)s") % {'document_number': waste.document_number},
        source_type=JournalEntry.SourceType.WASTE,
        source_id=waste.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=waste_account,
        description=_("Waste write-off for document %(document_number)s") % {'document_number': waste.document_number},
        debit=total_waste_cost,
        credit=Decimal("0.000"),
    )

    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=inventory_account,
        description=_("Inventory reduction due to waste %(document_number)s") % {'document_number': waste.document_number},
        debit=Decimal("0.000"),
        credit=total_waste_cost,
    )

    return post_journal_entry(journal.id, user)


@transaction.atomic
def reverse_journal_entry(entry_id, user, reason):
    original = (
        JournalEntry.objects
        .select_for_update()
        .prefetch_related("lines__account")
        .get(pk=entry_id)
    )

    if hasattr(original, "reversal_entry"):
        raise ValidationError(
            _("This journal entry has already been reversed.")
        )

    if original.status != JournalEntry.Status.POSTED:
        raise ValidationError(
            _("Only posted journal entries can be reversed.")
        )

    if original.source_type == JournalEntry.SourceType.REVERSAL:
        raise ValidationError(
            _("A reversal journal entry cannot be reversed.")
        )

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            _("A reversal reason is required.")
        )

    reversal = JournalEntry.objects.create(
        entry_number=f"REV-{uuid.uuid4().hex[:12].upper()}",
        date=timezone.localdate(),
        description=_("Reversal of journal entry %(number)s") % {
            "number": original.entry_number,
        },
        source_type=JournalEntry.SourceType.REVERSAL,
        source_id=original.pk,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
        reversal_of=original,
    )

    JournalEntryLine.objects.bulk_create(
        [
            JournalEntryLine(
                journal_entry=reversal,
                account=line.account,
                description=line.description,
                debit=line.credit,
                credit=line.debit,
            )
            for line in original.lines.all()
        ]
    )

    reversal = post_journal_entry(reversal.pk, user)

    original.status = JournalEntry.Status.REVERSED
    original.reversal_reason = reason
    original.reversed_by = user
    original.reversed_at = timezone.now()
    original.save(
        update_fields=[
            "status",
            "reversal_reason",
            "reversed_by",
            "reversed_at",
            "updated_at",
        ]
    )

    return reversal
