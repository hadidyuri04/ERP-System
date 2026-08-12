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
        status=StockBatch.BatchStatus.ACTIVE,
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
            status=StockBatch.BatchStatus.ACTIVE
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
    total_cost = Decimal("0.000")

    for batch, qty_to_take in allocations:
        total_cost += qty_to_take * batch.unit_cost
        batch.quantity_remaining -= qty_to_take
        if batch.quantity_remaining == 0:
            batch.status = StockBatch.BatchStatus.DEPLETED
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

    return total_cost


@transaction.atomic
def confirm_waste_loss(waste_id, user):
    """Confirm a waste document and post its accounting journal atomically."""
    waste = (
        WasteLoss.objects
        .select_for_update()
        .prefetch_related("items")
        .get(pk=waste_id)
    )

    if waste.status != WasteLoss.Status.DRAFT:
        raise ValidationError(
            _("Only draft waste and loss documents can be confirmed.")
        )

    items = list(waste.items.all())
    if not items:
        raise ValidationError(
            _("A waste and loss document must contain at least one item.")
        )

    for item in items:
        if item.quantity <= 0:
            raise ValidationError(
                _("Waste quantity must be greater than zero.")
            )
        if item.unit_cost < 0:
            raise ValidationError(
                _("Waste unit cost cannot be negative.")
            )

        expected_total = item.quantity * item.unit_cost
        if item.total_cost != expected_total:
            raise ValidationError(
                _(
                    "Waste item total does not match quantity multiplied by "
                    "unit cost."
                )
            )

    waste.status = WasteLoss.Status.CONFIRMED
    waste.save(update_fields=["status"])

    # Local import keeps the inventory and finance modules free of import cycles.
    from finance.services import post_waste_loss

    journal = post_waste_loss(waste.id, user)
    return waste, journal
