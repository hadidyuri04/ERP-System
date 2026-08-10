from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from finance.models import Account, JournalEntry, JournalEntryLine
from finance.services import post_journal_entry
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
            "Only draft purchase invoices can be confirmed."
        )

    items = list(
        purchase.items.select_related("product").all()
    )

    # 2. Invoice must contain at least one item
    if not items:
        raise ValidationError(
            "Purchase invoice must contain at least one item."
        )

    total_inventory_cost = Decimal("0.000")

    # 3. Process every purchased item
    for item in items:

        if item.quantity <= 0:
            raise ValidationError(
                f"Quantity for {item.product.name} must be greater than zero."
            )

        if item.unit_cost < 0:
            raise ValidationError(
                f"Unit cost for {item.product.name} cannot be negative."
            )

        # Cost that will enter inventory
        item_cost = item.quantity * item.unit_cost
        total_inventory_cost += item_cost

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

    # 7. Find the accounting accounts
    inventory_account = Account.objects.get(code="1400")

    if purchase.payment_type == PurchaseInvoice.PaymentType.CASH:
        credit_account = Account.objects.get(code="1100")
    else:
        credit_account = Account.objects.get(code="2100")

    # 8. Create accounting journal
    journal = JournalEntry.objects.create(
        entry_number=f"PUR-{purchase.invoice_number}",
        date=purchase.invoice_date,
        description=f"Purchase Invoice {purchase.invoice_number}",
        source_type=JournalEntry.SourceType.PURCHASE,
        source_id=purchase.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Inventory increases → Debit
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=inventory_account,
        debit=total_inventory_cost,
        credit=Decimal("0.000"),
        description=f"Inventory purchased - {purchase.invoice_number}",
    )

    # Cash / Accounts Payable increases on credit side
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=credit_account,
        debit=Decimal("0.000"),
        credit=total_inventory_cost,
        supplier=purchase.supplier,
        description=f"Purchase from {purchase.supplier.name}",
    )

    # 9. Use the finance engine you already created
    post_journal_entry(journal.id)

    # 10. Finally confirm the purchase
    purchase.status = PurchaseInvoice.Status.CONFIRMED

    purchase.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return purchase