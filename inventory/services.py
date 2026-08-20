from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import (
    StockAdjustment,
    StockBalance,
    StockBatch,
    StockMovement,
    WarehouseTransfer,
    WasteLoss,
)

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
def add_stock(product, warehouse, quantity, unit_cost, reference_type, reference_id, user, batch_number=None, expiration_date=None, supplier=None, purchase_item=None, movement_type=StockMovement.MovementType.PURCHASE):
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
        movement_type=movement_type,
        quantity=quantity,
        unit_cost=unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=user
    )

    # Update fast stock balance
    balance, _created = StockBalance.objects.select_for_update().get_or_create(
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
def restore_stock_to_batch(batch_id, quantity, reference_type, reference_id, user):
    """Restore a sales return to its original batch and warehouse."""
    if quantity <= 0:
        raise ValidationError(_("Stock return quantity must be greater than zero."))
    batch = StockBatch.objects.select_for_update().select_related(
        "product", "warehouse"
    ).get(pk=batch_id)
    batch.quantity_remaining += quantity
    batch.status = StockBatch.BatchStatus.ACTIVE
    batch.save(update_fields=["quantity_remaining", "status"])

    balance, _created = StockBalance.objects.select_for_update().get_or_create(
        product=batch.product,
        warehouse=batch.warehouse,
        defaults={"quantity": Decimal("0.000"), "reserved_quantity": Decimal("0.000")},
    )
    balance.quantity += quantity
    balance.save(update_fields=["quantity", "updated_at"])
    StockMovement.objects.create(
        product=batch.product,
        warehouse=batch.warehouse,
        batch=batch,
        movement_type=StockMovement.MovementType.SALE_RETURN,
        quantity=quantity,
        unit_cost=batch.unit_cost,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=user,
    )
    return batch


@transaction.atomic
def confirm_stock_adjustment(adjustment_id, user):
    """
    Confirm a physical stock count and correct the books to match reality.

    For each line the counted quantity is compared with what the system
    currently holds. A shortage writes ADJUSTMENT_OUT, a surplus writes
    ADJUSTMENT_IN, and a line that matches does nothing at all.

    The system quantity is re-read here rather than trusted from the draft, so
    a count saved yesterday cannot silently undo a sale made this morning.
    """
    adjustment = (
        StockAdjustment.objects
        .select_for_update()
        .select_related("warehouse")
        .prefetch_related("items__product", "items__batch")
        .get(pk=adjustment_id)
    )

    if adjustment.status != StockAdjustment.Status.DRAFT:
        raise ValidationError(_("Only draft adjustments can be confirmed."))

    items = list(adjustment.items.all())
    if not items:
        raise ValidationError(_("An adjustment must contain at least one item."))

    warehouse = adjustment.warehouse

    for item in items:
        if item.counted_quantity < 0:
            raise ValidationError(_("Counted quantity cannot be negative."))

        product = item.product

        balance, _created = StockBalance.objects.select_for_update().get_or_create(
            product=product,
            warehouse=warehouse,
            defaults={"quantity": Decimal("0.000")},
        )

        system_quantity = balance.quantity
        variance = item.counted_quantity - system_quantity

        # Keep the document honest about what the system actually held.
        item.system_quantity = system_quantity
        item.variance = variance

        if variance == 0:
            item.save(update_fields=["system_quantity", "variance"])
            continue

        if variance < 0:
            # ---- Shortage: take the missing units out ----
            shortfall = -variance

            if item.batch:
                if item.batch.warehouse_id != warehouse.id:
                    raise ValidationError(
                        _("The selected batch is not stored in this warehouse.")
                    )
                if item.batch.quantity_remaining < shortfall:
                    raise ValidationError(
                        _("The selected batch does not contain the full shortage quantity.")
                    )
                allocations = [(item.batch, shortfall)]
            else:
                candidates = (
                    StockBatch.objects
                    .select_for_update()
                    .filter(
                        product=product,
                        warehouse=warehouse,
                        quantity_remaining__gt=0,
                    )
                    .exclude(status=StockBatch.BatchStatus.DEPLETED)
                    .order_by("expiration_date", "received_date")
                )
                allocations = []
                outstanding = shortfall
                for batch in candidates:
                    take = min(batch.quantity_remaining, outstanding)
                    allocations.append((batch, take))
                    outstanding -= take
                    if outstanding <= 0:
                        break
                if outstanding > 0:
                    raise ValidationError(
                        _("Stock batches do not contain the full shortage quantity.")
                    )

            for batch, qty in allocations:
                if qty <= 0:
                    continue
                batch.quantity_remaining -= qty
                if batch.quantity_remaining == 0:
                    batch.status = StockBatch.BatchStatus.DEPLETED
                batch.save(update_fields=["quantity_remaining", "status"])

                StockMovement.objects.create(
                    product=product,
                    warehouse=warehouse,
                    batch=batch,
                    movement_type=StockMovement.MovementType.ADJUSTMENT_OUT,
                    quantity=-qty,
                    unit_cost=batch.unit_cost,
                    reference_type="StockAdjustment",
                    reference_id=adjustment.id,
                    created_by=user,
                )

        else:
            # ---- Surplus: bring the extra units in ----
            target_batch = item.batch

            if target_batch and target_batch.warehouse_id != warehouse.id:
                raise ValidationError(
                    _("The selected batch is not stored in this warehouse.")
                )

            if target_batch is None:
                # No batch chosen, so park the surplus in a batch named after
                # this document. That keeps it traceable and costed.
                target_batch, _made = StockBatch.objects.select_for_update().get_or_create(
                    product=product,
                    warehouse=warehouse,
                    batch_number=f"ADJ-{adjustment.adjustment_number}",
                    defaults={
                        "expiration_date": None,
                        "received_date": adjustment.date,
                        "unit_cost": product.purchase_price,
                        "quantity_received": Decimal("0.000"),
                        "quantity_remaining": Decimal("0.000"),
                        "status": StockBatch.BatchStatus.ACTIVE,
                    },
                )

            target_batch.quantity_received += variance
            target_batch.quantity_remaining += variance
            if target_batch.status == StockBatch.BatchStatus.DEPLETED:
                target_batch.status = StockBatch.BatchStatus.ACTIVE
            target_batch.save(
                update_fields=["quantity_received", "quantity_remaining", "status"]
            )

            StockMovement.objects.create(
                product=product,
                warehouse=warehouse,
                batch=target_batch,
                movement_type=StockMovement.MovementType.ADJUSTMENT_IN,
                quantity=variance,
                unit_cost=target_batch.unit_cost,
                reference_type="StockAdjustment",
                reference_id=adjustment.id,
                created_by=user,
            )

        # The counted figure is the new truth.
        balance.quantity = item.counted_quantity
        balance.save(update_fields=["quantity"])

        item.save(update_fields=["system_quantity", "variance"])

    adjustment.status = StockAdjustment.Status.CONFIRMED
    adjustment.save(update_fields=["status"])

    # Stock and accounting must succeed or roll back together.
    from finance.services import post_stock_adjustment

    post_stock_adjustment(adjustment.id, user)

    return adjustment


@transaction.atomic
def mark_expired_batches(today=None):
    """
    Flag every active batch whose expiration date has passed.

    Spec 5.6 requires an expired batch to be blocked before it is written off.
    FEFO already refuses to sell an expired batch, but without this the batch
    stays ACTIVE forever, so nobody can find the stock in order to write it off.

    Safe to run repeatedly. Returns the number of batches newly marked.
    """
    today = today or timezone.now().date()

    expired = StockBatch.objects.select_for_update().filter(
        status=StockBatch.BatchStatus.ACTIVE,
        expiration_date__isnull=False,
        expiration_date__lt=today,
        quantity_remaining__gt=0,
    )

    return expired.update(status=StockBatch.BatchStatus.EXPIRED)


def get_expiry_watchlist(warning_days=None, today=None):
    """
    Batches that are expired or close to expiring, nearest expiry first.

    `warning_days` defaults to CompanySettings.expiration_warning_days, which
    is the configurable window the signed requirements ask for (module 17).
    """
    from core.models import CompanySettings

    today = today or timezone.now().date()
    if warning_days is None:
        warning_days = CompanySettings.load().expiration_warning_days

    cutoff = today + timedelta(days=warning_days)

    return (
        StockBatch.objects
        .select_related("product", "warehouse")
        .filter(
            expiration_date__isnull=False,
            expiration_date__lte=cutoff,
            quantity_remaining__gt=0,
        )
        .exclude(status=StockBatch.BatchStatus.DEPLETED)
        .order_by("expiration_date", "product__code")
    )


@transaction.atomic
def confirm_warehouse_transfer(transfer_id, user):
    """
    Confirm a warehouse transfer.

    Moves each line out of the source warehouse and into the destination one.
    Total company stock is unchanged (signed requirements, module 5): every
    TRANSFER_OUT is matched by an equal TRANSFER_IN.

    Batch identity is preserved. If a source batch carries an expiration date,
    the destination batch keeps the same batch number and expiry, so FEFO and
    expiry blocking still work after the move.
    """
    transfer = (
        WarehouseTransfer.objects
        .select_for_update()
        .select_related("source_warehouse", "destination_warehouse")
        .prefetch_related("items__product", "items__batch")
        .get(pk=transfer_id)
    )

    if transfer.status != WarehouseTransfer.TransferStatus.DRAFT:
        raise ValidationError(_("Only draft transfers can be confirmed."))

    if transfer.source_warehouse_id == transfer.destination_warehouse_id:
        raise ValidationError(
            _("The source and destination warehouses must be different.")
        )

    items = list(transfer.items.all())
    if not items:
        raise ValidationError(_("A transfer must contain at least one item."))

    source = transfer.source_warehouse
    destination = transfer.destination_warehouse

    for item in items:
        if item.quantity <= 0:
            raise ValidationError(_("Transfer quantity must be greater than zero."))

        product = item.product

        # Work out which physical batches leave the source warehouse.
        if item.batch:
            if item.batch.warehouse_id != source.id:
                raise ValidationError(
                    _("The selected batch is not stored in the source warehouse.")
                )
            if item.batch.quantity_remaining < item.quantity:
                raise ValidationError(
                    _("Batch %(batch)s only has %(available)s remaining.") % {
                        "batch": item.batch.batch_number,
                        "available": item.batch.quantity_remaining,
                    }
                )
            allocations = [(item.batch, item.quantity)]
        else:
            available = get_available_stock(product, source)
            if available < item.quantity:
                raise ValidationError(
                    _("Not enough stock of %(product)s in %(warehouse)s. Available: %(available)s.") % {
                        "product": product.name,
                        "warehouse": source.name,
                        "available": available,
                    }
                )
            allocations = select_fefo_batch(product, source, item.quantity)

        moved_value = Decimal("0.000")

        for batch, qty in allocations:
            moved_value += qty * batch.unit_cost

            # ---- Leave the source warehouse ----
            batch.quantity_remaining -= qty
            if batch.quantity_remaining == 0:
                batch.status = StockBatch.BatchStatus.DEPLETED
            batch.save(update_fields=["quantity_remaining", "status"])

            StockMovement.objects.create(
                product=product,
                warehouse=source,
                batch=batch,
                movement_type=StockMovement.MovementType.TRANSFER_OUT,
                quantity=-qty,
                unit_cost=batch.unit_cost,
                reference_type="WarehouseTransfer",
                reference_id=transfer.id,
                created_by=user,
            )

            # ---- Arrive at the destination warehouse ----
            # Reuse an existing batch there with the same number, cost and
            # expiry so repeated transfers do not fragment the batch list.
            destination_batch, created = StockBatch.objects.select_for_update().get_or_create(
                product=product,
                warehouse=destination,
                batch_number=batch.batch_number,
                expiration_date=batch.expiration_date,
                unit_cost=batch.unit_cost,
                defaults={
                    "received_date": transfer.date,
                    "quantity_received": Decimal("0.000"),
                    "quantity_remaining": Decimal("0.000"),
                    "supplier": batch.supplier,
                    "status": StockBatch.BatchStatus.ACTIVE,
                },
            )
            destination_batch.quantity_received += qty
            destination_batch.quantity_remaining += qty
            if destination_batch.status == StockBatch.BatchStatus.DEPLETED:
                destination_batch.status = StockBatch.BatchStatus.ACTIVE
            destination_batch.save(
                update_fields=["quantity_received", "quantity_remaining", "status"]
            )

            StockMovement.objects.create(
                product=product,
                warehouse=destination,
                batch=destination_batch,
                movement_type=StockMovement.MovementType.TRANSFER_IN,
                quantity=qty,
                unit_cost=batch.unit_cost,
                reference_type="WarehouseTransfer",
                reference_id=transfer.id,
                created_by=user,
            )

        # ---- Update the fast balances on both sides ----
        source_balance = StockBalance.objects.select_for_update().get(
            product=product, warehouse=source
        )
        source_balance.quantity -= item.quantity
        source_balance.save(update_fields=["quantity"])

        destination_balance, _created = StockBalance.objects.select_for_update().get_or_create(
            product=product,
            warehouse=destination,
            defaults={"quantity": Decimal("0.000")},
        )
        destination_balance.quantity += item.quantity
        destination_balance.save(update_fields=["quantity"])

        # Record what the moved goods were actually worth (spec 5.4).
        item.unit_cost = (moved_value / item.quantity).quantize(Decimal("0.001"))
        item.save(update_fields=["unit_cost"])

    transfer.status = WarehouseTransfer.TransferStatus.COMPLETED
    transfer.save(update_fields=["status"])

    return transfer


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

    # ---- Take the goods out of the warehouse ----
    # remove_stock() cannot be reused here: it selects by FEFO and deliberately
    # skips expired batches, but expired stock is the main thing we write off.
    warehouse = waste.warehouse

    for item in items:
        product = item.product

        if item.batch:
            if item.batch.warehouse_id != warehouse.id:
                raise ValidationError(
                    _("The selected batch is not stored in this warehouse.")
                )
            if item.batch.quantity_remaining < item.quantity:
                raise ValidationError(
                    _("Batch %(batch)s only has %(available)s remaining.") % {
                        "batch": item.batch.batch_number,
                        "available": item.batch.quantity_remaining,
                    }
                )
            allocations = [(item.batch, item.quantity)]
        else:
            # Oldest expiry first, and expired batches are eligible.
            candidates = (
                StockBatch.objects
                .select_for_update()
                .filter(
                    product=product,
                    warehouse=warehouse,
                    quantity_remaining__gt=0,
                )
                .exclude(status=StockBatch.BatchStatus.DEPLETED)
                .order_by("expiration_date", "received_date")
            )

            allocations = []
            outstanding = item.quantity
            for batch in candidates:
                take = min(batch.quantity_remaining, outstanding)
                allocations.append((batch, take))
                outstanding -= take
                if outstanding <= 0:
                    break

            if outstanding > 0:
                raise ValidationError(
                    _("Not enough stock of %(product)s in %(warehouse)s. Available: %(available)s.") % {
                        "product": product.name,
                        "warehouse": warehouse.name,
                        "available": item.quantity - outstanding,
                    }
                )

        for batch, qty in allocations:
            batch.quantity_remaining -= qty
            if batch.quantity_remaining == 0:
                batch.status = StockBatch.BatchStatus.DEPLETED
            batch.save(update_fields=["quantity_remaining", "status"])

            StockMovement.objects.create(
                product=product,
                warehouse=warehouse,
                batch=batch,
                movement_type=StockMovement.MovementType.WASTE,
                quantity=-qty,
                unit_cost=batch.unit_cost,
                reference_type="WasteLoss",
                reference_id=waste.id,
                created_by=user,
            )

        balance, _created = StockBalance.objects.select_for_update().get_or_create(
            product=product,
            warehouse=warehouse,
            defaults={"quantity": Decimal("0.000")},
        )
        balance.quantity -= item.quantity
        balance.save(update_fields=["quantity"])

    waste.status = WasteLoss.Status.CONFIRMED
    waste.save(update_fields=["status"])

    # Local import keeps the inventory and finance modules free of import cycles.
    from finance.services import post_waste_loss

    journal = post_waste_loss(waste.id, user)
    return waste, journal
