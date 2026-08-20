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
from sales.services import create_invoice_from_quotation

from .models import Quotation
from .services import create_quotation

User = get_user_model()


class QuotationTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="seller", email="s@example.com", password="x"
        )
        self.today = timezone.localdate()

        seed_finance(self.user, self.today)
        self.tax_rate = seed_tax_rate("16.000")
        catalogue = seed_catalogue(tax_rate=self.tax_rate)
        self.product = catalogue["product"]
        self.warehouse = catalogue["warehouse"]

        self.customer = Customer.objects.create(code="C1", name="Abu Ahmad")

    def make_quotation(self, quantity="10", expiry_in_days=7,
                       line_discount="0.000", header_discount="0.000"):
        quantity = Decimal(quantity)
        line_discount = Decimal(line_discount)
        gross = quantity * self.product.selling_price
        tax = self.product.tax_for(gross - line_discount)

        return create_quotation(
            customer=self.customer,
            date=self.today,
            expiry_date=self.today + timedelta(days=expiry_in_days),
            items_data=[{
                "product": self.product,
                "quantity": quantity,
                "unit_price": self.product.selling_price,
                "discount_amount": line_discount,
                "tax_amount": tax,
            }],
            user=self.user,
            discount_amount=Decimal(header_discount),
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
        """subtotal used to include tax, which was then added a second time."""
        quotation = self.make_quotation(quantity="5")

        self.assertEqual(quotation.subtotal, Decimal("1.750"))
        self.assertEqual(
            quotation.total,
            quotation.subtotal - quotation.discount_amount + quotation.tax_amount,
        )

    def test_a_header_discount_cannot_push_the_total_negative(self):
        with self.assertRaises(ValidationError):
            self.make_quotation(quantity="5", header_discount="400.000")

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
    """Quotations now convert into a draft SalesInvoice, not a POS sale."""

    def setUp(self):
        super().setUp()
        give_stock(self.product, self.warehouse, "100.000")

    def _convert(self, quotation, due_in_days=30):
        return create_invoice_from_quotation(
            quotation_id=quotation.id,
            warehouse=self.warehouse,
            invoice_date=self.today,
            due_date=self.today + timedelta(days=due_in_days),
            payment_type="credit",
            user=self.user,
        )

    def test_converting_creates_an_invoice_carrying_the_totals(self):
        quotation = self.make_quotation(quantity="10")
        invoice = self._convert(quotation)

        self.assertEqual(invoice.total, quotation.total)
        self.assertEqual(invoice.customer, self.customer)

    def test_converting_does_not_move_stock_yet(self):
        """The invoice starts as a draft; stock moves when it is confirmed."""
        quotation = self.make_quotation(quantity="10")
        before = stock_of(self.product, self.warehouse)

        self._convert(quotation)

        self.assertEqual(stock_of(self.product, self.warehouse), before)

    def test_a_quotation_cannot_be_converted_twice(self):
        quotation = self.make_quotation(quantity="5")
        self._convert(quotation)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

    def test_an_expired_quotation_cannot_be_converted(self):
        quotation = self.make_quotation(quantity="5", expiry_in_days=-1)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

    def test_an_expired_quotation_is_marked_expired(self):
        """The marking must survive the exception, not roll back with it."""
        quotation = self.make_quotation(quantity="5", expiry_in_days=-1)

        with self.assertRaises(ValidationError):
            self._convert(quotation)

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.Status.EXPIRED)

    def test_a_rejected_quotation_cannot_be_converted(self):
        quotation = self.make_quotation(quantity="5")
        quotation.status = Quotation.Status.REJECTED
        quotation.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            self._convert(quotation)

    def test_a_due_date_before_the_invoice_date_is_refused(self):
        quotation = self.make_quotation(quantity="5")

        with self.assertRaises(ValidationError):
            self._convert(quotation, due_in_days=-5)


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

    def test_convert_view_reports_a_bad_date_instead_of_crashing(self):
        """The view parses dates with timezone.datetime, which must be imported."""
        quotation = self.make_quotation()

        response = self.client.post(
            f"/quotations/{quotation.pk}/convert/",
            {"warehouse": self.warehouse.pk, "invoice_date": "", "due_date": ""},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
