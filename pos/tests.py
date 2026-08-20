from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.test_utils import (
    give_stock,
    seed_catalogue,
    seed_finance,
    seed_tax_rate,
    stock_of,
)
from finance.models import JournalEntry, JournalEntryLine

from .models import POSSale, POSSession
from .services import close_pos_session, complete_sale

User = get_user_model()


class POSTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="cashier", email="c@example.com", password="x"
        )
        self.today = timezone.now().date()

        seed_finance(self.user, self.today)
        self.tax_rate = seed_tax_rate("16.000")
        catalogue = seed_catalogue(tax_rate=self.tax_rate)
        self.product = catalogue["product"]
        self.warehouse = catalogue["warehouse"]

        give_stock(self.product, self.warehouse, "100.000")

        self.session = POSSession.objects.create(
            session_number="SESS-1",
            cashier=self.user,
            warehouse=self.warehouse,
            opening_balance=Decimal("0.000"),
        )

    def sell(self, quantity="1", unit_price=None, discount="0.000", paid=None):
        quantity = Decimal(quantity)
        unit_price = Decimal(unit_price or self.product.selling_price)
        discount = Decimal(discount)

        goods = quantity * unit_price - discount
        tax = self.product.tax_for(goods)
        total = goods + tax if paid is None else Decimal(paid)

        return complete_sale(
            warehouse=self.warehouse,
            cashier=self.user,
            session=self.session,
            customer=None,
            items_data=[{
                "product": self.product,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_amount": discount,
            }],
            payments_data=[{"payment_method": "cash", "amount": total}],
            notes="",
        )


class POSSaleTests(POSTestBase):
    def test_tax_is_added_from_the_product_rate(self):
        sale = self.sell(quantity="1")

        self.assertEqual(sale.subtotal, Decimal("0.350"))
        self.assertEqual(sale.tax_amount, Decimal("0.056"))
        self.assertEqual(sale.total, Decimal("0.406"))

    def test_subtotal_excludes_tax(self):
        """Subtotal is the goods value; tax and total are separate figures."""
        sale = self.sell(quantity="10")

        self.assertEqual(sale.subtotal, Decimal("3.500"))
        self.assertEqual(sale.total, sale.subtotal - sale.discount_amount + sale.tax_amount)

    def test_selling_reduces_stock(self):
        before = stock_of(self.product, self.warehouse)
        self.sell(quantity="3")

        self.assertEqual(stock_of(self.product, self.warehouse), before - Decimal("3"))

    def test_a_sale_does_not_post_on_its_own(self):
        """Sales are posted as one journal when the register closes, not per sale."""
        sale = self.sell(quantity="2")

        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.POS_SALE, source_id=sale.id
            ).exists()
        )

    def test_closing_the_register_posts_one_balanced_journal(self):
        self.sell(quantity="2")
        self.sell(quantity="3")

        session, journal = close_pos_session(
            self.session.id, self.user, actual_cash=self._expected_cash()
        )
        lines = JournalEntryLine.objects.filter(journal_entry=journal)

        self.assertEqual(
            sum(line.debit for line in lines),
            sum(line.credit for line in lines),
        )
        self.assertEqual(session.status, POSSession.SessionStatus.CLOSED)

    def _expected_cash(self):
        """Opening float plus cash taken, minus change given."""
        from django.db.models import Sum

        from .models import POSPayment

        taken = POSPayment.objects.filter(
            sale__session=self.session,
            sale__status=POSSale.SaleStatus.COMPLETED,
            payment_method=POSPayment.PaymentMethod.CASH,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.000")

        change = self.session.sales.filter(
            status=POSSale.SaleStatus.COMPLETED
        ).aggregate(total=Sum("change_amount"))["total"] or Decimal("0.000")

        return self.session.opening_balance + taken - change

    def test_a_discount_reduces_the_tax_too(self):
        """Tax is charged on what is actually paid, so a discount lowers it."""
        undiscounted = self.product.tax_for(Decimal("10") * self.product.selling_price)
        discounted = self.product.tax_for(
            Decimal("10") * self.product.selling_price - Decimal("1.000")
        )

        self.assertLess(discounted, undiscounted)


    def test_two_sales_in_the_same_second_both_succeed(self):
        """Sale numbers were second-resolution, so a busy till collided."""
        first = self.sell(quantity="1")
        second = self.sell(quantity="1")

        self.assertNotEqual(first.sale_number, second.sale_number)


class POSGuardTests(POSTestBase):
    def test_selling_more_than_available_is_refused(self):
        with self.assertRaises(ValidationError):
            self.sell(quantity="500")

    def test_an_empty_cart_is_refused(self):
        with self.assertRaises(ValidationError):
            complete_sale(
                warehouse=self.warehouse,
                cashier=self.user,
                session=self.session,
                customer=None,
                items_data=[],
                payments_data=[{"payment_method": "cash", "amount": Decimal("0")}],
                notes="",
            )

    def test_a_non_sellable_product_is_refused(self):
        self.product.is_sellable = False
        self.product.save(update_fields=["is_sellable"])

        with self.assertRaises(ValidationError):
            self.sell(quantity="1")

    def test_a_discount_over_the_product_ceiling_is_refused(self):
        self.product.maximum_discount = Decimal("5.000")   # 5% maximum
        self.product.save(update_fields=["maximum_discount"])

        # 10 x 0.350 = 3.500, and 1.000 off is roughly 28%.
        with self.assertRaises(ValidationError):
            self.sell(quantity="10", discount="1.000")

    def test_a_discount_inside_the_ceiling_is_allowed(self):
        self.product.maximum_discount = Decimal("50.000")
        self.product.save(update_fields=["maximum_discount"])

        sale = self.sell(quantity="10", discount="1.000")

        self.assertEqual(sale.discount_amount, Decimal("1.000"))

    def test_underpaying_is_refused(self):
        with self.assertRaises(ValidationError):
            self.sell(quantity="10", paid="0.100")

    def test_a_refused_sale_leaves_stock_untouched(self):
        before = stock_of(self.product, self.warehouse)

        with self.assertRaises(ValidationError):
            self.sell(quantity="500")

        self.assertEqual(stock_of(self.product, self.warehouse), before)


class POSExpiryTests(POSTestBase):
    def test_expired_stock_cannot_be_sold(self):
        """Spec 5.6: FEFO must skip expired batches."""
        from inventory.models import StockBalance, StockBatch

        # Clear the good stock so only an expired batch remains.
        StockBatch.objects.all().delete()
        StockBalance.objects.filter(product=self.product).update(
            quantity=Decimal("0.000")
        )
        give_stock(
            self.product, self.warehouse, "50.000",
            batch_number="B-OLD", expires_in_days=-5,
        )

        with self.assertRaises(ValidationError):
            self.sell(quantity="1")


class POSViewTests(POSTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_sale_list_renders(self):
        self.assertEqual(self.client.get("/pos/sales/").status_code, 200)

    def test_product_search_finds_by_arabic_name(self):
        """Product.name is a property, so the search must use name_ar/name_en."""
        response = self.client.get(
            "/pos/api/search-product/",
            {"q": "شاي", "warehouse_id": self.warehouse.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_product_search_does_not_offer_non_sellable_items(self):
        self.product.is_sellable = False
        self.product.save(update_fields=["is_sellable"])

        response = self.client.get(
            "/pos/api/search-product/",
            {"q": "شاي", "warehouse_id": self.warehouse.id},
        )

        self.assertEqual(response.status_code, 404)
