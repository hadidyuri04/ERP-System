from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import StockBatch, StockBalance, StockMovement, WarehouseTransfer, WasteLoss

def get_available_stock(product, warehouse):
    """Returns current available stock for fast lookup via StockBalance."""
    balance, created = StockBalance.objects.get_or_create(
        product=product, 
        warehouse=warehouse, 
        defaults={'quantity': Decimal('0.000')}
    )
    return balance.quantity

def select_fefo_batch(product, warehouse, required_quantity):
    """
    Selects active, non-expired batches using FEFO (First Expired, First Out).
    Returns a list of tuples: [(batch, quantity_to_take_from_batch), ...]
    """
    active_batches = StockBatch.objects.filter(
        product=product,
        warehouse=warehouse,
        status=StockBatch.Status.ACTIVE,
        quantity_remaining__gt=0
    ).order_by('expiration_date', 'received_date')

    allocations = []
    remaining_needed = required_quantity

    for batch in active_batches:
        if batch.expiration_date and batch.expiration_date < timezone.now().date():
            # Skip expired batches from sales selection
            continue

        take_qty = min(batch.quantity_remaining, remaining_needed)
        allocations.append((batch, take_qty))
        remaining_needed -= take_qty

        if remaining_needed <= 0:
            break

    if remaining_needed > 0:
        raise ValidationError(_("Insufficient unexpired stock for product %(product)s. Short by %(short)s.") % {
            'product': product.name,
            'short': remaining_needed
        })

    return allocations

@transaction.atomic
def add_stock(product, warehouse, quantity, unit_cost, reference_type, reference_id, user, batch_number=None, expiration_date=None, supplier=None, purchase_item=None):
    """Adds stock to a warehouse, creating or updating a StockBatch and updating balances."""
    if quantity <= 0:
        raise ValidationError(_("Stock addition quantity must be greater than zero."))

    # Create batch if tracking expiration or explicitly given
    batch = None
    if product.track_expiration or batch_number:
        batch = StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number=batch_number or f"BATCH-{timezone.now().strftime('%Y%m%d%H%M%S')}",
            expiration_date=expiration_date,
            received_date=timezone.now().date(),
            unit_cost=unit_cost,
            quantity_received=quantity,
            quantity_remaining=quantity,
            supplier=supplier,
            purchase_item=purchase_item,
            status=StockBatch.Status.ACTIVE
        )

    # Create audit trail movement
    StockMovement.objects.create(
        product=product,
        warehouse=warehouse,
        batch=batch,
        movement_type=StockMovement.MovementType.PURCHASE, # Can be adapted based on reference_type
        quantity=quantity,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=user
    )

    # Update fast stock balance
    balance, _ = StockBalance.objects.select_for_update().get_or_create(
        product=product, 
        warehouse=warehouse, 
        defaults={'quantity': Decimal('0.000')}
    )
    balance.quantity += quantity
    balance.save()

    return batch

@transaction.atomic
def remove_stock(product, warehouse, quantity, reference_type, reference_id, user, movement_type=StockMovement.MovementType.SALE):
    """Removes stock using FEFO batch allocations and updates balances safely."""
    if quantity <= 0:
        raise ValidationError(_("Stock removal quantity must be greater than zero."))

    current_stock = get_available_stock(product, warehouse)
    if current_stock < quantity:
        raise ValidationError(_("Negative stock rule enforced: Cannot remove %(qty)s of %(product)s. Available: %(available)s.") % {
            'qty': quantity,
            'product': product.name,
            'available': current_stock
        })

    # Fetch batches using FEFO
    allocations = select_fefo_batch(product, warehouse, quantity)

    for batch, qty_to_take in allocations:
        batch.quantity_remaining -= qty_to_take
        if batch.quantity_remaining == 0:
            batch.status = StockBatch.Status.DEPLETED
        batch.save()

        # Create movement record per batch line
        StockMovement.objects.create(
            product=product,
            warehouse=warehouse,
            batch=batch,
            movement_type=movement_type,
            quantity=-qty_to_take,
            unit_cost=batch.unit_cost,
            reference_type=reference_type,
            reference_id=reference_id,
            created_by=user
        )

    # Update overall warehouse stock balance
    balance = StockBalance.objects.select_for_update().get(product=product, warehouse=warehouse)
    balance.quantity -= quantity
    balance.save()

@transaction.atomic
def confirm_warehouse_transfer(transfer_id, user):
    """
    Confirms a warehouse transfer:
    1. Removes stock from the source warehouse.
    2. Adds stock to the destination warehouse.
    3. Total company stock remains unchanged.
    """
    from .models import WarehouseTransfer
    transfer = WarehouseTransfer.objects.prefetch_related('items').select_for_update().get(pk=transfer_id)

    if transfer.status != WarehouseTransfer.Status.DRAFT:
        raise ValidationError(_("Only draft warehouse transfers can be confirmed."))

    if transfer.from_warehouse == transfer.to_warehouse:
        raise ValidationError(_("Source and destination warehouses cannot be the same."))

    for item in transfer.items.all():
        # Remove stock from source warehouse (using FEFO/batch tracking)
        remove_stock(
            product=item.product,
            warehouse=transfer.from_warehouse,
            quantity=item.quantity,
            reference_type='WAREHOUSE_TRANSFER',
            reference_id=transfer.id,
            user=user,
            movement_type='TRANSFER_OUT'
        )

        # Add stock to destination warehouse
        add_stock(
            product=item.product,
            warehouse=transfer.to_warehouse,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            reference_type='WAREHOUSE_TRANSFER',
            reference_id=transfer.id,
            user=user,
            batch_number=item.batch.batch_number if item.batch else None,
            expiration_date=item.batch.expiration_date if item.batch else None
        )

    transfer.status = WarehouseTransfer.Status.CONFIRMED
    transfer.approved_by = user
    transfer.save(update_fields=['status', 'approved_by'])
    return transfer


@transaction.atomic
def confirm_waste_loss(waste_id, user):
    """
    Confirms a waste & loss document:
    1. Removes expired or damaged stock from available inventory.
    2. Triggers financial waste posting.
    """
    from .models import WasteLoss
    from finance.services import post_waste_loss

    waste = WasteLoss.objects.prefetch_related('items').select_for_update().get(pk=waste_id)

    if waste.status != WasteLoss.Status.DRAFT:
        raise ValidationError(_("Only draft waste and loss documents can be confirmed."))

    for item in waste.items.all():
        remove_stock(
            product=item.product,
            warehouse=waste.warehouse,
            quantity=item.quantity,
            reference_type='WASTE_LOSS',
            reference_id=waste.id,
            user=user,
            movement_type='WASTE'
        )

    waste.status = WasteLoss.Status.CONFIRMED
    waste.approved_by = user
    waste.save(update_fields=['status', 'approved_by'])

    # Post financial expense entry
    post_waste_loss(waste.id, user)
    return waste