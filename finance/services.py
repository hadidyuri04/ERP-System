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
    from pos.models import POSSale, POSPayment

    sale = (
        POSSale.objects
        .prefetch_related("items", "payments")
        .select_related("customer")
        .get(pk=sale_id)
    )

    # Only completed sales can reach accounting
    if sale.status != POSSale.SaleStatus.COMPLETED:
        raise ValidationError(
            _("Only completed POS sales can be posted.")
        )

    # Prevent posting the same sale twice
    entry_number = f"POS-{sale.sale_number}"

    if JournalEntry.objects.filter(
        entry_number=entry_number
    ).exists():
        raise ValidationError(
            _("This POS sale has already been posted to accounting.")
        )

    # Accounting accounts
    cash_account = Account.objects.get(code="1100")
    bank_account = Account.objects.get(code="1200")
    card_account = Account.objects.get(code="1210")
    receivable_account = Account.objects.get(code="1300")

    sales_revenue_account = Account.objects.get(code="4100")
    cogs_account = Account.objects.get(code="5100")
    inventory_account = Account.objects.get(code="1400")

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
            debit_account = cash_account

        elif payment.payment_method == POSPayment.PaymentMethod.CARD:
            debit_account = card_account

        elif payment.payment_method == POSPayment.PaymentMethod.BANK:
            debit_account = bank_account

        elif payment.payment_method == POSPayment.PaymentMethod.CREDIT:
            if sale.customer is None:
                raise ValidationError(
                    _(
                        "Credit sales require a customer."
                    )
                )

            debit_account = receivable_account

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
        credit=sale.total,
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

    post_journal_entry(journal.id)

    return journal

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