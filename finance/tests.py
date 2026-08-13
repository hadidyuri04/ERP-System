from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from inventory.models import (
    Category,
    Product,
    StockBalance,
    StockBatch,
    Unit,
    Warehouse,
    WasteLoss,
    WasteLossItem,
)
from inventory.services import confirm_waste_loss
from pos.models import POSPayment, POSSale, POSSaleItem
from pos.services import complete_sale
from purchasing.models import PurchaseInvoice, PurchaseInvoiceItem
from suppliers.models import Supplier

from .models import Account, JournalEntry, JournalEntryLine, PaymentVoucher, ReceiptVoucher
from .reports import generate_balance_sheet, generate_income_statement, get_account_balance
from .services import (
    get_posting_account,
    post_journal_entry,
    post_payment_voucher,
    post_pos_sale,
    post_purchase_invoice,
    post_receipt_voucher,
    reverse_journal_entry,
)


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
        )
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
        category = Category.objects.create(code="CAT", name="Category")
        unit = Unit.objects.create(name="Piece", symbol="pc")
        warehouse = Warehouse.objects.create(code="WH", name="Warehouse")
        product = Product.objects.create(
            code="P001",
            name="Product",
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
        category = Category.objects.create(code="PCAT", name="Purchase Category")
        unit = Unit.objects.create(name="Box", symbol="box")
        warehouse = Warehouse.objects.create(code="PWH", name="Purchase Warehouse")
        product = Product.objects.create(
            code="PP01",
            name="Purchased product",
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
        category = Category.objects.create(code="SCAT", name="Sale Category")
        unit = Unit.objects.create(name="Unit", symbol="u")
        warehouse = Warehouse.objects.create(code="SWH", name="Sale Warehouse")
        product = Product.objects.create(
            code="SP01",
            name="Stock product",
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
        category = Category.objects.create(code="WCAT", name="Waste Category")
        unit = Unit.objects.create(name="Waste Unit", symbol="wu")
        warehouse = Warehouse.objects.create(code="WWH", name="Waste Warehouse")
        product = Product.objects.create(
            code="WP01",
            name="Waste product",
            category=category,
            unit=unit,
            purchase_price=Decimal("12.000"),
            selling_price=Decimal("20.000"),
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
        category = Category.objects.create(code="RCAT", name="Rollback Category")
        unit = Unit.objects.create(name="Rollback Unit", symbol="ru")
        warehouse = Warehouse.objects.create(code="RWH", name="Rollback Warehouse")
        product = Product.objects.create(
            code="RP01",
            name="Rollback product",
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


class JournalReversalTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="reversal-accountant",
            password="test-password",
            role="accountant",
        )
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


class IncomeStatementTests(TestCase):
    def setUp(self):
        self.accountant = get_user_model().objects.create_user(
            username="statement-accountant",
            password="test-password",
            role="accountant",
        )
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
