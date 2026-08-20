"""
Shared setup for tests across apps.

Almost every service in this project ends in a journal entry, and posting is
refused unless the accounts exist and the date falls in an open fiscal period.
Both of those are easy to forget, and because the services are atomic a missing
account silently rolls back the stock changes too. Keeping the setup in one
place means a new account requirement is a one-line change here rather than a
hunt through five test files.
"""

from decimal import Decimal

from django.utils import timezone

# code, name, account type
POSTING_ACCOUNTS = [
    ("1100", "Cash", "asset"),
    ("1200", "Bank", "asset"),
    ("1210", "Card Clearing", "asset"),
    ("1300", "Accounts Receivable", "asset"),
    ("1400", "Inventory", "asset"),
    ("1500", "Purchase Tax", "asset"),
    ("2100", "Accounts Payable", "liability"),
    ("2200", "Sales Tax Payable", "liability"),
    ("3100", "Capital", "equity"),
    ("4100", "Sales Revenue", "revenue"),
    ("4300", "Inventory Adjustment Gain", "revenue"),
    ("5100", "Cost of Goods Sold", "expense"),
    ("6300", "Waste and Loss", "expense"),
    ("6310", "Inventory Adjustment Loss", "expense"),
]


def seed_accounts():
    """Create every account the posting services look up."""
    from finance.models import Account

    for code, name, account_type in POSTING_ACCOUNTS:
        Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "account_type": account_type,
                "allow_posting": True,
            },
        )


def seed_fiscal_year(user, today=None):
    """Open the fiscal year covering `today` so postings are accepted."""
    from finance.models import FiscalYear
    from finance.services import create_fiscal_year

    today = today or timezone.now().date()
    if not FiscalYear.objects.filter(year=today.year).exists():
        create_fiscal_year(today.year, user=user)


def seed_finance(user, today=None):
    """Accounts plus an open fiscal year: the minimum for anything to post."""
    seed_accounts()
    seed_fiscal_year(user, today)


def seed_tax_rate(rate="16.000", code="TAX16"):
    from finance.models import TaxRate

    tax_rate, _ = TaxRate.objects.get_or_create(
        code=code,
        defaults={
            "name": f"Standard {rate}%",
            "rate": Decimal(rate),
            "subject_to_tax": True,
        },
    )
    return tax_rate


def seed_catalogue(tax_rate=None, selling_price="0.350", purchase_price="0.250"):
    """One category, unit, warehouse and product. Returns them as a dict."""
    from inventory.models import Category, Product, Unit, Warehouse

    category, _ = Category.objects.get_or_create(
        code="C1", defaults={"name_en": "Drinks", "name_ar": "مشروبات"}
    )
    unit, _ = Unit.objects.get_or_create(
        symbol="pc", defaults={"name_en": "Piece", "name_ar": "قطعة"}
    )
    warehouse, _ = Warehouse.objects.get_or_create(
        code="W1", defaults={"name": "Main"}
    )
    product, _ = Product.objects.get_or_create(
        code="P1",
        defaults={
            "name_en": "Ice Tea",
            "name_ar": "شاي مثلج",
            "category": category,
            "unit": unit,
            "purchase_price": Decimal(purchase_price),
            "selling_price": Decimal(selling_price),
            "tax_rate": tax_rate,
        },
    )

    return {
        "category": category,
        "unit": unit,
        "warehouse": warehouse,
        "product": product,
    }


def give_stock(product, warehouse, quantity, unit_cost="0.250",
               batch_number="B1", expires_in_days=None):
    """Put real batch-backed stock in a warehouse, and update the balance."""
    from datetime import timedelta

    from inventory.models import StockBalance, StockBatch

    today = timezone.now().date()
    quantity = Decimal(quantity)

    StockBatch.objects.create(
        product=product,
        warehouse=warehouse,
        batch_number=batch_number,
        expiration_date=(
            None if expires_in_days is None else today + timedelta(days=expires_in_days)
        ),
        received_date=today,
        unit_cost=Decimal(unit_cost),
        quantity_received=quantity,
        quantity_remaining=quantity,
        status=StockBatch.BatchStatus.ACTIVE,
    )

    balance, _ = StockBalance.objects.get_or_create(
        product=product, warehouse=warehouse,
        defaults={"quantity": Decimal("0.000")},
    )
    balance.quantity += quantity
    balance.save(update_fields=["quantity"])
    return balance


def stock_of(product, warehouse):
    from inventory.models import StockBalance

    balance = StockBalance.objects.filter(
        product=product, warehouse=warehouse
    ).first()
    return balance.quantity if balance else Decimal("0.000")
