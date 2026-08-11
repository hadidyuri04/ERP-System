from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from inventory.services import add_stock
from .models import PurchaseInvoice

@transaction.atomic
def confirm_purchase(purchase_invoice_id, user):
    """
    Confirms a purchase invoice:
    1. Updates invoice status.
    2. Generates stock batches and movements via inventory service.
    3. Updates supplier balances & creates financial journal entries.
    """
    invoice = PurchaseInvoice.objects.select_for_update().get(pk=purchase_invoice_id)
    
    if invoice.status == PurchaseInvoice.Status.CONFIRMED:
        raise ValidationError(_("This purchase invoice is already confirmed."))
    if invoice.status == PurchaseInvoice.Status.CANCELLED:
        raise ValidationError(_("Cannot confirm a cancelled purchase invoice."))

    invoice.status = PurchaseInvoice.Status.CONFIRMED
    invoice.save()

    # Loop through invoice items and inject stock
    for item in invoice.items.all():
        add_stock(
            product=item.product,
            warehouse=invoice.warehouse,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            reference_type='PURCHASE_INVOICE',
            reference_id=invoice.id,
            user=user,
            batch_number=item.batch_number,
            expiration_date=item.expiration_date,
            supplier=invoice.supplier,
            purchase_item=item
        )

    # TODO: Trigger finance service to post Journal Entry (Inventory Dr, Accounts Payable/Cash Cr)
    return invoice