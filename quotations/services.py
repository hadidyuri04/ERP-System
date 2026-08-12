from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import Quotation, QuotationItem
from pos.services import complete_sale

@transaction.atomic
def create_quotation(customer, date, expiry_date, items_data, user, discount_amount=Decimal('0.000'), tax_amount=Decimal('0.000'), notes=""):
    """
    Creates a Quotation with items. Does NOT affect stock or accounting balances.
    """
    if not items_data:
        raise ValidationError(_("Cannot create a quotation with no items."))

    quotation_number = f"QT-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    
    subtotal = Decimal('0.000')
    prepared_items = []

    for item in items_data:
        qty = Decimal(str(item['quantity']))
        price = Decimal(str(item['unit_price']))
        disc = Decimal(str(item.get('discount_amount', '0.000')))
        tax = Decimal(str(item.get('tax_amount', '0.000')))
        line_total = (qty * price) - disc + tax
        subtotal += line_total

        prepared_items.append({
            'product': item['product'],
            'quantity': qty,
            'unit_price': price,
            'discount_amount': disc,
            'tax_amount': tax,
            'line_total': line_total
        })

    total = subtotal - Decimal(str(discount_amount)) + Decimal(str(tax_amount))

    quotation = Quotation.objects.create(
        quotation_number=quotation_number,
        customer=customer,
        date=date,
        expiry_date=expiry_date,
        status=Quotation.Status.DRAFT,
        subtotal=subtotal,
        discount_amount=discount_amount,
        tax_amount=tax_amount,
        total=total,
        notes=notes,
        created_by=user
    )

    for p_item in prepared_items:
        QuotationItem.objects.create(
            quotation=quotation,
            product=p_item['product'],
            quantity=p_item['quantity'],
            unit_price=p_item['unit_price'],
            discount_amount=p_item['discount_amount'],
            tax_amount=p_item['tax_amount'],
            line_total=p_item['line_total']
        )

    return quotation


@transaction.atomic
def convert_quotation_to_pos_sale(quotation_id, warehouse, cashier, payments_data):
    """
    Converts an ACCEPTED quotation into a completed POS Sale.
    Deducts stock and creates financial transactions at this exact stage.
    """
    quotation = Quotation.objects.select_for_update().prefetch_related('items').get(pk=quotation_id)

    if quotation.status in [Quotation.Status.REJECTED, Quotation.Status.EXPIRED]:
        raise ValidationError(_("Cannot convert a rejected or expired quotation."))

    if quotation.expiry_date < timezone.now().date():
        quotation.status = Quotation.Status.EXPIRED
        quotation.save(update_fields=['status'])
        raise ValidationError(_("This quotation has expired and cannot be converted."))

    # Prepare item payload for POS service
    pos_items_data = []
    for item in quotation.items.all():
        pos_items_data.append({
            'product': item.product,
            'quantity': item.quantity,
            'unit_price': item.unit_price,
            'discount_amount': item.discount_amount,
            'tax_amount': item.tax_amount,
        })

    # Complete POS sale (validates stock, updates FEFO batches, posts accounting)[cite: 1]
    sale = complete_sale(
        warehouse=warehouse,
        cashier=cashier,
        items_data=pos_items_data,
        payments_data=payments_data,
        customer=quotation.customer,
        notes=_("Converted from Quotation %(q_num)s") % {'q_num': quotation.quotation_number}
    )

    # Update quotation status
    quotation.status = Quotation.Status.ACCEPTED
    quotation.save(update_fields=['status'])

    return sale