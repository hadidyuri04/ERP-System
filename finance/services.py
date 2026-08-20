import calendar
import uuid
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .audit import record_finance_audit
from .models import (
    Account,
    FinanceAuditLog,
    FiscalPeriod,
    FiscalPeriodAction,
    FiscalYear,
    JournalEntry,
    JournalEntryLine,
    OpenItem,
    OpenItemAllocation,
    PeriodStatus,
    ReceiptVoucher,
    PaymentVoucher,
)


DEFAULT_POSTING_ACCOUNT_CODES = {
    "cash": "1100",
    "bank": "1200",
    "card_clearing": "1210",
    "accounts_receivable": "1300",
    "inventory": "1400",
    "purchase_tax": "1500",
    "accounts_payable": "2100",
    "sales_tax_payable": "2200",
    "sales_revenue": "4100",
    "inventory_adjustment_gain": "4300",
    "cost_of_goods_sold": "5100",
    "waste_loss": "6300",
    "inventory_adjustment_loss": "6310",
}


def _allocate_open_items(
    *, item_type, party, settlement_line, allocation_date, user,
    target_open_item_id=None,
):
    """Allocate a settlement to the party's oldest open documents first."""
    party_filter = (
        {"customer": party}
        if item_type == OpenItem.ItemType.RECEIVABLE
        else {"supplier": party}
    )
    amount_left = settlement_line.credit
    if item_type == OpenItem.ItemType.PAYABLE:
        amount_left = settlement_line.debit

    open_items_query = OpenItem.objects.select_for_update().filter(
        item_type=item_type,
        document_date__lte=allocation_date,
        **party_filter,
    )
    if target_open_item_id is not None:
        open_items_query = open_items_query.filter(pk=target_open_item_id)
    open_items = list(open_items_query.order_by("due_date", "document_date", "id"))
    if target_open_item_id is not None and not open_items:
        raise ValidationError(_("The selected invoice is not an open item for this customer."))
    allocation_totals = {
        row["open_item_id"]: row["total"]
        for row in OpenItemAllocation.objects.filter(open_item__in=open_items)
        .values("open_item_id")
        .annotate(total=Sum("amount"))
    }
    for open_item in open_items:
        if amount_left <= 0:
            break
        allocated = allocation_totals.get(open_item.pk, Decimal("0.000"))
        remaining = open_item.original_amount - allocated
        if remaining <= 0:
            continue
        amount = min(remaining, amount_left)
        OpenItemAllocation.objects.create(
            open_item=open_item,
            journal_line=settlement_line,
            allocation_date=allocation_date,
            amount=amount,
            created_by=user,
        )
        amount_left -= amount

    if target_open_item_id is not None and amount_left > 0:
        raise ValidationError(_("Payment cannot exceed the selected invoice outstanding balance."))

    return amount_left


def ensure_not_posted(source_type, source_id):
    if JournalEntry.objects.filter(
        source_type=source_type,
        source_id=source_id,
    ).exists():
        raise ValidationError(
            _("This document has already been posted to accounting.")
        )


def get_posting_account(account_key, expected_type):
    """Resolve a semantic posting key to its configured chart account."""
    configured_codes = {
        **DEFAULT_POSTING_ACCOUNT_CODES,
        **getattr(settings, "FINANCE_POSTING_ACCOUNTS", {}),
    }
    # Accept a literal code for backwards compatibility with existing callers.
    code = configured_codes.get(account_key, account_key)
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

    if not account.is_cash_equivalent:
        raise ValidationError(
            _("The selected account is not marked as cash or a cash equivalent.")
        )


def classify_cash_flow_activity(
    lines,
    *,
    selected_activity=JournalEntry.CashFlowActivity.NONE,
    automatic_activity=None,
):
    """Return the activity based on cash lines and the business workflow."""
    affects_cash = any(line.account.is_cash_equivalent for line in lines)
    if not affects_cash:
        return JournalEntry.CashFlowActivity.NONE

    classified_activities = {
        JournalEntry.CashFlowActivity.OPERATING,
        JournalEntry.CashFlowActivity.INVESTING,
        JournalEntry.CashFlowActivity.FINANCING,
    }
    if automatic_activity is not None:
        if automatic_activity not in classified_activities:
            raise ValidationError(
                _("An automatic cash journal requires a valid cash-flow activity.")
            )
        return automatic_activity

    if selected_activity not in classified_activities:
        raise ValidationError(
            _("Select a cash-flow activity before posting a journal that changes cash.")
        )
    return selected_activity


def validate_journal_entry_for_posting(
    entry,
    *,
    lock_period=True,
    automatic_activity=None,
):
    """Validate a journal without changing it and return its posting totals."""
    # 1. Only draft entries can be posted
    if entry.status != JournalEntry.Status.DRAFT:
        raise ValidationError(
            _("Only draft journal entries can be posted.")
        )

    ensure_posting_period_open(entry.date, lock=lock_period)

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

    entry.cash_flow_activity = classify_cash_flow_activity(
        lines,
        selected_activity=entry.cash_flow_activity,
        automatic_activity=automatic_activity,
    )

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
    return lines, total_debit, total_credit


@transaction.atomic
def post_journal_entry(entry_id, user, *, automatic_activity=None):
    # Lock the journal entry while we are posting it.
    entry = JournalEntry.objects.select_for_update().get(pk=entry_id)
    lines, total_debit, total_credit = validate_journal_entry_for_posting(
        entry,
        automatic_activity=automatic_activity,
    )

    entry.status = JournalEntry.Status.POSTED
    entry.approved_by = user

    entry.save(
        update_fields=[
            "status",
            "approved_by",
            "cash_flow_activity",
            "updated_at",
        ]
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=entry,
        changes={
            "status": {"before": JournalEntry.Status.DRAFT, "after": entry.status},
            "total_debit": {"before": None, "after": str(total_debit)},
            "total_credit": {"before": None, "after": str(total_credit)},
        },
    )

    return entry


@transaction.atomic
def post_receipt_voucher(voucher_id, user, *, target_open_item_id=None):
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
        "accounts_receivable",
        Account.AccountType.ASSET,
    )

    # 5. Create journal entry
    journal = JournalEntry.objects.create(
        entry_number=f"RV-{voucher.voucher_number}",
        date=voucher.date,
        description=f"Receipt Voucher {voucher.voucher_number}",
        source_type=JournalEntry.SourceType.RECEIPT,
        source_id=voucher.id,
        cash_flow_activity=JournalEntry.CashFlowActivity.NONE,
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
    receivable_line = JournalEntryLine.objects.create(
        journal_entry=journal,
        account=receivable_account,
        customer=voucher.customer,
        debit=Decimal("0.000"),
        credit=voucher.amount,
        description=f"Customer receipt {voucher.voucher_number}",
    )

    # 6. Post the journal using your existing validation
    post_journal_entry(
        journal.id,
        user,
        automatic_activity=JournalEntry.CashFlowActivity.OPERATING,
    )
    _allocate_open_items(
        item_type=OpenItem.ItemType.RECEIVABLE,
        party=voucher.customer,
        settlement_line=receivable_line,
        allocation_date=voucher.date,
        user=user,
        target_open_item_id=target_open_item_id,
    )

    # 7. Confirm the voucher
    voucher.status = ReceiptVoucher.Status.CONFIRMED
    voucher.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=voucher,
        changes={
            "status": {"before": ReceiptVoucher.Status.DRAFT, "after": voucher.status},
            "journal_entry": {"before": None, "after": journal.entry_number},
            "amount": {"before": None, "after": str(voucher.amount)},
        },
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
        "accounts_payable",
        Account.AccountType.LIABILITY,
    )

    # 5. Create journal entry
    journal = JournalEntry.objects.create(
        entry_number=f"PV-{voucher.voucher_number}",
        date=voucher.date,
        description=f"Payment Voucher {voucher.voucher_number}",
        source_type=JournalEntry.SourceType.PAYMENT,
        source_id=voucher.id,
        cash_flow_activity=JournalEntry.CashFlowActivity.NONE,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Accounts Payable decreases → Debit
    payable_line = JournalEntryLine.objects.create(
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
    post_journal_entry(
        journal.id,
        user,
        automatic_activity=JournalEntry.CashFlowActivity.OPERATING,
    )
    _allocate_open_items(
        item_type=OpenItem.ItemType.PAYABLE,
        party=voucher.supplier,
        settlement_line=payable_line,
        allocation_date=voucher.date,
        user=user,
    )

    # 7. Confirm the voucher
    voucher.status = PaymentVoucher.Status.CONFIRMED
    voucher.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=voucher,
        changes={
            "status": {"before": PaymentVoucher.Status.DRAFT, "after": voucher.status},
            "journal_entry": {"before": None, "after": journal.entry_number},
            "amount": {"before": None, "after": str(voucher.amount)},
        },
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
        "inventory",
        Account.AccountType.ASSET,
    )

    # The settlement accounts are chosen in step 11 from what was actually
    # paid, not from payment_type alone, so a part payment splits correctly.

    # Optional purchase tax account
    purchase_tax_account = None

    if invoice.tax_amount > 0:
        purchase_tax_account = get_posting_account(
            "purchase_tax",
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
        cash_flow_activity=JournalEntry.CashFlowActivity.NONE,
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

    # 11. Credit what was actually paid, and owe the rest.
    #
    # paid_amount used to be ignored entirely: the full total was credited to
    # Cash on a cash invoice, so a part payment left the outstanding balance
    # invisible. Now the settlement is split between Cash and Payables.
    cash_account = get_posting_account("cash", Account.AccountType.ASSET)
    payable_account = get_posting_account("accounts_payable", Account.AccountType.LIABILITY)

    paid = invoice.paid_amount or Decimal("0.000")

    if paid < 0:
        raise ValidationError(_("Paid amount cannot be negative."))

    if invoice.payment_type == PurchaseInvoice.PaymentType.CASH:
        # Nothing entered on a cash invoice means paid in full.
        if paid == 0:
            paid = accounting_total

        if paid != accounting_total:
            raise ValidationError(
                _(
                    "A cash invoice must be paid in full before it can be "
                    "confirmed. Paid: %(paid)s, required: %(total)s."
                ) % {
                    "paid": paid,
                    "total": accounting_total,
                }
            )
    else:
        # A credit invoice is by definition unpaid at confirmation. Settle it
        # afterwards with a Payment Voucher.
        if paid != 0:
            raise ValidationError(
                _(
                    "A credit invoice cannot carry a paid amount. Confirm it "
                    "with zero paid, then settle it using a payment voucher."
                )
            )

    outstanding = accounting_total - paid

    if paid > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=cash_account,
            description=_(
                "Purchase invoice %(invoice_number)s paid"
            ) % {
                "invoice_number": invoice.invoice_number
            },
            debit=Decimal("0.000"),
            credit=paid,
        )

    if outstanding > 0:
        payable_line = JournalEntryLine.objects.create(
            journal_entry=journal,
            account=payable_account,
            supplier=invoice.supplier,
            description=_(
                "Purchase invoice %(invoice_number)s outstanding balance"
            ) % {
                "invoice_number": invoice.invoice_number
            },
            debit=Decimal("0.000"),
            credit=outstanding,
        )
        OpenItem.objects.create(
            item_type=OpenItem.ItemType.PAYABLE,
            supplier=invoice.supplier,
            journal_line=payable_line,
            document_number=invoice.invoice_number,
            document_date=invoice.invoice_date,
            due_date=invoice.due_date or invoice.invoice_date,
            original_amount=outstanding,
        )

    # 12. Post the journal
    posted_journal = post_journal_entry(
        journal.id,
        user,
        automatic_activity=JournalEntry.CashFlowActivity.OPERATING,
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=invoice,
        changes={
            "journal_entry": {"before": None, "after": posted_journal.entry_number},
            "total": {"before": None, "after": str(invoice.total)},
        },
    )
    return posted_journal


@transaction.atomic
def post_sales_invoice(invoice_id, user):
    from sales.models import SalesInvoice

    invoice = (
        SalesInvoice.objects.select_for_update()
        .select_related("customer")
        .prefetch_related("items")
        .get(pk=invoice_id)
    )
    if invoice.status != SalesInvoice.Status.POSTED:
        raise ValidationError(_("Only posted sales invoices can be sent to accounting."))
    ensure_not_posted(JournalEntry.SourceType.SALES_INVOICE, invoice.id)

    receivable = get_posting_account("accounts_receivable", Account.AccountType.ASSET)
    revenue = get_posting_account("sales_revenue", Account.AccountType.REVENUE)
    cogs = get_posting_account("cost_of_goods_sold", Account.AccountType.EXPENSE)
    inventory = get_posting_account("inventory", Account.AccountType.ASSET)
    net_revenue = invoice.subtotal - invoice.discount_amount
    total_cogs = sum(
        (item.quantity * item.unit_cost for item in invoice.items.all()),
        Decimal("0.000"),
    )
    if net_revenue < 0 or net_revenue + invoice.tax_amount != invoice.total:
        raise ValidationError(_("Sales invoice accounting totals do not match the invoice total."))

    journal = JournalEntry.objects.create(
        entry_number=f"SI-{invoice.id}",
        date=invoice.invoice_date,
        description=_("Sales invoice %(number)s") % {"number": invoice.invoice_number},
        source_type=JournalEntry.SourceType.SALES_INVOICE,
        source_id=invoice.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )
    receivable_line = JournalEntryLine.objects.create(
        journal_entry=journal,
        account=receivable,
        customer=invoice.customer,
        description=_("Customer invoice %(number)s") % {"number": invoice.invoice_number},
        debit=invoice.total,
    )
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=revenue,
        description=_("Sales revenue for invoice %(number)s") % {"number": invoice.invoice_number},
        credit=net_revenue,
    )
    if invoice.tax_amount:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=get_posting_account("sales_tax_payable", Account.AccountType.LIABILITY),
            description=_("Sales tax for invoice %(number)s") % {"number": invoice.invoice_number},
            credit=invoice.tax_amount,
        )
    if total_cogs:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=cogs,
            description=_("COGS for invoice %(number)s") % {"number": invoice.invoice_number},
            debit=total_cogs,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory,
            description=_("Inventory issued for invoice %(number)s") % {"number": invoice.invoice_number},
            credit=total_cogs,
        )
    OpenItem.objects.create(
        item_type=OpenItem.ItemType.RECEIVABLE,
        customer=invoice.customer,
        journal_line=receivable_line,
        document_number=invoice.invoice_number,
        document_date=invoice.invoice_date,
        due_date=invoice.due_date,
        original_amount=invoice.total,
    )
    posted = post_journal_entry(journal.id, user)
    record_finance_audit(
        actor=user, action=FinanceAuditLog.Action.POSTED, instance=invoice,
        changes={
            "journal_entry": {"before": None, "after": posted.entry_number},
            "total": {"before": None, "after": str(invoice.total)},
        },
    )
    return posted


@transaction.atomic
def post_sales_credit_note(credit_note_id, user):
    from sales.models import SalesCreditNote

    note = SalesCreditNote.objects.select_for_update().select_related(
        "invoice", "invoice__customer"
    ).get(pk=credit_note_id)
    if note.status != SalesCreditNote.Status.POSTED:
        raise ValidationError(_("Only posted sales credit notes can be sent to accounting."))
    ensure_not_posted(JournalEntry.SourceType.SALES_CREDIT_NOTE, note.id)
    original = JournalEntry.objects.select_for_update().prefetch_related(
        "lines__account"
    ).get(
        source_type=JournalEntry.SourceType.SALES_INVOICE,
        source_id=note.invoice_id,
    )
    journal = JournalEntry.objects.create(
        entry_number=f"SCN-{note.id}",
        date=note.date,
        description=_("Credit note %(number)s for invoice %(invoice)s") % {
            "number": note.credit_note_number,
            "invoice": note.invoice.invoice_number,
        },
        source_type=JournalEntry.SourceType.SALES_CREDIT_NOTE,
        source_id=note.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )
    receivable_credit_line = None
    for original_line in original.lines.all():
        line = JournalEntryLine.objects.create(
            journal_entry=journal,
            account=original_line.account,
            customer=original_line.customer,
            supplier=original_line.supplier,
            description=_("Credit note reversal of %(invoice)s") % {
                "invoice": note.invoice.invoice_number,
            },
            debit=original_line.credit,
            credit=original_line.debit,
        )
        if original_line.customer_id and line.credit:
            receivable_credit_line = line

    posted = post_journal_entry(journal.id, user)
    open_item = note.invoice.open_item
    if open_item is None or receivable_credit_line is None:
        raise ValidationError(_("The invoice receivable could not be found for this credit note."))
    _allocate_open_items(
        item_type=OpenItem.ItemType.RECEIVABLE,
        party=note.invoice.customer,
        settlement_line=receivable_credit_line,
        allocation_date=note.date,
        user=user,
        target_open_item_id=open_item.id,
    )
    record_finance_audit(
        actor=user, action=FinanceAuditLog.Action.POSTED, instance=note,
        changes={"journal_entry": {"before": None, "after": posted.entry_number}},
    )
    return posted


@transaction.atomic
def post_pos_session(session_id, user):
    """Post one consolidated journal for all unposted sales in a closed register."""
    from pos.models import POSPayment, POSSale, POSSession

    session = POSSession.objects.select_for_update().get(pk=session_id)
    if session.status != POSSession.SessionStatus.CLOSED:
        raise ValidationError(_("Only closed POS register sessions can be posted."))

    ensure_not_posted(JournalEntry.SourceType.POS_SESSION, session.id)

    # Sales posted by the old per-sale workflow must never be posted twice.
    legacy_posted_ids = JournalEntry.objects.filter(
        source_type=JournalEntry.SourceType.POS_SALE,
        source_id__isnull=False,
    ).values_list("source_id", flat=True)
    sales = list(
        POSSale.objects.select_for_update()
        .filter(session=session, status=POSSale.SaleStatus.COMPLETED)
        .exclude(pk__in=legacy_posted_ids)
        .prefetch_related("items", "payments")
        .order_by("id")
    )
    if not sales:
        return None

    revenue_account = get_posting_account("sales_revenue", Account.AccountType.REVENUE)

    payment_totals = {
        POSPayment.PaymentMethod.CASH: Decimal("0.000"),
        POSPayment.PaymentMethod.CARD: Decimal("0.000"),
        POSPayment.PaymentMethod.BANK: Decimal("0.000"),
    }
    credit_sales = []
    total_revenue = Decimal("0.000")
    total_tax = Decimal("0.000")
    total_cogs = Decimal("0.000")

    for sale in sales:
        payments = list(sale.payments.all())
        if not payments:
            raise ValidationError(_("A completed POS sale must contain at least one payment."))

        sale_tax = sum((item.tax_amount for item in sale.items.all()), Decimal("0.000"))
        sale_revenue = sale.total - sale_tax
        if sale_revenue < 0:
            raise ValidationError(_("POS sale tax cannot exceed the sale total."))

        remaining_change = sale.change_amount
        posted_total = Decimal("0.000")
        credit_total = Decimal("0.000")
        for payment in payments:
            amount = payment.amount
            if amount <= 0:
                raise ValidationError(_("POS payment amounts must be greater than zero."))
            if payment.payment_method == POSPayment.PaymentMethod.CASH and remaining_change > 0:
                cash_change = min(amount, remaining_change)
                amount -= cash_change
                remaining_change -= cash_change

            if payment.payment_method in payment_totals:
                payment_totals[payment.payment_method] += amount
            elif payment.payment_method == POSPayment.PaymentMethod.CREDIT:
                if sale.customer is None:
                    raise ValidationError(_("Credit sales require a customer."))
                credit_total += amount
            else:
                raise ValidationError(_("Unsupported payment method: %(method)s") % {
                    "method": payment.payment_method,
                })
            posted_total += amount

        if remaining_change > 0:
            raise ValidationError(_("Sale change exceeds the available cash payment."))
        if posted_total != sale.total:
            raise ValidationError(
                _("POS payment total does not match sale total. Payments: %(payments)s, Sale total: %(total)s")
                % {"payments": posted_total, "total": sale.total}
            )
        if credit_total:
            credit_sales.append((sale, credit_total))
        total_revenue += sale_revenue
        total_tax += sale_tax
        total_cogs += sum(
            (item.quantity * item.unit_cost for item in sale.items.all()),
            Decimal("0.000"),
        )

    journal = JournalEntry.objects.create(
        entry_number=f"POS-CLOSE-{session.id}",
        date=timezone.localtime(session.closed_at).date(),
        description=_("POS register session %(session)s") % {"session": session.session_number},
        source_type=JournalEntry.SourceType.POS_SESSION,
        source_id=session.id,
        cash_flow_activity=JournalEntry.CashFlowActivity.NONE,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    method_labels = {
        POSPayment.PaymentMethod.CASH: _("Cash"),
        POSPayment.PaymentMethod.CARD: _("Card"),
        POSPayment.PaymentMethod.BANK: _("Bank Transfer"),
    }
    method_account_codes = {
        POSPayment.PaymentMethod.CASH: "cash",
        POSPayment.PaymentMethod.CARD: "card_clearing",
        POSPayment.PaymentMethod.BANK: "bank",
    }
    for method, amount in payment_totals.items():
        if amount:
            JournalEntryLine.objects.create(
                journal_entry=journal,
                account=get_posting_account(method_account_codes[method], Account.AccountType.ASSET),
                description=_("%(method)s payments for register %(session)s") % {
                    "method": method_labels[method], "session": session.session_number,
                },
                debit=amount,
                credit=Decimal("0.000"),
            )

    for sale, amount in credit_sales:
        line = JournalEntryLine.objects.create(
            journal_entry=journal,
            account=get_posting_account("accounts_receivable", Account.AccountType.ASSET),
            customer=sale.customer,
            description=_("Credit sale %(sale)s") % {"sale": sale.sale_number},
            debit=amount,
            credit=Decimal("0.000"),
        )
        OpenItem.objects.create(
            item_type=OpenItem.ItemType.RECEIVABLE,
            customer=sale.customer,
            journal_line=line,
            document_number=sale.sale_number,
            document_date=sale.date.date(),
            due_date=sale.date.date(),
            original_amount=amount,
        )

    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=revenue_account,
        description=_("Sales revenue for register %(session)s") % {"session": session.session_number},
        debit=Decimal("0.000"),
        credit=total_revenue,
    )
    if total_tax:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=get_posting_account("sales_tax_payable", Account.AccountType.LIABILITY),
            description=_("Sales tax for register %(session)s") % {"session": session.session_number},
            debit=Decimal("0.000"),
            credit=total_tax,
        )
    if total_cogs:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=get_posting_account("cost_of_goods_sold", Account.AccountType.EXPENSE),
            description=_("COGS for register %(session)s") % {"session": session.session_number},
            debit=total_cogs,
            credit=Decimal("0.000"),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=get_posting_account("inventory", Account.AccountType.ASSET),
            description=_("Inventory reduction for register %(session)s") % {"session": session.session_number},
            debit=Decimal("0.000"),
            credit=total_cogs,
        )

    posted_journal = post_journal_entry(
        journal.id,
        user,
        automatic_activity=JournalEntry.CashFlowActivity.OPERATING,
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=session,
        changes={
            "journal_entry": {"before": None, "after": journal.entry_number},
            "sale_count": {"before": None, "after": len(sales)},
            "total": {"before": None, "after": str(sum((sale.total for sale in sales), Decimal("0.000")))},
        },
    )
    return posted_journal


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
        "sales_revenue",
        Account.AccountType.REVENUE,
    )
    cogs_account = get_posting_account(
        "cost_of_goods_sold",
        Account.AccountType.EXPENSE,
    )
    inventory_account = get_posting_account(
        "inventory",
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
        cash_flow_activity=JournalEntry.CashFlowActivity.NONE,
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
                "cash",
                Account.AccountType.ASSET,
            )

        elif payment.payment_method == POSPayment.PaymentMethod.CARD:
            debit_account = get_posting_account(
                "card_clearing",
                Account.AccountType.ASSET,
            )

        elif payment.payment_method == POSPayment.PaymentMethod.BANK:
            debit_account = get_posting_account(
                "bank",
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
                "accounts_receivable",
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

        payment_line = JournalEntryLine.objects.create(
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
        if payment.payment_method == POSPayment.PaymentMethod.CREDIT:
            OpenItem.objects.create(
                item_type=OpenItem.ItemType.RECEIVABLE,
                customer=sale.customer,
                journal_line=payment_line,
                document_number=sale.sale_number,
                document_date=sale.date.date(),
                due_date=sale.date.date(),
                original_amount=amount,
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
            "sales_tax_payable",
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

    post_journal_entry(
        journal.id,
        user,
        automatic_activity=JournalEntry.CashFlowActivity.OPERATING,
    )
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=sale,
        changes={
            "journal_entry": {"before": None, "after": journal.entry_number},
            "total": {"before": None, "after": str(sale.total)},
        },
    )

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
        "waste_loss",
        Account.AccountType.EXPENSE,
    )
    inventory_account = get_posting_account(
        "inventory",
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

    posted_journal = post_journal_entry(journal.id, user)
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=waste,
        changes={
            "journal_entry": {"before": None, "after": posted_journal.entry_number},
            "total_cost": {"before": None, "after": str(total_waste_cost)},
        },
    )
    return posted_journal


@transaction.atomic
def post_stock_adjustment(adjustment_id, user):
    """Post the inventory value gained or lost by a confirmed stock count."""
    from inventory.models import StockAdjustment, StockMovement

    adjustment = (
        StockAdjustment.objects.select_for_update()
        .select_related("warehouse")
        .get(pk=adjustment_id)
    )
    if adjustment.status != StockAdjustment.Status.CONFIRMED:
        raise ValidationError(
            _("Only confirmed stock adjustments can be posted to accounting.")
        )

    ensure_not_posted(JournalEntry.SourceType.STOCK_ADJUSTMENT, adjustment.id)
    movements = StockMovement.objects.filter(
        reference_type="StockAdjustment",
        reference_id=adjustment.id,
        movement_type__in=[
            StockMovement.MovementType.ADJUSTMENT_IN,
            StockMovement.MovementType.ADJUSTMENT_OUT,
        ],
    )
    shortage_value = sum(
        (-movement.quantity * movement.unit_cost for movement in movements if movement.quantity < 0),
        Decimal("0.000"),
    )
    surplus_value = sum(
        (movement.quantity * movement.unit_cost for movement in movements if movement.quantity > 0),
        Decimal("0.000"),
    )

    # A count with no variance changes no inventory value and needs no journal.
    if shortage_value == 0 and surplus_value == 0:
        return None

    inventory_account = get_posting_account("inventory", Account.AccountType.ASSET)
    loss_account = (
        get_posting_account("inventory_adjustment_loss", Account.AccountType.EXPENSE)
        if shortage_value > 0
        else None
    )
    gain_account = (
        get_posting_account("inventory_adjustment_gain", Account.AccountType.REVENUE)
        if surplus_value > 0
        else None
    )
    journal = JournalEntry.objects.create(
        entry_number=f"ADJ-{adjustment.adjustment_number}",
        date=adjustment.date,
        description=_("Stock count adjustment %(number)s") % {
            "number": adjustment.adjustment_number,
        },
        source_type=JournalEntry.SourceType.STOCK_ADJUSTMENT,
        source_id=adjustment.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    if shortage_value > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=loss_account,
            description=_("Inventory shortage for stock count %(number)s") % {
                "number": adjustment.adjustment_number,
            },
            debit=shortage_value,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory_account,
            description=_("Inventory decrease for stock count %(number)s") % {
                "number": adjustment.adjustment_number,
            },
            credit=shortage_value,
        )

    if surplus_value > 0:
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=inventory_account,
            description=_("Inventory increase for stock count %(number)s") % {
                "number": adjustment.adjustment_number,
            },
            debit=surplus_value,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=gain_account,
            description=_("Inventory gain for stock count %(number)s") % {
                "number": adjustment.adjustment_number,
            },
            credit=surplus_value,
        )

    posted_journal = post_journal_entry(journal.id, user)
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.POSTED,
        instance=adjustment,
        changes={
            "journal_entry": {"before": None, "after": posted_journal.entry_number},
            "shortage_value": {"before": None, "after": str(shortage_value)},
            "surplus_value": {"before": None, "after": str(surplus_value)},
        },
    )
    return posted_journal


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

    if OpenItemAllocation.objects.filter(
        open_item__journal_line__journal_entry=original,
    ).exists():
        raise ValidationError(
            _(
                "This document has settlements applied to it. Reverse those "
                "receipts or payments first."
            )
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
        cash_flow_activity=original.cash_flow_activity,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
        reversal_of=original,
    )

    JournalEntryLine.objects.bulk_create(
        [
            JournalEntryLine(
                journal_entry=reversal,
                account=line.account,
                customer=line.customer,
                supplier=line.supplier,
                description=line.description,
                debit=line.credit,
                credit=line.debit,
            )
            for line in original.lines.all()
        ]
    )

    reversal = post_journal_entry(reversal.pk, user)

    # Reversing a settlement reopens the invoices it had paid. Reversing an
    # unsettled invoice removes its operational open item; the journals remain
    # as the permanent accounting audit trail.
    OpenItemAllocation.objects.filter(
        journal_line__journal_entry=original,
    ).delete()
    OpenItem.objects.filter(
        journal_line__journal_entry=original,
    ).delete()

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
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.REVERSED,
        instance=original,
        changes={
            "status": {"before": JournalEntry.Status.POSTED, "after": original.status},
            "reason": {"before": None, "after": reason},
            "reversal_entry": {"before": None, "after": reversal.entry_number},
        },
    )

    return reversal


@transaction.atomic
def create_fiscal_year(year, notes="", user=None):
    if FiscalYear.objects.filter(year=year).exists():
        raise ValidationError(_("This fiscal year already exists."))

    fiscal_year = FiscalYear.objects.create(year=year, notes=(notes or "").strip())

    periods = []
    for month in range(1, 13):
        last_day = calendar.monthrange(year, month)[1]
        periods.append(
            FiscalPeriod(
                fiscal_year=fiscal_year,
                month=month,
                start_date=date(year, month, 1),
                end_date=date(year, month, last_day),
            )
        )

    FiscalPeriod.objects.bulk_create(periods)
    record_finance_audit(
        actor=user,
        action=FinanceAuditLog.Action.CREATED,
        instance=fiscal_year,
        changes={
            "year": {"before": None, "after": year},
            "periods_created": {"before": 0, "after": 12},
        },
    )
    return fiscal_year


def get_unfinished_document_counts(start_date, end_date):
    """Return draft financial source documents in an inclusive date range."""
    from pos.models import POSSale
    from purchasing.models import PurchaseInvoice
    from sales.models import SalesInvoice

    date_range = (start_date, end_date)
    return {
        "journals": JournalEntry.objects.filter(
            date__range=date_range,
            status=JournalEntry.Status.DRAFT,
        ).count(),
        "receipts": ReceiptVoucher.objects.filter(
            date__range=date_range,
            status=ReceiptVoucher.Status.DRAFT,
        ).count(),
        "payments": PaymentVoucher.objects.filter(
            date__range=date_range,
            status=PaymentVoucher.Status.DRAFT,
        ).count(),
        "purchases": PurchaseInvoice.objects.filter(
            invoice_date__range=date_range,
            status=PurchaseInvoice.Status.DRAFT,
        ).count(),
        "sales": POSSale.objects.filter(
            date__date__range=date_range,
            status=POSSale.SaleStatus.DRAFT,
        ).count(),
        "sales_invoices": SalesInvoice.objects.filter(
            invoice_date__range=date_range,
            status=SalesInvoice.Status.DRAFT,
        ).count(),
    }


def get_period_summary(period):
    journals = JournalEntry.objects.filter(
        date__range=(period.start_date, period.end_date)
    )
    posted_journals = journals.filter(
        status__in=[JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED]
    )
    totals = JournalEntryLine.objects.filter(
        journal_entry__in=posted_journals
    ).aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))

    return {
        "posted_journals": posted_journals.count(),
        "draft_journals": journals.filter(status=JournalEntry.Status.DRAFT).count(),
        "total_debit": totals["total_debit"] or Decimal("0.000"),
        "total_credit": totals["total_credit"] or Decimal("0.000"),
        "unfinished": get_unfinished_document_counts(
            period.start_date,
            period.end_date,
        ),
    }


def ensure_date_range_can_close(start_date, end_date):
    unfinished = get_unfinished_document_counts(start_date, end_date)
    labels = {
        "journals": _("draft journals"),
        "receipts": _("draft receipts"),
        "payments": _("draft payments"),
        "purchases": _("draft purchases"),
        "sales": _("draft POS sales"),
        "sales_invoices": _("draft sales invoices"),
    }
    blockers = [
        _("%(count)s %(label)s") % {"count": count, "label": labels[key]}
        for key, count in unfinished.items()
        if count
    ]
    if blockers:
        raise ValidationError(
            _("This period cannot be closed while it contains: %(documents)s.")
            % {"documents": ", ".join(str(item) for item in blockers)}
        )


def normalize_status_reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("A reason is required for this status change."))
    return reason


def ensure_posting_period_open(posting_date, *, lock=True):
    fiscal_year_query = FiscalYear.objects
    if lock:
        fiscal_year_query = fiscal_year_query.select_for_update()
    fiscal_year = fiscal_year_query.filter(year=posting_date.year).first()

    if fiscal_year is None:
        raise ValidationError(
            _("No fiscal year has been configured for this date.")
        )

    if fiscal_year.status == PeriodStatus.CLOSED:
        raise ValidationError(_("The fiscal year is closed."))

    period_query = FiscalPeriod.objects
    if lock:
        period_query = period_query.select_for_update()
    period = period_query.filter(
        fiscal_year=fiscal_year,
        month=posting_date.month,
        start_date__lte=posting_date,
        end_date__gte=posting_date,
    ).first()

    if period is None:
        raise ValidationError(
            _("No accounting period exists for this date.")
        )

    if period.status == PeriodStatus.CLOSED:
        raise ValidationError(
            _("The accounting period for this date is closed.")
        )


def ensure_user_can_reopen(user):
    is_admin = user.is_superuser or getattr(user, "role", None) == "admin"

    if not is_admin:
        raise PermissionDenied(
            _("Only administrators can reopen accounting periods.")
        )


@transaction.atomic
def set_period_status(period_id, status, user, reason=""):
    period_year_id = FiscalPeriod.objects.values_list(
        "fiscal_year_id", flat=True
    ).get(pk=period_id)
    fiscal_year = FiscalYear.objects.select_for_update().get(pk=period_year_id)
    period = FiscalPeriod.objects.select_for_update().get(pk=period_id)

    if status not in PeriodStatus.values:
        raise ValidationError(_("Invalid accounting-period status."))

    if period.status == status:
        return period

    if status == PeriodStatus.OPEN:
        ensure_user_can_reopen(user)

    if (
        status == PeriodStatus.OPEN
        and fiscal_year.status == PeriodStatus.CLOSED
    ):
        raise ValidationError(
            _("Open the fiscal year before reopening one of its periods.")
        )

    reason = normalize_status_reason(reason)

    if status == PeriodStatus.CLOSED:
        ensure_date_range_can_close(period.start_date, period.end_date)

    period.status = status
    if status == PeriodStatus.CLOSED:
        period.closed_by = user
        period.closed_at = timezone.now()
        period.close_reason = reason
    else:
        period.closed_by = None
        period.closed_at = None
        period.close_reason = ""

    period.save(
        update_fields=("status", "closed_by", "closed_at", "close_reason")
    )
    FiscalPeriodAction.objects.create(
        fiscal_year=fiscal_year,
        period=period,
        action=(
            FiscalPeriodAction.Action.CLOSED
            if status == PeriodStatus.CLOSED
            else FiscalPeriodAction.Action.OPENED
        ),
        performed_by=user,
        reason=reason,
    )
    record_finance_audit(
        actor=user,
        action=(
            FinanceAuditLog.Action.CLOSED
            if status == PeriodStatus.CLOSED
            else FinanceAuditLog.Action.REOPENED
        ),
        instance=period,
        changes={
            "status": {
                "before": (
                    PeriodStatus.OPEN if status == PeriodStatus.CLOSED else PeriodStatus.CLOSED
                ),
                "after": status,
            },
            "reason": {"before": None, "after": reason},
        },
    )
    return period


@transaction.atomic
def set_fiscal_year_status(fiscal_year_id, status, user, reason=""):
    fiscal_year = FiscalYear.objects.select_for_update().get(
        pk=fiscal_year_id
    )

    if status not in PeriodStatus.values:
        raise ValidationError(_("Invalid fiscal-year status."))

    if fiscal_year.status == status:
        return fiscal_year

    if status == PeriodStatus.OPEN:
        ensure_user_can_reopen(user)

    reason = normalize_status_reason(reason)

    if status == PeriodStatus.CLOSED:
        ensure_date_range_can_close(
            date(fiscal_year.year, 1, 1),
            date(fiscal_year.year, 12, 31),
        )

    fiscal_year.status = status
    if status == PeriodStatus.CLOSED:
        fiscal_year.closed_by = user
        fiscal_year.closed_at = timezone.now()
        fiscal_year.close_reason = reason

        periods = list(
            FiscalPeriod.objects.select_for_update().filter(
                fiscal_year=fiscal_year
            )
        )
        FiscalPeriod.objects.filter(pk__in=[period.pk for period in periods]).update(
            status=PeriodStatus.CLOSED,
            closed_by=user,
            closed_at=timezone.now(),
            close_reason=reason,
        )
        FiscalPeriodAction.objects.bulk_create(
            [
                FiscalPeriodAction(
                    fiscal_year=fiscal_year,
                    period=period,
                    action=FiscalPeriodAction.Action.CLOSED,
                    performed_by=user,
                    reason=reason,
                )
                for period in periods
                if period.status != PeriodStatus.CLOSED
            ]
        )
    else:
        fiscal_year.closed_by = None
        fiscal_year.closed_at = None
        fiscal_year.close_reason = ""

    fiscal_year.save(
        update_fields=("status", "closed_by", "closed_at", "close_reason")
    )
    FiscalPeriodAction.objects.create(
        fiscal_year=fiscal_year,
        action=(
            FiscalPeriodAction.Action.CLOSED
            if status == PeriodStatus.CLOSED
            else FiscalPeriodAction.Action.OPENED
        ),
        performed_by=user,
        reason=reason,
    )
    record_finance_audit(
        actor=user,
        action=(
            FinanceAuditLog.Action.CLOSED
            if status == PeriodStatus.CLOSED
            else FinanceAuditLog.Action.REOPENED
        ),
        instance=fiscal_year,
        changes={
            "status": {
                "before": (
                    PeriodStatus.OPEN if status == PeriodStatus.CLOSED else PeriodStatus.CLOSED
                ),
                "after": status,
            },
            "reason": {"before": None, "after": reason},
        },
    )
    return fiscal_year
