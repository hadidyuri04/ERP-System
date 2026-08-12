from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

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
from .services import (
    get_posting_account,
    post_journal_entry,
    post_payment_voucher,
    post_pos_sale,
    post_purchase_invoice,
    post_receipt_voucher,
)


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
