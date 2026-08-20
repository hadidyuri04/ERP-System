from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import Quotation, QuotationItem

@transaction.atomic
def create_quotation(customer, date, expiry_date, items_data, user, discount_amount=Decimal('0.000'), tax_amount=Decimal('0.000'), notes=""):
    """
    Creates a Quotation with items. Does NOT affect stock or accounting balances.
    """
    if not items_data:
        raise ValidationError(_("Cannot create a quotation with no items."))

    quotation_number = f"QT-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    
    subtotal = Decimal('0.000')
    line_discounts = Decimal('0.000')
    line_taxes = Decimal('0.000')
    prepared_items = []

    for item in items_data:
        qty = Decimal(str(item['quantity']))
        price = Decimal(str(item['unit_price']))
        disc = Decimal(str(item.get('discount_amount', '0.000')))
        tax = Decimal(str(item.get('tax_amount', '0.000')))
        line_total = (qty * price) - disc + tax
        subtotal += qty * price
        line_discounts += disc
        line_taxes += tax

        prepared_items.append({
            'product': item['product'],
            'quantity': qty,
            'unit_price': price,
            'discount_amount': disc,
            'tax_amount': tax,
            'line_total': line_total
        })

    total_discount = line_discounts + Decimal(str(discount_amount))
    total = subtotal - total_discount + line_taxes

    quotation = Quotation.objects.create(
        quotation_number=quotation_number,
        customer=customer,
        date=date,
        expiry_date=expiry_date,
        status=Quotation.Status.DRAFT,
        subtotal=subtotal,
        discount_amount=total_discount,
        tax_amount=line_taxes,
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
