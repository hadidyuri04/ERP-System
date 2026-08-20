from datetime import timedelta
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
from customers.models import Customer

from .models import Quotation
from .services import convert_quotation_to_pos_sale, create_quotation

User = get_user_model()


class QuotationTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="seller", email="s@example.com", password="x"
        )
        self.today = timezone.now().date()

        seed_finance(self.user, self.today)
        self.tax_rate = seed_tax_rate("16.000")
        catalogue = seed_catalogue(tax_rate=self.tax_rate)
        self.product = catalogue["product"]
        self.warehouse = catalogue["warehouse"]

        self.customer = Customer.objects.create(code="C1", name="Abu Ahmad")

    def make_quotation(self, quantity="10", expiry_in_days=7, discount="0.000"):
        quantity = Decimal(quantity)
        discount = Decimal(discount)
        gross = quantity * self.product.selling_price
        tax = self.product.tax_for(gross - discount)

        return create_quotation(
            customer=self.customer,
            date=self.today,
            expiry_date=self.today + timedelta(days=expiry_in_days),
            items_data=[{
                "product": self.product,
                "quantity": quantity,
                "unit_price": self.product.selling_price,
                "discount_amount": discount,
                "tax_amount": tax,
            }],
            user=self.user,
            discount_amount=Decimal("0.000"),
            tax_amount=tax,
            notes="",
        )


class QuotationCreationTests(QuotationTestBase):
    def test_a_quotation_totals_its_lines(self):
        quotation = self.make_quotation(quantity="10")

        # 10 x 0.350 = 3.500 goods, plus 16% = 0.560
        self.assertEqual(quotation.subtotal, Decimal("3.500"))
        self.assertEqual(quotation.tax_amount, Decimal("0.560"))
        self.assertEqual(quotation.total, Decimal("4.060"))

    def test_subtotal_is_goods_only_and_excludes_tax(self):
        """subtotal used to include tax, which was then added again."""
        quotation = self.make_quotation(quantity="5")

        self.assertEqual(quotation.subtotal, Decimal("1.750"))
        self.assertEqual(
            quotation.total,
            quotation.subtotal - quotation.discount_amount + quotation.tax_amount,
        )

    def test_a_header_discount_cannot_push_the_total_negative(self):
        with self.assertRaises(ValidationError):
            create_quotation(
                customer=self.customer,
                date=self.today,
                expiry_date=self.today + timedelta(days=7),
                items_data=[{
                    "product": self.product,
                    "quantity": Decimal("5"),
                    "unit_price": Decimal("0.400"),
                    "discount_amount": Decimal("0.100"),
                    "tax_amount": Decimal("0.304"),
                }],
                user=self.user,
                discount_amount=Decimal("4.000"),
                notes="",
            )

    def test_a_total_is_never_negative(self):
        quotation = self.make_quotation(quantity="10", discount="0.500")

        self.assertGreater(quotation.total, Decimal("0"))

    def test_a_quotation_does_not_touch_stock(self):
        """Spec 8: a quotation affects neither stock nor accounting."""
        give_stock(self.product, self.warehouse, "100.000")
        before = stock_of(self.product, self.warehouse)

        self.make_quotation(quantity="10")

        self.assertEqual(stock_of(self.product, self.warehouse), before)

    def test_two_quotations_in_the_same_second_both_succeed(self):
        first = self.make_quotation()
        second = self.make_quotation()

        self.assertNotEqual(first.quotation_number, second.quotation_number)


class QuotationConversionTests(QuotationTestBase):
    def setUp(self):
        super().setUp()
        give_stock(self.product, self.warehouse, "100.000")
        self.session = self._open_session()

    def _open_session(self):
        from pos.models import POSSession

        return POSSession.objects.create(
            session_number="SESS-Q1",
            cashier=self.user,
            warehouse=self.warehouse,
            opening_balance=Decimal("0.000"),
        )

    def _convert(self, quotation):
        return convert_quotation_to_pos_sale(
            quotation_id=quotation.id,
            warehouse=self.warehouse,
            cashier=self.user,
            session=self.session,
            payments_data=[{
                "payment_method": "cash",
                "amount": quotation.total,
            }],
        )

    def test_converting_reduces_stock(self):
        quotation = self.make_quotation(quantity="10")
        before = stock_of(self.product, self.warehouse)

        self._convert(quotation)

        self.assertEqual(
            stock_of(self.product, self.warehouse), before - Decimal("10")
        )

    def test_converting_marks_the_quotation_accepted(self):
        quotation = self.make_quotation(quantity="5")
        self._convert(quotation)
        quotation.refresh_from_db()

        self.assertEqual(quotation.status, Quotation.Status.ACCEPTED)

    def test_an_expired_quotation_cannot_be_converted(self):
        quotation = self.make_quotation(quantity="5", expiry_in_days=-1)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

    def test_an_expired_quotation_is_marked_expired(self):
        quotation = self.make_quotation(quantity="5", expiry_in_days=-1)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.Status.EXPIRED)

    def test_a_quotation_cannot_be_converted_twice(self):
        """Converting again would sell the same order and take stock twice."""
        quotation = self.make_quotation(quantity="5")
        self._convert(quotation)
        after_first = stock_of(self.product, self.warehouse)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

        self.assertEqual(stock_of(self.product, self.warehouse), after_first)

    def test_a_rejected_quotation_cannot_be_converted(self):
        quotation = self.make_quotation(quantity="5")
        quotation.status = Quotation.Status.REJECTED
        quotation.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self._convert(quotation)

    def test_a_failed_conversion_leaves_stock_untouched(self):
        quotation = self.make_quotation(quantity="5", expiry_in_days=-1)
        before = stock_of(self.product, self.warehouse)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

        self.assertEqual(stock_of(self.product, self.warehouse), before)


class QuotationViewTests(QuotationTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_list_and_create_pages_render(self):
        self.assertEqual(self.client.get("/quotations/").status_code, 200)
        self.assertEqual(self.client.get("/quotations/create/").status_code, 200)

    def test_detail_page_renders(self):
        quotation = self.make_quotation()

        self.assertEqual(
            self.client.get(f"/quotations/{quotation.pk}/").status_code, 200
        )
