from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
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

@transaction.atomic
def post_purchase_invoice(invoice_id, user):
    """
    Creates and posts a journal entry for a confirmed Purchase Invoice:
    Debit: Inventory (Account 1400)
    Credit: Accounts Payable (Account 2100) or Cash
    """
    from purchasing.models import PurchaseInvoice
    invoice = PurchaseInvoice.objects.select_related('supplier', 'warehouse').get(pk=invoice_id)

    if invoice.status != PurchaseInvoice.Status.CONFIRMED:
        raise ValidationError(_("Only confirmed purchase invoices can be posted to accounting."))

    inventory_account = Account.objects.get(code="1400")
    payable_account = Account.objects.get(code="2100") # Or cash depending on payment type

    journal = JournalEntry.objects.create(
        entry_number=f"PI-{invoice.invoice_number}",
        date=invoice.invoice_date,
        description=_("Purchase Invoice %(invoice_number)s from %(supplier_name)s") % {
            'invoice_number': invoice.invoice_number,
            'supplier_name': invoice.supplier.name
        },
        source_type=JournalEntry.SourceType.PURCHASE,
        source_id=invoice.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Debit Inventory
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=inventory_account,
        description=_("Stock purchase from %(supplier_name)s") % {'supplier_name': invoice.supplier.name},
        debit=invoice.subtotal, # Adjust if including tax/discounts per design rules
        credit=Decimal("0.000"),
    )

    # Credit Accounts Payable
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=payable_account,
        supplier=invoice.supplier,
        description=_("Payable for purchase invoice %(invoice_number)s") % {'invoice_number': invoice.invoice_number},
        debit=Decimal("0.000"),
        credit=invoice.total,
    )

    return post_journal_entry(journal.id)


@transaction.atomic
def post_pos_sale(sale_id, user):
    """
    Creates and posts journal entries for a completed POS Sale:
    1. Revenue Entry: Debit Cash/Receivable, Credit Sales Revenue
    2. COGS Entry: Debit Cost of Goods Sold, Credit Inventory
    """
    from pos.models import POSSale
    sale = POSSale.objects.prefetch_related('items', 'payments').get(pk=sale_id)

    if sale.status != POSSale.Status.COMPLETED:
        raise ValidationError(_("Only completed POS sales can be posted."))

    cash_account = Account.objects.get(code="1100") # Assuming Cash/Main Account
    sales_revenue_account = Account.objects.get(code="4100")
    cogs_account = Account.objects.get(code="5100")
    inventory_account = Account.objects.get(code="1400")

    # Calculate total cost of goods sold (COGS) from items
    total_cogs = sum((item.quantity * item.unit_cost for item in sale.items.all()), Decimal("0.000"))

    # 1. Sales Revenue Journal Entry
    journal_sale = JournalEntry.objects.create(
        entry_number=f"POS-{sale.sale_number}",
        date=sale.date.date(),
        description=_("POS Sale %(sale_number)s") % {'sale_number': sale.sale_number},
        source_type=JournalEntry.SourceType.POS_SALE,
        source_id=sale.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    JournalEntryLine.objects.create(
        journal_entry=journal_sale,
        account=cash_account,
        description=_("Cash received for sale %(sale_number)s") % {'sale_number': sale.sale_number},
        debit=sale.total,
        credit=Decimal("0.000"),
    )

    JournalEntryLine.objects.create(
        journal_entry=journal_sale,
        account=sales_revenue_account,
        description=_("Sales revenue for %(sale_number)s") % {'sale_number': sale.sale_number},
        debit=Decimal("0.000"),
        credit=sale.total,
    )
    post_journal_entry(journal_sale.id)

    # 2. COGS Journal Entry (if items have cost)
    if total_cogs > 0:
        journal_cogs = JournalEntry.objects.create(
            entry_number=f"COGS-{sale.sale_number}",
            date=sale.date.date(),
            description=_("Cost of Goods Sold for POS Sale %(sale_number)s") % {'sale_number': sale.sale_number},
            source_type=JournalEntry.SourceType.POS_SALE,
            source_id=sale.id,
            status=JournalEntry.Status.DRAFT,
            created_by=user,
        )

        JournalEntryLine.objects.create(
            journal_entry=journal_cogs,
            account=cogs_account,
            description=_("COGS for sale %(sale_number)s") % {'sale_number': sale.sale_number},
            debit=total_cogs,
            credit=Decimal("0.000"),
        )

        JournalEntryLine.objects.create(
            journal_entry=journal_cogs,
            account=inventory_account,
            description=_("Inventory reduction for sale %(sale_number)s") % {'sale_number': sale.sale_number},
            debit=Decimal("0.000"),
            credit=total_cogs,
        )
        post_journal_entry(journal_cogs.id)

    return sale


@transaction.atomic
def post_waste_loss(waste_id, user):
    """
    Creates and posts a journal entry for confirmed Waste & Loss:
    Debit: Waste & Loss Expense (Account 6300)
    Credit: Inventory (Account 1400)
    """
    from inventory.models import WasteLoss
    waste = WasteLoss.objects.prefetch_related('items').get(pk=waste_id)

    if waste.status != WasteLoss.Status.CONFIRMED:
        raise ValidationError(_("Only confirmed waste and loss documents can be posted."))

    waste_account = Account.objects.get(code="6300")
    inventory_account = Account.objects.get(code="1400")

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

    return post_journal_entry(journal.id)