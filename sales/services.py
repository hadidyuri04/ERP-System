import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance.models import ReceiptVoucher
from finance.services import (
    ensure_posting_period_open,
    post_receipt_voucher,
    post_sales_credit_note,
    post_sales_invoice,
    validate_cash_bank_account,
)
from inventory.models import StockMovement
from inventory.services import remove_stock, restore_stock_to_batch
from quotations.models import Quotation

from .models import (
    SalesCreditNote,
    SalesInvoice,
    SalesInvoiceItem,
    SalesInvoiceStockAllocation,
)


def generate_invoice_number():
    return f"SI-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def generate_credit_note_number():
    return f"SCN-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def create_invoice_from_quotation(
    quotation_id, warehouse, invoice_date, due_date, payment_type, user,
    payment_account=None,
):
    """
    Turn a quotation into a draft sales invoice.

    Eligibility is checked outside the transaction on purpose. Marking a
    quotation expired and then raising inside an atomic block rolls the marking
    back with the exception, so the quotation stays DRAFT and fails again
    identically every time it is tried.
    """
    quotation = Quotation.objects.get(pk=quotation_id)

    if quotation.status in {
        Quotation.Status.REJECTED,
        Quotation.Status.EXPIRED,
        Quotation.Status.CONVERTED,
    } or hasattr(quotation, "sales_invoice"):
        raise ValidationError(_("This quotation cannot be converted again."))
    if quotation.expiry_date < timezone.localdate():
        quotation.status = Quotation.Status.EXPIRED
        quotation.save(update_fields=["status"])
        raise ValidationError(_("This quotation has expired and cannot be converted."))
    if due_date < invoice_date:
        raise ValidationError(_("Due date cannot precede invoice date."))
    if payment_type == SalesInvoice.PaymentType.CASH:
        if payment_account is None:
            raise ValidationError(_("Select a cash or bank account for a cash invoice."))
        validate_cash_bank_account(payment_account)
        due_date = invoice_date

    return _build_invoice_from_quotation(
        quotation_id, warehouse, invoice_date, due_date, payment_type, user,
        payment_account,
    )


@transaction.atomic
def _build_invoice_from_quotation(
    quotation_id, warehouse, invoice_date, due_date, payment_type, user,
    payment_account,
):
    quotation = Quotation.objects.select_for_update().prefetch_related(
        "items__product"
    ).get(pk=quotation_id)

    invoice = SalesInvoice.objects.create(
        invoice_number=generate_invoice_number(),
        quotation=quotation,
        customer=quotation.customer,
        warehouse=warehouse,
        invoice_date=invoice_date,
        due_date=due_date,
        payment_type=payment_type,
        payment_account=payment_account if payment_type == SalesInvoice.PaymentType.CASH else None,
        subtotal=quotation.subtotal,
        discount_amount=quotation.discount_amount,
        tax_amount=quotation.tax_amount,
        total=quotation.total,
        notes=_("Converted from quotation %(number)s") % {
            "number": quotation.quotation_number,
        },
        created_by=user,
    )
    SalesInvoiceItem.objects.bulk_create([
        SalesInvoiceItem(
            invoice=invoice,
            product=item.product,
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount_amount=item.discount_amount,
            tax_amount=item.tax_amount,
            line_total=item.line_total,
        )
        for item in quotation.items.all()
    ])
    quotation.status = Quotation.Status.CONVERTED
    quotation.save(update_fields=["status"])
    return invoice


@transaction.atomic
def confirm_sales_invoice(invoice_id, user):
    invoice = (
        SalesInvoice.objects.select_for_update()
        .select_related("customer", "warehouse")
        .prefetch_related("items__product")
        .get(pk=invoice_id)
    )
    if invoice.status != SalesInvoice.Status.DRAFT:
        raise ValidationError(_("Only draft sales invoices can be confirmed."))
    items = list(invoice.items.all())
    if not items:
        raise ValidationError(_("Sales invoice must contain at least one item."))
    ensure_posting_period_open(invoice.invoice_date)

    for item in items:
        if item.quantity <= 0:
            raise ValidationError(_("Invoice item quantity must be greater than zero."))
        if not item.product.is_sellable:
            raise ValidationError(_("%(product)s is not available for sale.") % {
                "product": item.product.name,
            })
        total_cost = remove_stock(
            product=item.product,
            warehouse=invoice.warehouse,
            quantity=item.quantity,
            reference_type="SALES_INVOICE_ITEM",
            reference_id=item.id,
            user=user,
            movement_type=StockMovement.MovementType.SALE,
        )
        item.unit_cost = total_cost / item.quantity
        item.save(update_fields=["unit_cost"])
        movements = StockMovement.objects.filter(
            reference_type="SALES_INVOICE_ITEM",
            reference_id=item.id,
            movement_type=StockMovement.MovementType.SALE,
        ).select_related("batch")
        SalesInvoiceStockAllocation.objects.bulk_create([
            SalesInvoiceStockAllocation(
                invoice_item=item,
                batch=movement.batch,
                quantity=-movement.quantity,
                unit_cost=movement.unit_cost,
            )
            for movement in movements
        ])

    invoice.status = SalesInvoice.Status.POSTED
    invoice.posted_at = timezone.now()
    invoice.save(update_fields=["status", "posted_at", "updated_at"])
    journal = post_sales_invoice(invoice.id, user)

    receipt = None
    if invoice.payment_type == SalesInvoice.PaymentType.CASH:
        if invoice.payment_account is None:
            raise ValidationError(_("A cash invoice requires a cash or bank account."))
        validate_cash_bank_account(invoice.payment_account)
        receipt = ReceiptVoucher.objects.create(
            voucher_number=f"SI-RV-{invoice.id}",
            date=invoice.invoice_date,
            customer=invoice.customer,
            received_from=invoice.customer.name,
            account=invoice.payment_account,
            amount=invoice.total,
            payment_method=ReceiptVoucher.PaymentMethod.CASH,
            reference=invoice.invoice_number,
            description=_("Immediate payment for sales invoice %(number)s") % {
                "number": invoice.invoice_number,
            },
            created_by=user,
        )
        post_receipt_voucher(
            receipt.id,
            user,
            target_open_item_id=invoice.open_item.id,
        )
    return invoice, journal, receipt


@transaction.atomic
def record_invoice_payment(
    invoice_id, user, *, payment_date, account, amount, payment_method, reference="",
):
    invoice = SalesInvoice.objects.select_for_update().select_related("customer").get(pk=invoice_id)
    if invoice.status != SalesInvoice.Status.POSTED:
        raise ValidationError(_("Payments can only be recorded for posted sales invoices."))
    if payment_date < invoice.invoice_date:
        raise ValidationError(_("Payment date cannot precede the invoice date."))
    amount = Decimal(str(amount))
    if amount <= 0 or amount > invoice.outstanding_amount:
        raise ValidationError(_("Payment must be positive and cannot exceed the outstanding balance."))
    validate_cash_bank_account(account)
    open_item = invoice.open_item
    if open_item is None:
        raise ValidationError(_("The invoice receivable open item could not be found."))
    receipt = ReceiptVoucher.objects.create(
        voucher_number=f"SI-{invoice.id}-{uuid.uuid4().hex[:8].upper()}",
        date=payment_date,
        customer=invoice.customer,
        received_from=invoice.customer.name,
        account=account,
        amount=amount,
        payment_method=payment_method,
        reference=reference,
        description=_("Payment for sales invoice %(number)s") % {
            "number": invoice.invoice_number,
        },
        created_by=user,
    )
    post_receipt_voucher(receipt.id, user, target_open_item_id=open_item.id)
    return receipt


@transaction.atomic
def cancel_draft_invoice(invoice_id, user, reason):
    invoice = SalesInvoice.objects.select_for_update().get(pk=invoice_id)
    if invoice.status != SalesInvoice.Status.DRAFT:
        raise ValidationError(_("Only draft invoices can be cancelled directly. Use a credit note for a posted invoice."))
    if not (reason or "").strip():
        raise ValidationError(_("A cancellation reason is required."))
    invoice.status = SalesInvoice.Status.CANCELLED
    invoice.notes = "\n".join(filter(None, [invoice.notes, _("Cancelled: %(reason)s") % {"reason": reason.strip()}]))
    invoice.save(update_fields=["status", "notes", "updated_at"])
    return invoice


@transaction.atomic
def create_and_post_full_credit_note(invoice_id, user, *, note_date, reason):
    invoice = SalesInvoice.objects.select_for_update().prefetch_related(
        "items__stock_allocations"
    ).get(pk=invoice_id)
    if invoice.status != SalesInvoice.Status.POSTED:
        raise ValidationError(_("Only posted invoices can be credited."))
    if invoice.paid_amount > 0:
        raise ValidationError(_("This invoice has payments. Reverse or refund them before issuing a full credit note."))
    if not (reason or "").strip():
        raise ValidationError(_("A credit-note reason is required."))
    ensure_posting_period_open(note_date)
    note = SalesCreditNote.objects.create(
        credit_note_number=generate_credit_note_number(),
        invoice=invoice,
        date=note_date,
        reason=reason.strip(),
        status=SalesCreditNote.Status.POSTED,
        created_by=user,
        posted_at=timezone.now(),
    )
    for item in invoice.items.all():
        for allocation in item.stock_allocations.all():
            restore_stock_to_batch(
                allocation.batch_id,
                allocation.quantity,
                "SALES_CREDIT_NOTE",
                note.id,
                user,
            )
    journal = post_sales_credit_note(note.id, user)
    invoice.status = SalesInvoice.Status.CREDITED
    invoice.save(update_fields=["status", "updated_at"])
    return note, journal
