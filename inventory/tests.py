from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import (
    Category,
    Product,
    StockAdjustment,
    StockBalance,
    StockBatch,
    StockMovement,
    Unit,
    Warehouse,
    WarehouseTransfer,
    WasteLoss,
)
from .services import (
    confirm_stock_adjustment,
    confirm_warehouse_transfer,
    confirm_waste_loss,
    mark_expired_batches,
)

User = get_user_model()


def seed_posting_setup(user, today):
    """
    Minimum finance setup for any service that posts to accounting.

    Confirming a waste document or a stock adjustment ends in a journal entry,
    and those services are atomic: without these the posting fails and the
    stock changes roll back with it.
    """
    from finance.models import Account, FiscalYear
    from finance.services import create_fiscal_year

    accounts = [
        ("1400", "Inventory", Account.AccountType.ASSET),
        ("6300", "Waste and Loss", Account.AccountType.EXPENSE),
        ("6310", "Inventory Adjustment Loss", Account.AccountType.EXPENSE),
        ("4300", "Inventory Adjustment Gain", Account.AccountType.REVENUE),
    ]
    for code, name, account_type in accounts:
        Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "account_type": account_type,
                "allow_posting": True,
            },
        )

    # Posting is refused unless the date falls inside an open fiscal period.
    if not FiscalYear.objects.filter(year=today.year).exists():
        create_fiscal_year(today.year, user=user)


class InventoryTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="inv", email="i@example.com", password="x"
        )
        self.today = timezone.now().date()

        category = Category.objects.create(code="C1", name_en="Drinks", name_ar="مشروبات")
        unit = Unit.objects.create(name_en="Piece", name_ar="قطعة", symbol="pc")

        self.source = Warehouse.objects.create(code="W1", name="Main")
        self.destination = Warehouse.objects.create(code="W2", name="Branch")

        self.product = Product.objects.create(
            code="P1",
            name_en="Ice Tea",
            name_ar="شاي مثلج",
            category=category,
            unit=unit,
            purchase_price=Decimal("0.250"),
            selling_price=Decimal("0.350"),
        )

    def give_stock(self, warehouse, quantity, expires_in_days=None, number="B1"):
        StockBatch.objects.create(
            product=self.product,
            warehouse=warehouse,
            batch_number=number,
            expiration_date=(
                None if expires_in_days is None
                else self.today + timedelta(days=expires_in_days)
            ),
            received_date=self.today,
            unit_cost=Decimal("0.250"),
            quantity_received=Decimal(quantity),
            quantity_remaining=Decimal(quantity),
            status=StockBatch.BatchStatus.ACTIVE,
        )
        balance, _ = StockBalance.objects.get_or_create(
            product=self.product, warehouse=warehouse,
            defaults={"quantity": Decimal("0.000")},
        )
        balance.quantity += Decimal(quantity)
        balance.save()

    def balance(self, warehouse):
        return StockBalance.objects.get(
            product=self.product, warehouse=warehouse
        ).quantity


class WarehouseTransferTests(InventoryTestBase):
    """Spec 5.4: total company stock must not change on a transfer."""

    def _transfer(self, quantity="30.000"):
        transfer = WarehouseTransfer.objects.create(
            transfer_number="WT-1",
            source_warehouse=self.source,
            destination_warehouse=self.destination,
            date=self.today,
            created_by=self.user,
        )
        transfer.items.create(product=self.product, quantity=Decimal(quantity))
        return transfer

    def test_confirm_moves_stock_without_changing_the_total(self):
        self.give_stock(self.source, "100.000")
        before = self.balance(self.source)

        confirm_warehouse_transfer(self._transfer().id, self.user)

        self.assertEqual(self.balance(self.source), before - Decimal("30.000"))
        self.assertEqual(self.balance(self.destination), Decimal("30.000"))
        self.assertEqual(
            self.balance(self.source) + self.balance(self.destination), before
        )

    def test_confirm_writes_a_matching_pair_of_movements(self):
        self.give_stock(self.source, "100.000")
        confirm_warehouse_transfer(self._transfer().id, self.user)

        out = StockMovement.objects.filter(
            movement_type=StockMovement.MovementType.TRANSFER_OUT
        ).first()
        into = StockMovement.objects.filter(
            movement_type=StockMovement.MovementType.TRANSFER_IN
        ).first()

        self.assertEqual(out.quantity, -into.quantity)

    def test_same_warehouse_is_refused(self):
        self.give_stock(self.source, "100.000")
        transfer = self._transfer()
        transfer.destination_warehouse = self.source
        transfer.save()

        with self.assertRaises(ValidationError):
            confirm_warehouse_transfer(transfer.id, self.user)

    def test_transferring_more_than_available_is_refused(self):
        self.give_stock(self.source, "10.000")

        with self.assertRaises(ValidationError):
            confirm_warehouse_transfer(self._transfer("30.000").id, self.user)

    def test_a_confirmed_transfer_cannot_be_confirmed_again(self):
        self.give_stock(self.source, "100.000")
        transfer = self._transfer()
        confirm_warehouse_transfer(transfer.id, self.user)

        with self.assertRaises(ValidationError):
            confirm_warehouse_transfer(transfer.id, self.user)


class StockAdjustmentTests(InventoryTestBase):
    def setUp(self):
        super().setUp()
        # Confirming an adjustment now posts the inventory gain or loss.
        seed_posting_setup(self.user, self.today)

    def _adjustment(self, counted):
        adjustment = StockAdjustment.objects.create(
            adjustment_number=f"ADJ-{counted}",
            warehouse=self.source,
            date=self.today,
            created_by=self.user,
        )
        adjustment.items.create(
            product=self.product,
            counted_quantity=Decimal(counted),
            system_quantity=Decimal("0.000"),
        )
        return adjustment

    def test_a_shortage_records_adjustment_out(self):
        self.give_stock(self.source, "100.000")
        confirm_stock_adjustment(self._adjustment("60.000").id, self.user)

        self.assertEqual(self.balance(self.source), Decimal("60.000"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.ADJUSTMENT_OUT
            ).exists()
        )

    def test_a_surplus_records_adjustment_in(self):
        self.give_stock(self.source, "10.000")
        confirm_stock_adjustment(self._adjustment("40.000").id, self.user)

        self.assertEqual(self.balance(self.source), Decimal("40.000"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.ADJUSTMENT_IN
            ).exists()
        )

    def test_a_matching_count_changes_nothing(self):
        self.give_stock(self.source, "25.000")
        confirm_stock_adjustment(self._adjustment("25.000").id, self.user)

        self.assertEqual(self.balance(self.source), Decimal("25.000"))
        self.assertFalse(
            StockMovement.objects.filter(
                movement_type__in=[
                    StockMovement.MovementType.ADJUSTMENT_IN,
                    StockMovement.MovementType.ADJUSTMENT_OUT,
                ]
            ).exists()
        )


class WasteLossTests(InventoryTestBase):
    """Confirming a waste document must reduce stock, not only post the loss."""

    def setUp(self):
        super().setUp()
        seed_posting_setup(self.user, self.today)

    def test_confirm_reduces_stock_and_writes_a_waste_movement(self):
        self.give_stock(self.source, "50.000")

        waste = WasteLoss.objects.create(
            document_number="WL-1",
            warehouse=self.source,
            date=self.today,
            reason=WasteLoss.WasteReason.EXPIRED,
            created_by=self.user,
        )
        waste.items.create(
            product=self.product,
            quantity=Decimal("20.000"),
            unit_cost=Decimal("0.250"),
            total_cost=Decimal("5.000"),
        )

        confirm_waste_loss(waste.id, self.user)

        self.assertEqual(self.balance(self.source), Decimal("30.000"))
        self.assertTrue(
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.WASTE
            ).exists()
        )


class ExpiryTests(InventoryTestBase):
    def test_a_passed_expiry_date_marks_the_batch_expired(self):
        self.give_stock(self.source, "10.000", expires_in_days=-1, number="B-OLD")

        self.assertEqual(mark_expired_batches(), 1)
        self.assertEqual(
            StockBatch.objects.get(batch_number="B-OLD").status,
            StockBatch.BatchStatus.EXPIRED,
        )

    def test_running_twice_marks_nothing_new(self):
        self.give_stock(self.source, "10.000", expires_in_days=-1, number="B-OLD")
        mark_expired_batches()

        self.assertEqual(mark_expired_batches(), 0)

    def test_a_future_expiry_date_is_left_alone(self):
        self.give_stock(self.source, "10.000", expires_in_days=30, number="B-NEW")

        self.assertEqual(mark_expired_batches(), 0)
