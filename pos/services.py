from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from inventory.services import remove_stock, get_available_stock
from .models import POSSale, POSSaleItem, POSPayment

@transaction.atomic
def complete_sale(warehouse, cashier, items_data, payments_data, customer=None, notes=""):
    """
    Completes a POS sale:
    1. Validates available stock and expiry.
    2. Creates POSSale and POSSaleItems.
    3. Triggers FEFO stock removal and stock movements.
    4. Records POSPayments.
    """
    if not items_data:
        raise ValidationError(_("Cannot complete a sale with an empty cart."))

    # Calculate totals
    subtotal = Decimal('0.000')
    sale_items_to_create = []

    for item_data in items_data:
        product = item_data['product']
        quantity = Decimal(str(item_data['quantity']))
        unit_price = Decimal(str(item_data['unit_price']))
        discount_amount = Decimal(str(item_data.get('discount_amount', '0.000')))
        tax_amount = Decimal(str(item_data.get('tax_amount', '0.000')))
        
        # Check available stock before committing
        available = get_available_stock(product, warehouse)
        if available < quantity:
            raise ValidationError(
                _("Insufficient stock for product %(product_name)s. Available: %(available)s, Requested: %(quantity)s") % {
                    'product_name': product.name,
                    'available': available,
                    'quantity': quantity
                }
            )
        
        line_total = (quantity * unit_price) - discount_amount + tax_amount
        subtotal += line_total

        sale_items_to_create.append({
            'product': product,
            'quantity': quantity,
            'unit_price': unit_price,
            'discount_amount': discount_amount,
            'tax_amount': tax_amount,
            'line_total': line_total
        })

    # Validate payments match total (or handle credit logic)
    total_paid = sum(Decimal(str(p['amount'])) for p in payments_data)
    
    # Create POSSale header
    sale_number = f"POS-{timezone.now().strftime('%Y%m%d%H%M%S')}" # Ensure timezone import or use format
    sale = POSSale.objects.create(
        sale_number=sale_number,
        customer=customer,
        warehouse=warehouse,
        cashier=cashier,
        date=timezone.now(),
        status=POSSale.Status.COMPLETED,
        subtotal=subtotal,
        total=subtotal, # Expand with global tax/discount if needed
        paid_amount=total_paid,
        change_amount=max(Decimal('0.000'), total_paid - subtotal),
        notes=notes
    )

    # Process items and inventory removal (FEFO)
    for item_data in sale_items_to_create:
        product = item_data['product']
        quantity = item_data['quantity']
        
        # Remove stock using inventory service FEFO logic
        remove_stock(
            product=product,
            warehouse=warehouse,
            quantity=quantity,
            reference_type='POS_SALE',
            reference_id=sale.id,
            user=cashier,
            movement_type='SALE'
        )

        POSSaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=item_data['unit_price'],
            unit_cost=product.purchase_price, # Fallback baseline cost for COGS
            discount_amount=item_data['discount_amount'],
            tax_amount=item_data['tax_amount'],
            line_total=item_data['line_total']
        )

    # Record Payments
    for payment_data in payments_data:
        POSPayment.objects.create(
            sale=sale,
            payment_method=payment_data['payment_method'],
            amount=payment_data['amount'],
            reference_number=payment_data.get('reference_number', ''),
            received_at=timezone.now(),
            created_by=cashier
        )

    # TODO: Trigger finance service to post Journal Entry for Sales Revenue and COGS
    return sale
@transaction.atomic
def process_sale_return(original_sale_id, return_items_data, user, notes=""):
    """
    Processes a sales return:
    1. References the original POS sale.
    2. Restores inventory stock if items are returned in valid condition.
    3. Creates accounting reversal entries (credits cash/receivable, debits sales/returns).
    """
    from .models import POSSale, SalesReturn, SalesReturnItem
    from finance.models import Account, JournalEntry, JournalEntryLine
    from finance.services import post_journal_entry
    from inventory.services import add_stock

    original_sale = POSSale.objects.prefetch_related('items').get(pk=original_sale_id)

    if original_sale.status != POSSale.Status.COMPLETED:
        raise ValidationError(_("Can only process returns for completed sales."))

    return_subtotal = Decimal('0.000')
    return_items_to_create = []

    for ret_data in return_items_data:
        original_item_id = ret_data['original_item_id']
        return_quantity = Decimal(str(ret_data['quantity']))

        original_item = original_sale.items.get(pk=original_item_id)

        if return_quantity <= 0:
            raise ValidationError(_("Return quantity must be greater than zero."))
        
        if return_quantity > original_item.quantity:
            raise ValidationError(
                _("Cannot return %(return_qty)s of %(product)s. Original sale quantity was %(orig_qty)s.") % {
                    'return_qty': return_quantity,
                    'product': original_item.product.name,
                    'orig_qty': original_item.quantity
                }
            )

        line_total = return_quantity * original_item.unit_price
        return_subtotal += line_total

        return_items_to_create.append({
            'original_item': original_item,
            'product': original_item.product,
            'quantity': return_quantity,
            'unit_price': original_item.unit_price,
            'unit_cost': original_item.unit_cost,
            'line_total': line_total,
            'restock': ret_data.get('restock', True) # Whether item goes back to inventory or waste
        })

    # Create SalesReturn header document
    return_number = f"RET-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    sales_return = SalesReturn.objects.create(
        return_number=return_number,
        original_sale=original_sale,
        customer=original_sale.customer,
        warehouse=original_sale.warehouse,
        cashier=user,
        date=timezone.now(),
        total_amount=return_subtotal,
        notes=notes
    )

    for item_info in return_items_to_create:
        SalesReturnItem.objects.create(
            sales_return=sales_return,
            original_item=item_info['original_item'],
            product=item_info['product'],
            quantity=item_info['quantity'],
            unit_price=item_info['unit_price'],
            line_total=item_info['line_total']
        )

        # If item is restockable, add it back to inventory stock
        if item_info['restock']:
            add_stock(
                product=item_info['product'],
                warehouse=original_sale.warehouse,
                quantity=item_info['quantity'],
                unit_cost=item_info['unit_cost'],
                reference_type='SALES_RETURN',
                reference_id=sales_return.id,
                user=user
            )

    # Create accounting reversal Journal Entry
    sales_revenue_account = Account.objects.get(code="4100")
    cash_account = Account.objects.get(code="1100")

    journal = JournalEntry.objects.create(
        entry_number=f"RET-{sales_return.return_number}",
        date=timezone.now().date(),
        description=_("Sales Return for POS Sale %(sale_number)s") % {'sale_number': original_sale.sale_number},
        source_type=JournalEntry.SourceType.RETURN,
        source_id=sales_return.id,
        status=JournalEntry.Status.DRAFT,
        created_by=user,
    )

    # Reverse Revenue (Debit Sales Revenue)
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=sales_revenue_account,
        description=_("Return reversal for sale %(sale_number)s") % {'sale_number': original_sale.sale_number},
        debit=return_subtotal,
        credit=Decimal("0.000"),
    )

    # Refund Payer (Credit Cash/Bank)
    JournalEntryLine.objects.create(
        journal_entry=journal,
        account=cash_account,
        description=_("Cash refund for return %(return_number)s") % {'return_number': sales_return.return_number},
        debit=Decimal("0.000"),
        credit=return_subtotal,
    )

    post_journal_entry(journal.id)
    return sales_return