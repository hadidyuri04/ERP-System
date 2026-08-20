from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.db.models import Sum

from customers.models import Customer
from finance.models import Account, FiscalPeriod, JournalEntry, OpenItem, PeriodStatus
from finance.services import create_fiscal_year, get_unfinished_document_counts
from inventory.models import Category, Product, StockBalance, StockBatch, Unit, Warehouse
from quotations.models import Quotation, QuotationItem

from .models import SalesInvoice, SalesInvoiceItem
from .services import (
    confirm_sales_invoice,
    create_and_post_full_credit_note,
    create_invoice_from_quotation,
    record_invoice_payment,
)


class SalesTestBase(TestCase):
    """Shared fixture: accounts, fiscal year, customer, warehouse, stock."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sales-accountant", password="test-password", role="accountant"
        )
        self.today = date.today()
        create_fiscal_year(self.today.year)
        specs = [
            ("1100", "Cash", Account.AccountType.ASSET, True),
            ("1200", "Bank", Account.AccountType.ASSET, True),
            ("1300", "Accounts Receivable", Account.AccountType.ASSET, False),
            ("1400", "Inventory", Account.AccountType.ASSET, False),
            ("2200", "Sales Tax Payable", Account.AccountType.LIABILITY, False),
            ("4100", "Sales Revenue", Account.AccountType.REVENUE, False),
            ("5100", "Cost of Goods Sold", Account.AccountType.EXPENSE, False),
        ]
        self.accounts = {
            code: Account.objects.create(
                code=code, name=name, account_type=kind, is_cash_equivalent=cash
            )
            for code, name, kind, cash in specs
        }
        self.customer = Customer.objects.create(code="C-1", name="Customer One")
        self.warehouse = Warehouse.objects.create(code="WH-1", name="Main")
        category = Category.objects.create(code="CAT", name_en="Goods", name_ar="سلع")
        unit = Unit.objects.create(name_en="Piece", name_ar="قطعة", symbol="pc")
        self.product = Product.objects.create(
            code="P-1", name_en="Product", name_ar="منتج", category=category,
            unit=unit, purchase_price=Decimal("6"), selling_price=Decimal("10"),
        )
        self.batch = StockBatch.objects.create(
            product=self.product, warehouse=self.warehouse, batch_number="B-1",
            received_date=self.today, unit_cost=Decimal("6"),
            quantity_received=Decimal("20"), quantity_remaining=Decimal("20"),
        )
        StockBalance.objects.create(
            product=self.product, warehouse=self.warehouse, quantity=Decimal("20")
        )

    def make_invoice(self, payment_type=SalesInvoice.PaymentType.CREDIT):
        invoice = SalesInvoice.objects.create(
            invoice_number=f"SI-TEST-{SalesInvoice.objects.count() + 1}",
            customer=self.customer, warehouse=self.warehouse,
            invoice_date=self.today, due_date=self.today + timedelta(days=30),
            payment_type=payment_type,
            payment_account=self.accounts["1100"] if payment_type == "cash" else None,
            subtotal=Decimal("20"), total=Decimal("20"), created_by=self.user,
        )
        SalesInvoiceItem.objects.create(
            invoice=invoice, product=self.product, quantity=Decimal("2"),
            unit_price=Decimal("10"), line_total=Decimal("20"),
        )
        return invoice

class SalesInvoiceWorkflowTests(SalesTestBase):
    def test_draft_has_no_effect_and_blocks_period_close(self):
        invoice = self.make_invoice()
        self.assertEqual(JournalEntry.objects.filter(source_id=invoice.id).count(), 0)
        self.assertEqual(StockBalance.objects.get().quantity, Decimal("20"))
        self.assertEqual(
            get_unfinished_document_counts(self.today, self.today)["sales_invoices"], 1
        )

    def test_confirm_posts_balanced_journal_stock_and_receivable(self):
        invoice = self.make_invoice()
        _, journal, receipt = confirm_sales_invoice(invoice.id, self.user)
        invoice.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertIsNone(receipt)
        self.assertEqual(invoice.status, SalesInvoice.Status.POSTED)
        self.assertEqual(self.batch.quantity_remaining, Decimal("18"))
        self.assertEqual(journal.source_type, JournalEntry.SourceType.SALES_INVOICE)
        totals = journal.lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        self.assertEqual(totals["debit"], totals["credit"])
        self.assertEqual(totals["debit"], Decimal("32"))
        self.assertTrue(OpenItem.objects.filter(customer=self.customer).exists())
        self.assertEqual(invoice.outstanding_amount, Decimal("20"))

    def test_partial_then_full_payment(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)
        record_invoice_payment(
            invoice.id, self.user, payment_date=self.today,
            account=self.accounts["1100"], amount=Decimal("7"), payment_method="cash",
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.paid_amount, Decimal("7"))
        self.assertEqual(invoice.payment_status_code, "partially_paid")
        record_invoice_payment(
            invoice.id, self.user, payment_date=self.today,
            account=self.accounts["1200"], amount=Decimal("13"), payment_method="bank_transfer",
        )
        self.assertEqual(invoice.outstanding_amount, Decimal("0"))
        self.assertEqual(invoice.payment_status_code, "paid")

    def test_cash_invoice_is_paid_immediately(self):
        invoice = self.make_invoice(SalesInvoice.PaymentType.CASH)
        _, _, receipt = confirm_sales_invoice(invoice.id, self.user)
        self.assertIsNotNone(receipt)
        self.assertEqual(invoice.outstanding_amount, Decimal("0"))

    def test_closed_period_rejects_confirmation_without_changing_stock(self):
        invoice = self.make_invoice()
        FiscalPeriod.objects.filter(
            fiscal_year__year=self.today.year, month=self.today.month
        ).update(status=PeriodStatus.CLOSED)
        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)
        invoice.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(invoice.status, SalesInvoice.Status.DRAFT)
        self.assertEqual(self.batch.quantity_remaining, Decimal("20"))
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.SALES_INVOICE,
                source_id=invoice.id,
            ).exists()
        )

    def test_full_credit_note_restores_stock_and_receivable(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)
        note, journal = create_and_post_full_credit_note(
            invoice.id, self.user, note_date=self.today, reason="Customer return"
        )
        invoice.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(invoice.status, SalesInvoice.Status.CREDITED)
        self.assertEqual(self.batch.quantity_remaining, Decimal("20"))
        self.assertEqual(invoice.outstanding_amount, Decimal("0"))
        self.assertEqual(journal.source_id, note.id)

    def test_paid_invoice_cannot_be_credited(self):
        invoice = self.make_invoice(SalesInvoice.PaymentType.CASH)
        confirm_sales_invoice(invoice.id, self.user)
        with self.assertRaises(ValidationError):
            create_and_post_full_credit_note(
                invoice.id, self.user, note_date=self.today, reason="Return"
            )

    def test_quotation_converts_to_draft_only_once(self):
        quotation = Quotation.objects.create(
            quotation_number="QT-1", customer=self.customer, date=self.today,
            expiry_date=self.today + timedelta(days=5), subtotal=Decimal("20"),
            total=Decimal("20"), created_by=self.user,
        )
        QuotationItem.objects.create(
            quotation=quotation, product=self.product, quantity=Decimal("2"),
            unit_price=Decimal("10"), line_total=Decimal("20"),
        )
        invoice = create_invoice_from_quotation(
            quotation.id, self.warehouse, self.today, self.today + timedelta(days=30),
            SalesInvoice.PaymentType.CREDIT, self.user,
        )
        quotation.refresh_from_db()
        self.assertEqual(invoice.status, SalesInvoice.Status.DRAFT)
        self.assertEqual(quotation.status, Quotation.Status.CONVERTED)
        with self.assertRaises(ValidationError):
            create_invoice_from_quotation(
                quotation.id, self.warehouse, self.today, self.today,
                SalesInvoice.PaymentType.CREDIT, self.user,
            )

    def test_pages_and_excel_export_render(self):
        invoice = self.make_invoice()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("sales:invoice_list")).status_code, 200)
        detail_url = reverse("sales:invoice_detail", args=[invoice.id])
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertEqual(self.client.get(detail_url + "?export=xlsx").status_code, 200)


class SalesInvoiceGuardTests(SalesTestBase):
    """
    The failure paths. His tests cover the happy ones; every bug found today
    lived in what happens when something is wrong.
    """

    def stock(self):
        return StockBalance.objects.get(
            product=self.product, warehouse=self.warehouse
        ).quantity

    # ---- confirming ----

    def test_confirming_twice_is_refused(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

    def test_confirming_twice_does_not_take_stock_twice(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)
        after_first = self.stock()

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

        self.assertEqual(self.stock(), after_first)

    def test_an_invoice_with_no_items_is_refused(self):
        invoice = self.make_invoice()
        invoice.items.all().delete()

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

    def test_a_non_sellable_product_is_refused(self):
        invoice = self.make_invoice()
        self.product.is_sellable = False
        self.product.save(update_fields=["is_sellable"])

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

    def test_more_than_available_is_refused_and_stock_is_untouched(self):
        invoice = self.make_invoice()
        item = invoice.items.first()
        item.quantity = Decimal("999")
        item.save(update_fields=["quantity"])
        before = self.stock()

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

        self.assertEqual(self.stock(), before)

    def test_a_cash_invoice_without_an_account_is_refused(self):
        invoice = self.make_invoice(payment_type=SalesInvoice.PaymentType.CASH)
        invoice.payment_account = None
        invoice.save(update_fields=["payment_account"])

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

    def test_an_expired_batch_is_not_sold(self):
        """Spec 5.6: FEFO must skip expired stock."""
        StockBatch.objects.all().update(
            expiration_date=self.today - timedelta(days=1)
        )
        invoice = self.make_invoice()

        with self.assertRaises(ValidationError):
            confirm_sales_invoice(invoice.id, self.user)

    def test_the_sale_draws_from_the_batch_expiring_soonest(self):
        self.batch.expiration_date = self.today + timedelta(days=90)
        self.batch.save(update_fields=["expiration_date"])
        soonest = StockBatch.objects.create(
            product=self.product, warehouse=self.warehouse, batch_number="B-SOON",
            received_date=self.today, expiration_date=self.today + timedelta(days=5),
            unit_cost=Decimal("6"), quantity_received=Decimal("10"),
            quantity_remaining=Decimal("10"),
        )
        balance = StockBalance.objects.get(
            product=self.product, warehouse=self.warehouse
        )
        balance.quantity += Decimal("10")
        balance.save(update_fields=["quantity"])

        confirm_sales_invoice(self.make_invoice().id, self.user)

        soonest.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual(soonest.quantity_remaining, Decimal("8"))
        self.assertEqual(self.batch.quantity_remaining, Decimal("20"))

    # ---- payments ----

    def test_a_payment_on_a_draft_is_refused(self):
        invoice = self.make_invoice()

        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice.id, self.user, payment_date=self.today,
                account=self.accounts["1100"], amount=Decimal("5"),
                payment_method="cash",
            )

    def test_a_payment_larger_than_the_balance_is_refused(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice.id, self.user, payment_date=self.today,
                account=self.accounts["1100"], amount=Decimal("9999"),
                payment_method="cash",
            )

    def test_a_zero_payment_is_refused(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice.id, self.user, payment_date=self.today,
                account=self.accounts["1100"], amount=Decimal("0"),
                payment_method="cash",
            )

    def test_a_payment_dated_before_the_invoice_is_refused(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice.id, self.user,
                payment_date=self.today - timedelta(days=1),
                account=self.accounts["1100"], amount=Decimal("5"),
                payment_method="cash",
            )

    def test_a_payment_to_a_revenue_account_is_refused(self):
        """Money can only land in a cash or bank account."""
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            record_invoice_payment(
                invoice.id, self.user, payment_date=self.today,
                account=self.accounts["4100"], amount=Decimal("5"),
                payment_method="cash",
            )

    # ---- cancelling ----

    def test_a_draft_can_be_cancelled_without_touching_stock(self):
        from .services import cancel_draft_invoice

        invoice = self.make_invoice()
        before = self.stock()
        cancel_draft_invoice(invoice.id, self.user, "Customer changed their mind")
        invoice.refresh_from_db()

        self.assertEqual(invoice.status, SalesInvoice.Status.CANCELLED)
        self.assertEqual(self.stock(), before)

    def test_cancelling_without_a_reason_is_refused(self):
        from .services import cancel_draft_invoice

        invoice = self.make_invoice()

        with self.assertRaises(ValidationError):
            cancel_draft_invoice(invoice.id, self.user, "   ")

    def test_a_posted_invoice_cannot_be_cancelled_directly(self):
        from .services import cancel_draft_invoice

        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            cancel_draft_invoice(invoice.id, self.user, "Mistake")

    # ---- credit notes ----

    def test_a_draft_cannot_be_credited(self):
        invoice = self.make_invoice()

        with self.assertRaises(ValidationError):
            create_and_post_full_credit_note(
                invoice.id, self.user, note_date=self.today, reason="Returned"
            )

    def test_a_credit_note_without_a_reason_is_refused(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)

        with self.assertRaises(ValidationError):
            create_and_post_full_credit_note(
                invoice.id, self.user, note_date=self.today, reason="  "
            )

    def test_an_invoice_cannot_be_credited_twice(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)
        create_and_post_full_credit_note(
            invoice.id, self.user, note_date=self.today, reason="Returned"
        )

        with self.assertRaises(ValidationError):
            create_and_post_full_credit_note(
                invoice.id, self.user, note_date=self.today, reason="Again"
            )

    def test_crediting_twice_does_not_restore_stock_twice(self):
        invoice = self.make_invoice()
        confirm_sales_invoice(invoice.id, self.user)
        create_and_post_full_credit_note(
            invoice.id, self.user, note_date=self.today, reason="Returned"
        )
        after_credit = self.stock()

        with self.assertRaises(ValidationError):
            create_and_post_full_credit_note(
                invoice.id, self.user, note_date=self.today, reason="Again"
            )

        self.assertEqual(self.stock(), after_credit)

    # ---- numbering ----

    def test_invoice_numbers_do_not_collide(self):
        from .services import generate_invoice_number

        numbers = {generate_invoice_number() for _ in range(5)}
        self.assertEqual(len(numbers), 5)
