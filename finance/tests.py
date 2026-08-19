from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from inventory.models import (
    Category,
    Product,
    StockBalance,
    StockBatch,
    StockMovement,
    Unit,
    Warehouse,
    WasteLoss,
    WasteLossItem,
)
from inventory.services import confirm_waste_loss
from pos.models import POSPayment, POSSale, POSSaleItem
from pos.services import complete_sale
from purchasing.models import PurchaseInvoice, PurchaseInvoiceItem
from purchasing.services import confirm_purchase
from suppliers.models import Supplier

from .models import (
    Account,
    FiscalPeriod,
    FiscalPeriodAction,
    FiscalYear,
    JournalEntry,
    JournalEntryLine,
    PaymentVoucher,
    PeriodStatus,
    ReceiptVoucher,
)
from .reports import generate_balance_sheet, generate_income_statement, get_account_balance
from .services import (
    create_fiscal_year,
    get_period_summary,
    get_posting_account,
    post_journal_entry,
    post_payment_voucher,
    post_pos_sale,
    post_purchase_invoice,
    post_receipt_voucher,
    reverse_journal_entry,
    set_fiscal_year_status,
    set_period_status,
)


def create_required_fiscal_years(*years):
    """Create each calendar year once for tests that post journal entries."""
    for year in set(years):
        create_fiscal_year(year)


class ManualJournalViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="manual-accountant",
            password="test-password",
            role="accountant",
        )
        self.cash = Account.objects.create(
            code="M1100",
            name="Manual cash",
            account_type=Account.AccountType.ASSET,
        )
        self.revenue = Account.objects.create(
            code="M4100",
            name="Manual revenue",
            account_type=Account.AccountType.REVENUE,
        )
        self.customer = Customer.objects.create(code="MC001", name="Manual customer")
        self.supplier = Supplier.objects.create(code="MS001", name="Manual supplier")
        self.client.force_login(self.user)

    def journal_data(self, debit="100.000", credit="100.000"):
        return {
            "entry_number": "MJE-001",
            "date": date.today().isoformat(),
            "description": "Manual journal test",
            "lines-TOTAL_FORMS": "2",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
            "lines-0-account": str(self.cash.pk),
            "lines-0-customer": "",
            "lines-0-supplier": "",
            "lines-0-description": "Cash debit",
            "lines-0-debit": debit,
            "lines-0-credit": "0.000",
            "lines-1-account": str(self.revenue.pk),
            "lines-1-customer": "",
            "lines-1-supplier": "",
            "lines-1-description": "Revenue credit",
            "lines-1-debit": "0.000",
            "lines-1-credit": credit,
        }

    def test_balanced_manual_journal_is_saved_as_draft(self):
        response = self.client.post(reverse("finance:journal_create"), self.journal_data())

        journal = JournalEntry.objects.get(entry_number="MJE-001")
        self.assertRedirects(response, reverse("finance:journal_detail", args=[journal.pk]))
        self.assertEqual(journal.status, JournalEntry.Status.DRAFT)
        self.assertEqual(journal.source_type, JournalEntry.SourceType.MANUAL)
        self.assertEqual(journal.created_by, self.user)
        self.assertEqual(journal.lines.count(), 2)

    def test_unbalanced_manual_journal_is_rejected(self):
        response = self.client.post(
            reverse("finance:journal_create"),
            self.journal_data(credit="90.000"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Journal is not balanced")
        self.assertFalse(JournalEntry.objects.filter(entry_number="MJE-001").exists())

    def test_line_cannot_reference_customer_and_supplier(self):
        data = self.journal_data()
        data["lines-0-customer"] = str(self.customer.pk)
        data["lines-0-supplier"] = str(self.supplier.pk)

        response = self.client.post(reverse("finance:journal_create"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot reference both a customer and a supplier")
        self.assertFalse(JournalEntry.objects.filter(entry_number="MJE-001").exists())

    def test_create_page_contains_dynamic_line_controls(self):
        response = self.client.get(reverse("finance:journal_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="add-journal-line"')
        self.assertContains(response, 'id="journal-empty-line"')


class FinancePostingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="accountant",
            password="test-password",
            role="accountant",
        )
        create_required_fiscal_years(2026, date.today().year)
        self.customer = Customer.objects.create(code="C001", name="Customer")
        self.supplier = Supplier.objects.create(code="S001", name="Supplier")

        account_data = {
            "1100": ("Cash", Account.AccountType.ASSET),
            "1200": ("Bank", Account.AccountType.ASSET),
            "1210": ("Card clearing", Account.AccountType.ASSET),
            "1300": ("Accounts receivable", Account.AccountType.ASSET),
            "1400": ("Inventory", Account.AccountType.ASSET),
            "2100": ("Accounts payable", Account.AccountType.LIABILITY),
            "2200": ("Tax payable", Account.AccountType.LIABILITY),
            "4100": ("Sales revenue", Account.AccountType.REVENUE),
            "5100": ("Cost of goods sold", Account.AccountType.EXPENSE),
            "6300": ("Waste expense", Account.AccountType.EXPENSE),
        }
        self.accounts = {
            code: Account.objects.create(code=code, name=name, account_type=account_type)
            for code, (name, account_type) in account_data.items()
        }

    def close_period_for(self, posting_date):
        period = FiscalPeriod.objects.get(
            fiscal_year__year=posting_date.year,
            month=posting_date.month,
        )
        set_period_status(
            period.pk,
            PeriodStatus.CLOSED,
            self.user,
            reason="Posting workflow validation test",
        )
        return period

    def test_post_journal_entry_records_approver(self):
        journal = JournalEntry.objects.create(
            entry_number="JE-001",
            date=date.today(),
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.accounts["1100"],
            debit=Decimal("25.000"),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.accounts["4100"],
            credit=Decimal("25.000"),
        )

        post_journal_entry(journal.id, self.user)

        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalEntry.Status.POSTED)
        self.assertEqual(journal.approved_by, self.user)

    def test_get_posting_account_rejects_wrong_account_type(self):
        revenue_account = self.accounts["4100"]
        revenue_account.account_type = Account.AccountType.EXPENSE
        revenue_account.save(update_fields=["account_type"])

        with self.assertRaisesMessage(ValidationError, "has the wrong type"):
            get_posting_account(
                "4100",
                Account.AccountType.REVENUE,
            )

    def test_unbalanced_journal_remains_draft(self):
        journal = JournalEntry.objects.create(
            entry_number="JE-002",
            date=date.today(),
            created_by=self.user,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.accounts["1100"],
            debit=Decimal("25.000"),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.accounts["4100"],
            credit=Decimal("20.000"),
        )

        with self.assertRaises(ValidationError):
            post_journal_entry(journal.id, self.user)

        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalEntry.Status.DRAFT)

    def test_receipt_and_payment_vouchers_post_balanced_journals(self):
        receipt = ReceiptVoucher.objects.create(
            voucher_number="001",
            date=date.today(),
            customer=self.customer,
            received_from=self.customer.name,
            account=self.accounts["1100"],
            amount=Decimal("40.000"),
            payment_method=ReceiptVoucher.PaymentMethod.CASH,
            created_by=self.user,
        )
        payment = PaymentVoucher.objects.create(
            voucher_number="001",
            date=date.today(),
            supplier=self.supplier,
            paid_to=self.supplier.name,
            account=self.accounts["1100"],
            amount=Decimal("30.000"),
            payment_method=PaymentVoucher.PaymentMethod.CASH,
            created_by=self.user,
        )

        post_receipt_voucher(receipt.id, self.user)
        post_payment_voucher(payment.id, self.user)

        receipt.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(receipt.status, ReceiptVoucher.Status.CONFIRMED)
        self.assertEqual(payment.status, PaymentVoucher.Status.CONFIRMED)
        self.assertEqual(
            JournalEntry.objects.filter(status=JournalEntry.Status.POSTED).count(),
            2,
        )

    def test_source_can_only_be_posted_once(self):
        JournalEntry.objects.create(
            entry_number="SOURCE-1",
            date=date.today(),
            source_type=JournalEntry.SourceType.RECEIPT,
            source_id=99,
            created_by=self.user,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.create(
                    entry_number="SOURCE-2",
                    date=date.today(),
                    source_type=JournalEntry.SourceType.RECEIPT,
                    source_id=99,
                    created_by=self.user,
                )

    def test_pos_posts_split_revenue_tax_and_cogs(self):
        category = Category.objects.create(
            code="CAT", name_en="Category", name_ar="Category"
        )
        unit = Unit.objects.create(name_en="Piece", name_ar="Piece", symbol="pc")
        warehouse = Warehouse.objects.create(code="WH", name="Warehouse")
        product = Product.objects.create(
            code="P001",
            name_en="Product",
            name_ar="Product",
            category=category,
            unit=unit,
            purchase_price=Decimal("60.000"),
            selling_price=Decimal("100.000"),
        )
        sale = POSSale.objects.create(
            sale_number="SALE-001",
            warehouse=warehouse,
            cashier=self.user,
            status=POSSale.SaleStatus.COMPLETED,
            subtotal=Decimal("115.000"),
            total=Decimal("115.000"),
            paid_amount=Decimal("115.000"),
        )
        POSSaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.000"),
            unit_cost=Decimal("60.000"),
            tax_amount=Decimal("15.000"),
            line_total=Decimal("115.000"),
        )
        POSPayment.objects.create(
            sale=sale,
            payment_method=POSPayment.PaymentMethod.CASH,
            amount=Decimal("115.000"),
            created_by=self.user,
        )

        journal = post_pos_sale(sale.id, self.user)

        amounts = {
            line.account.code: (line.debit, line.credit)
            for line in journal.lines.select_related("account")
        }
        self.assertEqual(amounts["1100"], (Decimal("115.000"), Decimal("0.000")))
        self.assertEqual(amounts["4100"], (Decimal("0.000"), Decimal("100.000")))
        self.assertEqual(amounts["2200"], (Decimal("0.000"), Decimal("15.000")))
        self.assertEqual(amounts["5100"], (Decimal("60.000"), Decimal("0.000")))
        self.assertEqual(amounts["1400"], (Decimal("0.000"), Decimal("60.000")))

        with self.assertRaises(ValidationError):
            post_pos_sale(sale.id, self.user)

    def test_purchase_uses_one_balanced_finance_journal(self):
        category = Category.objects.create(
            code="PCAT", name_en="Purchase Category", name_ar="Purchase Category"
        )
        unit = Unit.objects.create(name_en="Box", name_ar="Box", symbol="box")
        warehouse = Warehouse.objects.create(code="PWH", name="Purchase Warehouse")
        product = Product.objects.create(
            code="PP01",
            name_en="Purchased product",
            name_ar="Purchased product",
            category=category,
            unit=unit,
            purchase_price=Decimal("50.000"),
            selling_price=Decimal("75.000"),
        )
        invoice = PurchaseInvoice.objects.create(
            invoice_number="PI-001",
            supplier=self.supplier,
            warehouse=warehouse,
            invoice_date=date.today(),
            payment_type=PurchaseInvoice.PaymentType.CREDIT,
            status=PurchaseInvoice.Status.CONFIRMED,
            subtotal=Decimal("100.000"),
            total=Decimal("100.000"),
            created_by=self.user,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=invoice,
            product=product,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("50.000"),
            line_total=Decimal("100.000"),
        )

        journal = post_purchase_invoice(invoice.id, self.user)

        self.assertEqual(journal.status, JournalEntry.Status.POSTED)
        self.assertEqual(journal.lines.count(), 2)
        self.assertEqual(
            sum((line.debit for line in journal.lines.all()), Decimal("0.000")),
            sum((line.credit for line in journal.lines.all()), Decimal("0.000")),
        )

    def test_complete_sale_posts_finance_with_actual_batch_cost(self):
        category = Category.objects.create(
            code="SCAT", name_en="Sale Category", name_ar="Sale Category"
        )
        unit = Unit.objects.create(name_en="Unit", name_ar="Unit", symbol="u")
        warehouse = Warehouse.objects.create(code="SWH", name="Sale Warehouse")
        product = Product.objects.create(
            code="SP01",
            name_en="Stock product",
            name_ar="Stock product",
            category=category,
            unit=unit,
            purchase_price=Decimal("30.000"),
            selling_price=Decimal("80.000"),
        )
        StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number="B001",
            received_date=date.today(),
            unit_cost=Decimal("45.000"),
            quantity_received=Decimal("5.000"),
            quantity_remaining=Decimal("5.000"),
        )
        StockBalance.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=Decimal("5.000"),
        )

        sale = complete_sale(
            warehouse=warehouse,
            cashier=self.user,
            items_data=[{
                "product": product,
                "quantity": "2.000",
                "unit_price": "80.000",
            }],
            payments_data=[{
                "payment_method": POSPayment.PaymentMethod.CASH,
                "amount": "160.000",
            }],
        )

        sale_item = sale.items.get()
        journal = JournalEntry.objects.get(
            source_type=JournalEntry.SourceType.POS_SALE,
            source_id=sale.id,
        )
        self.assertEqual(sale_item.unit_cost, Decimal("45.000"))
        self.assertEqual(journal.status, JournalEntry.Status.POSTED)
        self.assertEqual(
            journal.lines.get(account=self.accounts["5100"]).debit,
            Decimal("90.000"),
        )

    def test_confirm_waste_loss_posts_finance_journal(self):
        category = Category.objects.create(
            code="WCAT", name_en="Waste Category", name_ar="Waste Category"
        )
        unit = Unit.objects.create(
            name_en="Waste Unit", name_ar="Waste Unit", symbol="wu"
        )
        warehouse = Warehouse.objects.create(code="WWH", name="Waste Warehouse")
        product = Product.objects.create(
            code="WP01",
            name_en="Waste product",
            name_ar="Waste product",
            category=category,
            unit=unit,
            purchase_price=Decimal("12.000"),
            selling_price=Decimal("20.000"),
        )
        StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number="WB001",
            received_date=date.today(),
            unit_cost=Decimal("12.000"),
            quantity_received=Decimal("2.000"),
            quantity_remaining=Decimal("2.000"),
        )
        StockBalance.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=Decimal("2.000"),
        )
        waste = WasteLoss.objects.create(
            document_number="WST-001",
            warehouse=warehouse,
            date=date.today(),
            reason=WasteLoss.WasteReason.DAMAGED,
            created_by=self.user,
        )
        WasteLossItem.objects.create(
            waste_loss=waste,
            product=product,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("12.000"),
            total_cost=Decimal("24.000"),
        )

        confirmed_waste, journal = confirm_waste_loss(waste.id, self.user)

        self.assertEqual(confirmed_waste.status, WasteLoss.Status.CONFIRMED)
        self.assertEqual(journal.status, JournalEntry.Status.POSTED)
        self.assertEqual(
            journal.lines.get(account=self.accounts["6300"]).debit,
            Decimal("24.000"),
        )
        self.assertEqual(
            journal.lines.get(account=self.accounts["1400"]).credit,
            Decimal("24.000"),
        )

    def test_waste_confirmation_rolls_back_when_finance_posting_fails(self):
        category = Category.objects.create(
            code="RCAT", name_en="Rollback Category", name_ar="Rollback Category"
        )
        unit = Unit.objects.create(
            name_en="Rollback Unit", name_ar="Rollback Unit", symbol="ru"
        )
        warehouse = Warehouse.objects.create(code="RWH", name="Rollback Warehouse")
        product = Product.objects.create(
            code="RP01",
            name_en="Rollback product",
            name_ar="Rollback product",
            category=category,
            unit=unit,
            purchase_price=Decimal("10.000"),
            selling_price=Decimal("15.000"),
        )
        waste = WasteLoss.objects.create(
            document_number="WST-ROLLBACK",
            warehouse=warehouse,
            date=date.today(),
            reason=WasteLoss.WasteReason.DAMAGED,
            created_by=self.user,
        )
        WasteLossItem.objects.create(
            waste_loss=waste,
            product=product,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("10.000"),
            total_cost=Decimal("10.000"),
        )
        self.accounts["6300"].is_active = False
        self.accounts["6300"].save(update_fields=["is_active"])

        with self.assertRaises(ValidationError):
            confirm_waste_loss(waste.id, self.user)

        waste.refresh_from_db()
        self.assertEqual(waste.status, WasteLoss.Status.DRAFT)
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.WASTE,
                source_id=waste.id,
            ).exists()
        )

    def test_closed_period_rejects_receipt_and_rolls_back_journal(self):
        self.close_period_for(date.today())
        receipt = ReceiptVoucher.objects.create(
            voucher_number="CLOSED-RV",
            date=date.today(),
            customer=self.customer,
            received_from=self.customer.name,
            account=self.accounts["1100"],
            amount=Decimal("40.000"),
            payment_method=ReceiptVoucher.PaymentMethod.CASH,
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            post_receipt_voucher(receipt.pk, self.user)

        receipt.refresh_from_db()
        self.assertEqual(receipt.status, ReceiptVoucher.Status.DRAFT)
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.RECEIPT,
                source_id=receipt.pk,
            ).exists()
        )

    def test_closed_period_rejects_payment_and_rolls_back_journal(self):
        self.close_period_for(date.today())
        payment = PaymentVoucher.objects.create(
            voucher_number="CLOSED-PV",
            date=date.today(),
            supplier=self.supplier,
            paid_to=self.supplier.name,
            account=self.accounts["1100"],
            amount=Decimal("30.000"),
            payment_method=PaymentVoucher.PaymentMethod.CASH,
            created_by=self.user,
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            post_payment_voucher(payment.pk, self.user)

        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentVoucher.Status.DRAFT)
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.PAYMENT,
                source_id=payment.pk,
            ).exists()
        )

    def test_closed_period_rejects_purchase_and_rolls_back_inventory(self):
        self.close_period_for(date.today())
        category = Category.objects.create(
            code="CLOSED-PCAT",
            name_en="Closed purchase category",
            name_ar="Closed purchase category",
        )
        unit = Unit.objects.create(
            name_en="Closed purchase unit",
            name_ar="Closed purchase unit",
            symbol="cpu",
        )
        warehouse = Warehouse.objects.create(
            code="CLOSED-PWH",
            name="Closed purchase warehouse",
        )
        product = Product.objects.create(
            code="CLOSED-PP",
            name_en="Closed purchase product",
            name_ar="Closed purchase product",
            category=category,
            unit=unit,
            purchase_price=Decimal("25.000"),
            selling_price=Decimal("40.000"),
        )
        invoice = PurchaseInvoice.objects.create(
            invoice_number="CLOSED-PI",
            supplier=self.supplier,
            warehouse=warehouse,
            invoice_date=date.today(),
            payment_type=PurchaseInvoice.PaymentType.CREDIT,
            subtotal=Decimal("50.000"),
            total=Decimal("50.000"),
            created_by=self.user,
        )
        PurchaseInvoiceItem.objects.create(
            purchase_invoice=invoice,
            product=product,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("25.000"),
            line_total=Decimal("50.000"),
            batch_number="CLOSED-BATCH",
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            confirm_purchase(invoice.pk, self.user)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, PurchaseInvoice.Status.DRAFT)
        self.assertFalse(StockBatch.objects.filter(product=product).exists())
        self.assertFalse(StockBalance.objects.filter(product=product).exists())
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.PURCHASE,
                source_id=invoice.pk,
            ).exists()
        )

    def test_closed_period_rejects_pos_sale_and_restores_stock(self):
        self.close_period_for(date.today())
        category = Category.objects.create(
            code="CLOSED-SCAT",
            name_en="Closed sale category",
            name_ar="Closed sale category",
        )
        unit = Unit.objects.create(
            name_en="Closed sale unit",
            name_ar="Closed sale unit",
            symbol="csu",
        )
        warehouse = Warehouse.objects.create(
            code="CLOSED-SWH",
            name="Closed sale warehouse",
        )
        product = Product.objects.create(
            code="CLOSED-SP",
            name_en="Closed sale product",
            name_ar="Closed sale product",
            category=category,
            unit=unit,
            purchase_price=Decimal("20.000"),
            selling_price=Decimal("35.000"),
        )
        batch = StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number="CLOSED-SALE-BATCH",
            received_date=date.today(),
            unit_cost=Decimal("20.000"),
            quantity_received=Decimal("5.000"),
            quantity_remaining=Decimal("5.000"),
        )
        balance = StockBalance.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=Decimal("5.000"),
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            complete_sale(
                warehouse=warehouse,
                cashier=self.user,
                items_data=[{
                    "product": product,
                    "quantity": "2.000",
                    "unit_price": "35.000",
                }],
                payments_data=[{
                    "payment_method": POSPayment.PaymentMethod.CASH,
                    "amount": "70.000",
                }],
            )

        batch.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal("5.000"))
        self.assertEqual(balance.quantity, Decimal("5.000"))
        self.assertFalse(POSSale.objects.filter(warehouse=warehouse).exists())
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.POS_SALE,
            ).exists()
        )

    def test_closed_period_rejects_waste_and_restores_stock(self):
        self.close_period_for(date.today())
        category = Category.objects.create(
            code="CLOSED-WCAT",
            name_en="Closed waste category",
            name_ar="Closed waste category",
        )
        unit = Unit.objects.create(
            name_en="Closed waste unit",
            name_ar="Closed waste unit",
            symbol="cwu",
        )
        warehouse = Warehouse.objects.create(
            code="CLOSED-WWH",
            name="Closed waste warehouse",
        )
        product = Product.objects.create(
            code="CLOSED-WP",
            name_en="Closed waste product",
            name_ar="Closed waste product",
            category=category,
            unit=unit,
            purchase_price=Decimal("12.000"),
            selling_price=Decimal("18.000"),
        )
        batch = StockBatch.objects.create(
            product=product,
            warehouse=warehouse,
            batch_number="CLOSED-WASTE-BATCH",
            received_date=date.today(),
            unit_cost=Decimal("12.000"),
            quantity_received=Decimal("3.000"),
            quantity_remaining=Decimal("3.000"),
        )
        balance = StockBalance.objects.create(
            product=product,
            warehouse=warehouse,
            quantity=Decimal("3.000"),
        )
        waste = WasteLoss.objects.create(
            document_number="CLOSED-WASTE",
            warehouse=warehouse,
            date=date.today(),
            reason=WasteLoss.WasteReason.DAMAGED,
            created_by=self.user,
        )
        WasteLossItem.objects.create(
            waste_loss=waste,
            product=product,
            batch=batch,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("12.000"),
            total_cost=Decimal("12.000"),
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            confirm_waste_loss(waste.pk, self.user)

        waste.refresh_from_db()
        batch.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(waste.status, WasteLoss.Status.DRAFT)
        self.assertEqual(batch.quantity_remaining, Decimal("3.000"))
        self.assertEqual(balance.quantity, Decimal("3.000"))
        self.assertFalse(StockMovement.objects.filter(product=product).exists())
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.WASTE,
                source_id=waste.pk,
            ).exists()
        )


class JournalReversalTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="reversal-accountant",
            password="test-password",
            role="accountant",
        )
        create_required_fiscal_years(date.today().year)
        self.cash = Account.objects.create(
            code="REV1100",
            name="Reversal cash",
            account_type=Account.AccountType.ASSET,
        )
        self.revenue = Account.objects.create(
            code="REV4100",
            name="Reversal revenue",
            account_type=Account.AccountType.REVENUE,
        )

    def create_journal(self, *, status=JournalEntry.Status.POSTED, source_type=None):
        journal = JournalEntry.objects.create(
            entry_number=f"REV-TEST-{JournalEntry.objects.count() + 1}",
            date=date.today(),
            description="Journal to reverse",
            source_type=source_type or JournalEntry.SourceType.MANUAL,
            status=status,
            created_by=self.accountant,
            approved_by=(
                self.accountant if status == JournalEntry.Status.POSTED else None
            ),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.cash,
            description="Cash debit",
            debit=Decimal("75.000"),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.revenue,
            description="Revenue credit",
            credit=Decimal("75.000"),
        )
        return journal

    def test_reversal_posts_opposite_lines_and_marks_original_reversed(self):
        original = self.create_journal()

        reversal = reverse_journal_entry(
            original.pk,
            self.accountant,
            "Incorrect customer",
        )

        original.refresh_from_db()
        self.assertEqual(original.status, JournalEntry.Status.REVERSED)
        self.assertEqual(original.reversal_reason, "Incorrect customer")
        self.assertEqual(original.reversed_by, self.accountant)
        self.assertIsNotNone(original.reversed_at)

        self.assertEqual(reversal.status, JournalEntry.Status.POSTED)
        self.assertEqual(reversal.source_type, JournalEntry.SourceType.REVERSAL)
        self.assertEqual(reversal.source_id, original.pk)
        self.assertEqual(reversal.reversal_of, original)
        self.assertEqual(reversal.approved_by, self.accountant)
        self.assertEqual(
            reversal.lines.get(account=self.cash).credit,
            Decimal("75.000"),
        )
        self.assertEqual(
            reversal.lines.get(account=self.revenue).debit,
            Decimal("75.000"),
        )
        self.assertEqual(get_account_balance(self.cash), Decimal("0.000"))
        self.assertEqual(get_account_balance(self.revenue), Decimal("0.000"))

    def test_journal_cannot_be_reversed_twice(self):
        original = self.create_journal()
        reverse_journal_entry(original.pk, self.accountant, "First reversal")

        with self.assertRaisesMessage(
            ValidationError,
            "This journal entry has already been reversed.",
        ):
            reverse_journal_entry(original.pk, self.accountant, "Again")

        self.assertEqual(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.REVERSAL,
                source_id=original.pk,
            ).count(),
            1,
        )

    def test_reversal_requires_posted_entry_and_reason(self):
        draft = self.create_journal(status=JournalEntry.Status.DRAFT)
        with self.assertRaisesMessage(
            ValidationError,
            "Only posted journal entries can be reversed.",
        ):
            reverse_journal_entry(draft.pk, self.accountant, "Correction")

        posted = self.create_journal()
        with self.assertRaisesMessage(
            ValidationError,
            "A reversal reason is required.",
        ):
            reverse_journal_entry(posted.pk, self.accountant, None)

    def test_reversal_entry_cannot_be_reversed(self):
        reversal = self.create_journal(
            source_type=JournalEntry.SourceType.REVERSAL,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "A reversal journal entry cannot be reversed.",
        ):
            reverse_journal_entry(reversal.pk, self.accountant, "Undo")

    def test_reversal_view_requires_post_and_accountant_permission(self):
        original = self.create_journal()
        url = reverse("finance:journal_reverse", args=[original.pk])
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(url).status_code, 405)

        cashier = get_user_model().objects.create_user(
            username="reversal-cashier",
            password="test-password",
            role="cashier",
        )
        self.client.force_login(cashier)
        self.assertEqual(
            self.client.post(url, {"reason": "Not allowed"}).status_code,
            403,
        )
        original.refresh_from_db()
        self.assertEqual(original.status, JournalEntry.Status.POSTED)

    def test_reversal_view_redirects_to_created_reversal(self):
        original = self.create_journal()
        self.client.force_login(self.accountant)

        response = self.client.post(
            reverse("finance:journal_reverse", args=[original.pk]),
            {"reason": "Entered twice"},
        )

        reversal = JournalEntry.objects.get(reversal_of=original)
        self.assertRedirects(
            response,
            reverse("finance:journal_detail", args=[reversal.pk]),
        )
        original_response = self.client.get(
            reverse("finance:journal_detail", args=[original.pk])
        )
        self.assertContains(original_response, "Entered twice")
        self.assertContains(original_response, reversal.entry_number)

    def test_closed_period_rejects_reversal_and_preserves_original(self):
        original = self.create_journal()
        period = FiscalPeriod.objects.get(
            fiscal_year__year=date.today().year,
            month=date.today().month,
        )
        set_period_status(
            period.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Month-end close",
        )

        with self.assertRaisesMessage(ValidationError, "period"):
            reverse_journal_entry(
                original.pk,
                self.accountant,
                "Correction after close",
            )

        original.refresh_from_db()
        self.assertEqual(original.status, JournalEntry.Status.POSTED)
        self.assertFalse(
            JournalEntry.objects.filter(
                source_type=JournalEntry.SourceType.REVERSAL,
                source_id=original.pk,
            ).exists()
        )


class IncomeStatementTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="statement-accountant",
            password="test-password",
            role="accountant",
        )
        create_required_fiscal_years(2026, date.today().year)
        self.cash = Account.objects.create(
            code="IS1100",
            name="Statement cash",
            account_type=Account.AccountType.ASSET,
        )
        self.revenue = Account.objects.create(
            code="IS4100",
            name="Statement revenue",
            account_type=Account.AccountType.REVENUE,
        )
        self.expense = Account.objects.create(
            code="IS5100",
            name="Statement expense",
            account_type=Account.AccountType.EXPENSE,
        )

    def create_journal(self, number, journal_date, lines, *, status):
        journal = JournalEntry.objects.create(
            entry_number=number,
            date=journal_date,
            description="Income statement test",
            status=status,
            created_by=self.accountant,
            approved_by=(
                self.accountant if status == JournalEntry.Status.POSTED else None
            ),
        )
        for account, debit, credit in lines:
            JournalEntryLine.objects.create(
                journal_entry=journal,
                account=account,
                debit=debit,
                credit=credit,
            )
        return journal

    def test_income_statement_calculates_profit_and_excludes_drafts(self):
        self.create_journal(
            "IS-SALE",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("100.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("100.000")),
            ],
            status=JournalEntry.Status.POSTED,
        )
        self.create_journal(
            "IS-EXPENSE",
            date(2026, 8, 2),
            [
                (self.expense, Decimal("40.000"), Decimal("0.000")),
                (self.cash, Decimal("0.000"), Decimal("40.000")),
            ],
            status=JournalEntry.Status.POSTED,
        )
        self.create_journal(
            "IS-DRAFT",
            date(2026, 8, 3),
            [
                (self.cash, Decimal("500.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("500.000")),
            ],
            status=JournalEntry.Status.DRAFT,
        )

        statement = generate_income_statement()

        self.assertEqual(statement["total_revenue"], Decimal("100.000"))
        self.assertEqual(statement["total_expenses"], Decimal("40.000"))
        self.assertEqual(statement["net_profit"], Decimal("60.000"))

    def test_income_statement_applies_date_range(self):
        self.create_journal(
            "IS-JULY",
            date(2026, 7, 31),
            [
                (self.cash, Decimal("80.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("80.000")),
            ],
            status=JournalEntry.Status.POSTED,
        )
        self.create_journal(
            "IS-AUGUST",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("25.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("25.000")),
            ],
            status=JournalEntry.Status.POSTED,
        )

        statement = generate_income_statement(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )

        self.assertEqual(statement["total_revenue"], Decimal("25.000"))

    def test_reversal_cancels_income_statement_activity(self):
        original = self.create_journal(
            "IS-REVERSED",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("70.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("70.000")),
            ],
            status=JournalEntry.Status.POSTED,
        )
        reverse_journal_entry(original.pk, self.accountant, "Incorrect sale")

        statement = generate_income_statement()

        self.assertEqual(statement["total_revenue"], Decimal("0.000"))
        self.assertEqual(statement["net_profit"], Decimal("0.000"))
        self.assertEqual(statement["revenue_rows"], [])

    def test_income_statement_view_validates_dates(self):
        self.client.force_login(self.accountant)
        url = reverse("finance:income_statement")

        invalid_format = self.client.get(url, {"start_date": "not-a-date"})
        self.assertEqual(invalid_format.status_code, 200)
        self.assertContains(invalid_format, "Enter a valid date.")

        reversed_range = self.client.get(
            url,
            {"start_date": "2026-08-31", "end_date": "2026-08-01"},
        )
        self.assertEqual(reversed_range.status_code, 200)
        self.assertContains(
            reversed_range,
            "The start date cannot be later than the end date.",
        )


class BalanceSheetTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="balance-accountant",
            password="test-password",
            role="accountant",
        )
        create_required_fiscal_years(2026, date.today().year)
        self.cash = Account.objects.create(
            code="BS1100",
            name="Balance cash",
            account_type=Account.AccountType.ASSET,
        )
        self.payable = Account.objects.create(
            code="BS2100",
            name="Balance payable",
            account_type=Account.AccountType.LIABILITY,
        )
        self.capital = Account.objects.create(
            code="BS3100",
            name="Owner capital",
            account_type=Account.AccountType.EQUITY,
        )
        self.revenue = Account.objects.create(
            code="BS4100",
            name="Balance revenue",
            account_type=Account.AccountType.REVENUE,
        )
        self.expense = Account.objects.create(
            code="BS5100",
            name="Balance expense",
            account_type=Account.AccountType.EXPENSE,
        )

    def create_journal(self, number, journal_date, lines, *, status=JournalEntry.Status.POSTED):
        journal = JournalEntry.objects.create(
            entry_number=number,
            date=journal_date,
            description="Balance sheet test",
            status=status,
            created_by=self.accountant,
            approved_by=(
                self.accountant if status == JournalEntry.Status.POSTED else None
            ),
        )
        for account, debit, credit in lines:
            JournalEntryLine.objects.create(
                journal_entry=journal,
                account=account,
                debit=debit,
                credit=credit,
            )
        return journal

    def test_balance_sheet_includes_equity_liabilities_and_current_earnings(self):
        self.create_journal(
            "BS-CAPITAL",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("100.000"), Decimal("0.000")),
                (self.capital, Decimal("0.000"), Decimal("100.000")),
            ],
        )
        self.create_journal(
            "BS-PAYABLE",
            date(2026, 8, 2),
            [
                (self.cash, Decimal("30.000"), Decimal("0.000")),
                (self.payable, Decimal("0.000"), Decimal("30.000")),
            ],
        )
        self.create_journal(
            "BS-PROFIT",
            date(2026, 8, 3),
            [
                (self.cash, Decimal("20.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("30.000")),
                (self.expense, Decimal("10.000"), Decimal("0.000")),
            ],
        )

        report = generate_balance_sheet()

        self.assertEqual(report["total_assets"], Decimal("150.000"))
        self.assertEqual(report["total_liabilities"], Decimal("30.000"))
        self.assertEqual(report["total_equity"], Decimal("100.000"))
        self.assertEqual(report["current_earnings"], Decimal("20.000"))
        self.assertEqual(
            report["total_liabilities_and_equity"],
            Decimal("150.000"),
        )
        self.assertTrue(report["is_balanced"])

    def test_balance_sheet_excludes_drafts_and_entries_after_as_of_date(self):
        self.create_journal(
            "BS-IN-RANGE",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("40.000"), Decimal("0.000")),
                (self.capital, Decimal("0.000"), Decimal("40.000")),
            ],
        )
        self.create_journal(
            "BS-AFTER",
            date(2026, 9, 1),
            [
                (self.cash, Decimal("50.000"), Decimal("0.000")),
                (self.capital, Decimal("0.000"), Decimal("50.000")),
            ],
        )
        self.create_journal(
            "BS-DRAFT",
            date(2026, 8, 2),
            [
                (self.cash, Decimal("60.000"), Decimal("0.000")),
                (self.capital, Decimal("0.000"), Decimal("60.000")),
            ],
            status=JournalEntry.Status.DRAFT,
        )

        report = generate_balance_sheet(as_of_date=date(2026, 8, 31))

        self.assertEqual(report["total_assets"], Decimal("40.000"))
        self.assertEqual(report["total_equity"], Decimal("40.000"))
        self.assertTrue(report["is_balanced"])

    def test_reversal_cancels_balance_sheet_activity(self):
        original = self.create_journal(
            "BS-REVERSED",
            date(2026, 8, 1),
            [
                (self.cash, Decimal("70.000"), Decimal("0.000")),
                (self.revenue, Decimal("0.000"), Decimal("70.000")),
            ],
        )
        reverse_journal_entry(original.pk, self.accountant, "Incorrect sale")

        report = generate_balance_sheet()

        self.assertEqual(report["total_assets"], Decimal("0.000"))
        self.assertEqual(report["current_earnings"], Decimal("0.000"))
        self.assertTrue(report["is_balanced"])

    def test_balance_sheet_view_validates_date_and_restricts_cashiers(self):
        url = reverse("finance:balance_sheet")
        self.client.force_login(self.accountant)

        invalid = self.client.get(url, {"as_of_date": "not-a-date"})
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Enter a valid date.")

        cashier = get_user_model().objects.create_user(
            username="balance-cashier",
            password="test-password",
            role="cashier",
        )
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(url).status_code, 403)


class FiscalPeriodTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="period-accountant",
            password="test-password",
            role="accountant",
        )
        self.admin = get_user_model().objects.create_user(
            username="period-admin",
            password="test-password",
            role="admin",
        )
        self.cashier = get_user_model().objects.create_user(
            username="period-cashier",
            password="test-password",
            role="cashier",
        )
        self.fiscal_year = create_fiscal_year(2026)
        self.period = self.fiscal_year.periods.get(month=8)
        self.cash = Account.objects.create(
            code="FP1100",
            name="Period cash",
            account_type=Account.AccountType.ASSET,
        )
        self.capital = Account.objects.create(
            code="FP3100",
            name="Period capital",
            account_type=Account.AccountType.EQUITY,
        )

    def create_draft_journal(self, number="FP-JE-001"):
        journal = JournalEntry.objects.create(
            entry_number=number,
            date=date(2026, 8, 15),
            description="Fiscal period test",
            created_by=self.accountant,
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.cash,
            debit=Decimal("10.000"),
        )
        JournalEntryLine.objects.create(
            journal_entry=journal,
            account=self.capital,
            credit=Decimal("10.000"),
        )
        return journal

    def test_year_creation_builds_twelve_calendar_periods(self):
        periods = list(self.fiscal_year.periods.order_by("month"))

        self.assertEqual(len(periods), 12)
        self.assertEqual(periods[0].start_date, date(2026, 1, 1))
        self.assertEqual(periods[1].end_date, date(2026, 2, 28))
        self.assertTrue(
            all(period.status == PeriodStatus.OPEN for period in periods)
        )

    def test_closed_month_blocks_posting(self):
        set_period_status(
            self.period.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Monthly close",
        )
        journal = self.create_draft_journal()

        with self.assertRaisesMessage(ValidationError, "accounting period"):
            post_journal_entry(journal.pk, self.accountant)

        journal.refresh_from_db()
        self.assertEqual(journal.status, JournalEntry.Status.DRAFT)

    def test_reopening_month_allows_posting_and_clears_audit_fields(self):
        set_period_status(
            self.period.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Monthly close",
        )
        set_period_status(
            self.period.pk,
            PeriodStatus.OPEN,
            self.admin,
            reason="Approved correction",
        )
        journal = self.create_draft_journal()

        post_journal_entry(journal.pk, self.accountant)

        self.period.refresh_from_db()
        journal.refresh_from_db()
        self.assertIsNone(self.period.closed_by)
        self.assertIsNone(self.period.closed_at)
        self.assertEqual(journal.status, JournalEntry.Status.POSTED)

    def test_closing_year_closes_every_month(self):
        set_fiscal_year_status(
            self.fiscal_year.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Year-end review complete",
        )

        self.fiscal_year.refresh_from_db()
        self.assertEqual(self.fiscal_year.status, PeriodStatus.CLOSED)
        self.assertEqual(self.fiscal_year.closed_by, self.accountant)
        self.assertEqual(
            self.fiscal_year.periods.filter(status=PeriodStatus.CLOSED).count(),
            12,
        )

    def test_closed_year_blocks_posting(self):
        set_fiscal_year_status(
            self.fiscal_year.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Year-end review complete",
        )
        journal = self.create_draft_journal()

        with self.assertRaisesMessage(ValidationError, "fiscal year is closed"):
            post_journal_entry(journal.pk, self.accountant)

    def test_month_cannot_reopen_while_year_is_closed(self):
        set_fiscal_year_status(
            self.fiscal_year.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Year-end review complete",
        )

        with self.assertRaisesMessage(ValidationError, "Open the fiscal year"):
            set_period_status(
                self.period.pk,
                PeriodStatus.OPEN,
                self.admin,
                reason="Correction required",
            )

    def test_only_administrator_can_reopen_month_or_year(self):
        set_period_status(
            self.period.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Monthly close",
        )

        with self.assertRaises(PermissionDenied):
            set_period_status(
                self.period.pk,
                PeriodStatus.OPEN,
                self.accountant,
                reason="Correction",
            )

        set_fiscal_year_status(
            self.fiscal_year.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Year close",
        )
        with self.assertRaises(PermissionDenied):
            set_fiscal_year_status(
                self.fiscal_year.pk,
                PeriodStatus.OPEN,
                self.accountant,
                reason="Correction",
            )

        set_fiscal_year_status(
            self.fiscal_year.pk,
            PeriodStatus.OPEN,
            self.admin,
            reason="Approved correction",
        )
        self.fiscal_year.refresh_from_db()
        self.assertEqual(self.fiscal_year.status, PeriodStatus.OPEN)

    def test_accountant_can_manage_periods_but_cashier_cannot(self):
        list_url = reverse("finance:fiscal_period_list")
        status_url = reverse("finance:fiscal_period_status", args=[self.period.pk])

        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(list_url).status_code, 200)
        response = self.client.post(
            status_url,
            {"status": PeriodStatus.CLOSED, "reason": "Monthly close"},
        )
        self.assertRedirects(response, list_url)
        self.assertEqual(
            self.client.post(
                status_url,
                {"status": PeriodStatus.OPEN, "reason": "Correction"},
            ).status_code,
            403,
        )

        self.client.force_login(self.admin)
        response = self.client.post(
            status_url,
            {"status": PeriodStatus.OPEN, "reason": "Approved correction"},
        )
        self.assertRedirects(response, list_url)

        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(list_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                status_url,
                {"status": PeriodStatus.OPEN, "reason": "Not allowed"},
            ).status_code,
            403,
        )

    def test_reason_is_required_and_actions_are_audited(self):
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            set_period_status(
                self.period.pk,
                PeriodStatus.CLOSED,
                self.accountant,
            )

        set_period_status(
            self.period.pk,
            PeriodStatus.CLOSED,
            self.accountant,
            reason="Books reconciled",
        )
        action = FiscalPeriodAction.objects.get(period=self.period)
        self.assertEqual(action.action, FiscalPeriodAction.Action.CLOSED)
        self.assertEqual(action.performed_by, self.accountant)
        self.assertEqual(action.reason, "Books reconciled")

    def test_draft_journal_blocks_closing(self):
        self.create_draft_journal()

        with self.assertRaisesMessage(ValidationError, "draft journals"):
            set_period_status(
                self.period.pk,
                PeriodStatus.CLOSED,
                self.accountant,
                reason="Attempted close",
            )

        self.period.refresh_from_db()
        self.assertEqual(self.period.status, PeriodStatus.OPEN)
        self.assertFalse(FiscalPeriodAction.objects.filter(period=self.period).exists())

    def test_period_summary_reports_posted_totals(self):
        journal = self.create_draft_journal()
        post_journal_entry(journal.pk, self.accountant)

        summary = get_period_summary(self.period)

        self.assertEqual(summary["posted_journals"], 1)
        self.assertEqual(summary["draft_journals"], 0)
        self.assertEqual(summary["total_debit"], Decimal("10.000"))
        self.assertEqual(summary["total_credit"], Decimal("10.000"))

    def test_accountant_can_update_period_notes(self):
        self.client.force_login(self.accountant)
        response = self.client.post(
            reverse("finance:fiscal_period_notes", args=[self.period.pk]),
            {"notes": "Bank reconciliation completed."},
        )

        self.assertRedirects(response, reverse("finance:fiscal_period_list"))
        self.period.refresh_from_db()
        self.assertEqual(self.period.notes, "Bank reconciliation completed.")

    def test_history_page_is_linked_and_paginates_ten_rows(self):
        FiscalPeriodAction.objects.bulk_create(
            [
                FiscalPeriodAction(
                    fiscal_year=self.fiscal_year,
                    period=self.period,
                    action=(
                        FiscalPeriodAction.Action.CLOSED
                        if index % 2
                        else FiscalPeriodAction.Action.OPENED
                    ),
                    performed_by=self.admin,
                    reason=f"History action {index}",
                )
                for index in range(11)
            ]
        )
        list_url = reverse("finance:fiscal_period_list")
        history_url = reverse(
            "finance:fiscal_year_history",
            args=[self.fiscal_year.pk],
        )
        self.client.force_login(self.accountant)

        list_response = self.client.get(list_url)
        self.assertContains(list_response, history_url)

        first_page = self.client.get(history_url)
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context["page_obj"]), 10)
        self.assertTrue(first_page.context["page_obj"].has_next())

        second_page = self.client.get(history_url, {"page": 2})
        self.assertEqual(len(second_page.context["page_obj"]), 1)
        self.assertTrue(second_page.context["page_obj"].has_previous())

    def test_cashier_cannot_view_fiscal_history(self):
        self.client.force_login(self.cashier)
        response = self.client.get(
            reverse(
                "finance:fiscal_year_history",
                args=[self.fiscal_year.pk],
            )
        )
        self.assertEqual(response.status_code, 403)
