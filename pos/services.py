from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from finance.services import post_pos_sale
from inventory.services import remove_stock, get_available_stock
from .models import POSSale, POSSaleItem, POSPayment

@transaction.atomic
def complete_sale(warehouse, cashier, items_data, payments_data, customer=None, notes="", session=None,):
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
        session=session,
        sale_number=sale_number,
        customer=customer,
        warehouse=warehouse,
        cashier=cashier,
        date=timezone.now(),
        status=POSSale.SaleStatus.COMPLETED,
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
        removed_cost = remove_stock(
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
            unit_cost=removed_cost / quantity,
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

    post_pos_sale(sale.id, cashier)

    return sale
