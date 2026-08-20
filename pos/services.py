from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from finance.services import post_pos_session
from inventory.services import remove_stock, get_available_stock
from .models import POSCashTransaction, POSPayment, POSSale, POSSaleItem, POSSession

@transaction.atomic
def complete_sale(warehouse, cashier, items_data, payments_data, customer=None, notes="", session=None, discount_code_obj=None):
    """
    Completes a POS sale:
    1. Validates available stock and expiry.
    2. Applies item discounts & optional DiscountCode header discount.
    3. Creates POSSale and POSSaleItems.
    4. Triggers FEFO stock removal and stock movements.
    5. Records POSPayments and increments DiscountCode usage counter.
    """
    if session is not None:
        session = POSSession.objects.select_for_update().get(pk=session.pk)
    if not items_data:
        raise ValidationError(_("Cannot complete a sale with an empty cart."))
    if session is None or session.status != POSSession.SessionStatus.OPEN:
        raise ValidationError(_("A sale requires an open register session."))
    if session.cashier_id != cashier.id:
        raise ValidationError(_("The sale must use the cashier's open register session."))
    if session.warehouse_id != warehouse.id:
        raise ValidationError(_("The sale warehouse must match the register warehouse."))
    if not payments_data:
        raise ValidationError(_("A completed POS sale must contain at least one payment."))

    # Calculate totals
    subtotal = Decimal('0.000')
    line_discounts_total = Decimal('0.000')
    total_tax = Decimal('0.000')
    sale_items_to_create = []

    for item_data in items_data:
        product = item_data['product']
        quantity = Decimal(str(item_data['quantity']))
        unit_price = Decimal(str(item_data['unit_price']))
        discount_amount = Decimal(str(item_data.get('discount_amount', '0.000')))

        tax_amount = product.tax_for((quantity * unit_price) - discount_amount)

        if not product.is_sellable:
            raise ValidationError(
                _("%(product_name)s is not available for sale.") % {
                    'product_name': product.name,
                }
            )

        if product.maximum_discount and quantity * unit_price > 0:
            discount_pct = (discount_amount / (quantity * unit_price)) * Decimal("100")
            if discount_pct > product.maximum_discount:
                raise ValidationError(
                    _("Discount on %(product_name)s exceeds the maximum of %(max)s%%.") % {
                        'product_name': product.name,
                        'max': product.maximum_discount,
                    }
                )

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

        subtotal += quantity * unit_price
        line_discounts_total += discount_amount
        total_tax += tax_amount

        sale_items_to_create.append({
            'product': product,
            'quantity': quantity,
            'unit_price': unit_price,
            'discount_amount': discount_amount,
            'tax_amount': tax_amount,
            'line_total': line_total
        })

    # Validate and process header Discount Code if supplied
    header_code_discount = Decimal('0.000')
    if discount_code_obj:
        is_valid, err_msg = discount_code_obj.is_valid(subtotal)
        if not is_valid:
            raise ValidationError(err_msg)
        header_code_discount = Decimal(str(discount_code_obj.calculate_discount(subtotal)))

    total_discount = line_discounts_total + header_code_discount
    grand_total = max(Decimal('0.000'), subtotal - total_discount + total_tax)

    total_paid = Decimal("0.000")
    cash_tendered = Decimal("0.000")
    for payment_data in payments_data:
        amount = Decimal(str(payment_data['amount']))
        method = payment_data['payment_method']
        if amount <= 0:
            raise ValidationError(_("POS payment amounts must be greater than zero."))
        if method not in POSPayment.PaymentMethod.values:
            raise ValidationError(_("The POS sale contains an unsupported payment method."))
        if method == POSPayment.PaymentMethod.CREDIT and customer is None:
            raise ValidationError(_("Credit sales require a customer."))
        total_paid += amount
        if method == POSPayment.PaymentMethod.CASH:
            cash_tendered += amount

    if grand_total < total_tax:
        raise ValidationError(_("POS sale discounts cannot exceed pre-tax revenue."))
    if total_paid < grand_total:
        raise ValidationError(_("POS payment total is less than the sale total."))
    change_amount = total_paid - grand_total
    if change_amount > cash_tendered:
        raise ValidationError(_("Sale change exceeds the available cash payment."))
    
    sale_number = f"POS-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    sale = POSSale.objects.create(
        session=session,
        sale_number=sale_number,
        customer=customer,
        warehouse=warehouse,
        cashier=cashier,
        discount_code=discount_code_obj,
        date=timezone.now(),
        status=POSSale.SaleStatus.COMPLETED,
        subtotal=subtotal,
        discount_amount=total_discount,
        tax_amount=total_tax,
        total=grand_total,
        paid_amount=total_paid,
        change_amount=change_amount,
        notes=notes
    )

    if discount_code_obj:
        discount_code_obj.used_count += 1
        discount_code_obj.save(update_fields=['used_count'])

    # Process items and inventory removal (FEFO)
    for item_data in sale_items_to_create:
        product = item_data['product']
        quantity = item_data['quantity']
        
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
            unit_cost=removed_cost / quantity if quantity > 0 else Decimal('0.000'),
            discount_amount=item_data['discount_amount'],
            tax_amount=item_data['tax_amount'],
            line_total=item_data['line_total']
        )

    for payment_data in payments_data:
        POSPayment.objects.create(
            sale=sale,
            payment_method=payment_data['payment_method'],
            amount=Decimal(str(payment_data['amount'])),
            reference_number=payment_data.get('reference_number', ''),
            received_at=timezone.now(),
            created_by=cashier
        )

    return sale


@transaction.atomic
def close_pos_session(session_id, user, actual_cash):
    """Reconcile a register and post all of its unposted completed sales once."""
    session = POSSession.objects.select_for_update().get(pk=session_id)
    if session.status != POSSession.SessionStatus.OPEN:
        raise ValidationError(_("Only an open register session can be closed."))

    is_admin = user.is_superuser or getattr(user, "role", "") == "admin"
    if session.cashier_id != user.id and not is_admin:
        raise ValidationError(_("Only the session cashier or an administrator can close this register."))

    actual_cash = Decimal(str(actual_cash))
    if actual_cash < 0:
        raise ValidationError(_("Actual counted cash cannot be negative."))

    cash_sales = POSPayment.objects.filter(
        sale__session=session,
        sale__status=POSSale.SaleStatus.COMPLETED,
        payment_method=POSPayment.PaymentMethod.CASH,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.000")
    cash_change = session.sales.filter(
        status=POSSale.SaleStatus.COMPLETED,
    ).aggregate(total=Sum("change_amount"))["total"] or Decimal("0.000")
    cash_in = session.cash_transactions.filter(
        transaction_type=POSCashTransaction.TransactionType.CASH_IN,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.000")
    cash_out = session.cash_transactions.filter(
        transaction_type=POSCashTransaction.TransactionType.CASH_OUT,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.000")

    expected_cash = session.opening_balance + cash_sales - cash_change + cash_in - cash_out
    session.closing_balance_expected = expected_cash
    session.closing_balance_actual = actual_cash
    session.difference = actual_cash - expected_cash
    session.status = POSSession.SessionStatus.CLOSED
    session.closed_at = timezone.now()
    session.save(update_fields=[
        "closing_balance_expected",
        "closing_balance_actual",
        "difference",
        "status",
        "closed_at",
    ])

    journal = post_pos_session(session.id, user)
    return session, journal
@transaction.atomic
def hold_sale(warehouse, cashier, items_data, customer=None, notes="", session=None):
    """
    Saves an active cart state as a held sale order without deducting stock or creating finance entries.
    """
    if not items_data:
        raise ValidationError(_("Cannot hold an empty cart."))

    subtotal = Decimal('0.000')
    total_discount = Decimal('0.000')
    total_tax = Decimal('0.000')
    sale_items_to_create = []

    for item_data in items_data:
        product = item_data['product']
        quantity = Decimal(str(item_data['quantity']))
        unit_price = Decimal(str(item_data['unit_price']))
        discount_amount = Decimal(str(item_data.get('discount_amount', '0.000')))
        tax_amount = product.tax_for((quantity * unit_price) - discount_amount)

        line_total = (quantity * unit_price) - discount_amount + tax_amount
        subtotal += quantity * unit_price
        total_discount += discount_amount
        total_tax += tax_amount

        sale_items_to_create.append({
            'product': product,
            'quantity': quantity,
            'unit_price': unit_price,
            'discount_amount': discount_amount,
            'tax_amount': tax_amount,
            'line_total': line_total
        })

    grand_total = subtotal - total_discount + total_tax
    sale_number = f"HOLD-{timezone.now().strftime('%Y%m%d%H%M%S')}"

    sale = POSSale.objects.create(
        session=session,
        sale_number=sale_number,
        customer=customer,
        warehouse=warehouse,
        cashier=cashier,
        date=timezone.now(),
        status=POSSale.SaleStatus.HELD,
        subtotal=subtotal,
        discount_amount=total_discount,
        tax_amount=total_tax,
        total=grand_total,
        notes=notes
    )

    for item in sale_items_to_create:
        POSSaleItem.objects.create(
            sale=sale,
            product=item['product'],
            quantity=item['quantity'],
            unit_price=item['unit_price'],
            unit_cost=getattr(item['product'], 'cost_price', Decimal('0.000')),
            discount_amount=item['discount_amount'],
            tax_amount=item['tax_amount'],
            line_total=item['line_total']
        )

    return sale
