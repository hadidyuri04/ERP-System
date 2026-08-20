from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.test_utils import seed_catalogue, seed_finance, seed_tax_rate, stock_of
from finance.models import JournalEntry, JournalEntryLine
from suppliers.models import Supplier

from .models import PurchaseInvoice
from .services import confirm_purchase

User = get_user_model()


class PurchaseTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="buyer", email="b@example.com", password="x"
        )
        self.today = timezone.now().date()

        seed_finance(self.user, self.today)
        self.tax_rate = seed_tax_rate("16.000")
        catalogue = seed_catalogue(tax_rate=self.tax_rate)
        self.product = catalogue["product"]
        self.warehouse = catalogue["warehouse"]

        self.supplier = Supplier.objects.create(code="S1", name="Jordan Dairy")

    def make_invoice(self, *, quantity="200", unit_cost="0.400",
                     payment_type="cash", paid=None, expenses="0.000",
                     number=None):
        """Build a draft invoice with totals already worked out, as the view does."""
        quantity = Decimal(quantity)
        unit_cost = Decimal(unit_cost)
        expenses = Decimal(expenses)

        subtotal = quantity * unit_cost
        tax = self.product.tax_for(subtotal)
        total = subtotal + tax + expenses

        invoice = PurchaseInvoice.objects.create(
            invoice_number=number or f"PI-{timezone.now().timestamp()}",
            supplier=self.supplier,
            warehouse=self.warehouse,
            invoice_date=self.today,
            payment_type=payment_type,
            subtotal=subtotal,
            discount_amount=Decimal("0.000"),
            tax_amount=tax,
            additional_expenses=expenses,
            total=total,
            paid_amount=total if paid is None else Decimal(paid),
            created_by=self.user,
        )
        invoice.items.create(
            product=self.product,
            quantity=quantity,
            unit_cost=unit_cost,
            discount_amount=Decimal("0.000"),
            tax_amount=tax,
            line_total=subtotal + tax,
        )
        return invoice


class PurchaseConfirmationTests(PurchaseTestBase):
    def test_tax_is_taken_from_the_product_rate(self):
        # 200 x 0.400 = 80.000, at 16% that is 12.800
        self.assertEqual(self.product.tax_for(Decimal("80.000")), Decimal("12.800"))

    def test_confirming_receives_the_stock(self):
        invoice = self.make_invoice()
        confirm_purchase(invoice.id, self.user)

        self.assertEqual(stock_of(self.product, self.warehouse), Decimal("200.000"))

    def test_confirming_posts_a_balanced_journal_entry(self):
        invoice = self.make_invoice(expenses="12.000")
        confirm_purchase(invoice.id, self.user)

        journal = JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.PURCHASE, source_id=invoice.id
        )
        lines = JournalEntryLine.objects.filter(journal_entry=journal)
        debits = sum(line.debit for line in lines)
        credits = sum(line.credit for line in lines)

        self.assertEqual(debits, credits)
        self.assertEqual(debits, invoice.total)

    def test_confirming_marks_the_invoice_confirmed(self):
        invoice = self.make_invoice()
        confirm_purchase(invoice.id, self.user)
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, PurchaseInvoice.Status.CONFIRMED)

    def test_a_confirmed_invoice_cannot_be_confirmed_twice(self):
        invoice = self.make_invoice()
        confirm_purchase(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            confirm_purchase(invoice.id, self.user)


class PurchasePaymentRuleTests(PurchaseTestBase):
    """A cash invoice must be paid in full; a credit invoice must be unpaid."""

    def test_underpaid_cash_invoice_is_refused(self):
        invoice = self.make_invoice(payment_type="cash", paid="10.000")

        with self.assertRaises(ValidationError):
            confirm_purchase(invoice.id, self.user)

    def test_overpaid_cash_invoice_is_refused(self):
        invoice = self.make_invoice(payment_type="cash", paid="99999.000")

        with self.assertRaises(ValidationError):
            confirm_purchase(invoice.id, self.user)

    def test_cash_invoice_paid_in_full_posts(self):
        invoice = self.make_invoice(payment_type="cash")
        confirm_purchase(invoice.id, self.user)
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, PurchaseInvoice.Status.CONFIRMED)

    def test_credit_invoice_carrying_a_payment_is_refused(self):
        invoice = self.make_invoice(payment_type="credit", paid="5.000")

        with self.assertRaises(ValidationError):
            confirm_purchase(invoice.id, self.user)

    def test_credit_invoice_with_nothing_paid_posts_to_payables(self):
        invoice = self.make_invoice(payment_type="credit", paid="0.000")
        confirm_purchase(invoice.id, self.user)

        journal = JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.PURCHASE, source_id=invoice.id
        )
        payable_credit = JournalEntryLine.objects.filter(
            journal_entry=journal, account__code="2100"
        ).first()

        self.assertIsNotNone(payable_credit)
        self.assertEqual(payable_credit.credit, invoice.total)

    def test_a_failed_confirmation_leaves_stock_untouched(self):
        invoice = self.make_invoice(payment_type="cash", paid="10.000")
        before = stock_of(self.product, self.warehouse)

        with self.assertRaises(ValidationError):
            confirm_purchase(invoice.id, self.user)

        self.assertEqual(stock_of(self.product, self.warehouse), before)


class PurchaseViewTests(PurchaseTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_list_and_create_pages_render(self):
        self.assertEqual(self.client.get("/purchasing/").status_code, 200)
        self.assertEqual(self.client.get("/purchasing/create/").status_code, 200)

    def test_a_draft_can_be_edited(self):
        invoice = self.make_invoice()
        response = self.client.get(f"/purchasing/{invoice.pk}/edit/")

        self.assertEqual(response.status_code, 200)

    def test_a_confirmed_invoice_cannot_be_edited(self):
        invoice = self.make_invoice()
        confirm_purchase(invoice.id, self.user)

        response = self.client.get(f"/purchasing/{invoice.pk}/edit/")

        # Redirected back to the detail page rather than shown the form.
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(invoice.pk), response["Location"])
