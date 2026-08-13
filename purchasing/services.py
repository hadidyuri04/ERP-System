from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from finance.services import post_purchase_invoice
from inventory.models import StockBatch, StockBalance, StockMovement

from .models import PurchaseInvoice

@transaction.atomic
def confirm_purchase(purchase_id, user):
    purchase = (
        PurchaseInvoice.objects
        .select_for_update()
        .select_related("supplier", "warehouse")
        .get(pk=purchase_id)
    )

    # 1. Only draft purchases can be confirmed
    if purchase.status != PurchaseInvoice.Status.DRAFT:
        raise ValidationError(
            _("Only draft purchase invoices can be confirmed.")
        )

    items = list(
        purchase.items.select_related("product").all()
    )

    # 2. Invoice must contain at least one item
    if not items:
        raise ValidationError(
            _("Purchase invoice must contain at least one item.")
        )

    # 3. Process every purchased item
    for item in items:

        if item.quantity <= 0:
            raise ValidationError(
                _("Quantity for %(product)s must be greater than zero.") % {
                    "product": item.product.name,
                }
            )

        if item.unit_cost < 0:
            raise ValidationError(
                _("Unit cost for %(product)s cannot be negative.") % {
                    "product": item.product.name,
                }
            )

        # 4. Create the stock batch
        batch = StockBatch.objects.create(
            product=item.product,
            warehouse=purchase.warehouse,
            batch_number=item.batch_number,
            expiration_date=item.expiration_date,
            received_date=purchase.invoice_date,
            unit_cost=item.unit_cost,
            quantity_received=item.quantity,
            quantity_remaining=item.quantity,
            supplier=purchase.supplier,
            purchase_item=item,
        )

        # 5. Create inventory movement
        StockMovement.objects.create(
            product=item.product,
            warehouse=purchase.warehouse,
            batch=batch,
            movement_type=StockMovement.MovementType.PURCHASE,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            reference_type="purchase",
            reference_id=purchase.id,
            created_by=user,
        )

        # 6. Update the current stock balance
        stock_balance, created = StockBalance.objects.select_for_update().get_or_create(
            product=item.product,
            warehouse=purchase.warehouse,
            defaults={
                "quantity": Decimal("0.000"),
                "reserved_quantity": Decimal("0.000"),
            },
        )

        stock_balance.quantity += item.quantity
        stock_balance.save(
            update_fields=[
                "quantity",
                "updated_at",
            ]
        )

    # Confirm before posting because finance accepts confirmed purchases only.
    purchase.status = PurchaseInvoice.Status.CONFIRMED

    purchase.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    post_purchase_invoice(purchase.id, user)

    return purchase
