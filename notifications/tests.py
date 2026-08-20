from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from inventory.models import (
    Category,
    Product,
    StockAdjustment,
    StockBalance,
    StockBatch,
    Unit,
    Warehouse,
)
from inventory.services import confirm_stock_adjustment
from inventory.tests import seed_posting_setup

from .models import Notification
from .services import generate_notifications

User = get_user_model()


class NotificationGenerationTests(TestCase):
    """Signed requirements module 17: low stock and expiry alerts."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="tester", email="t@example.com", password="x"
        )
        self.today = timezone.now().date()

        category = Category.objects.create(code="C1", name_en="Drinks", name_ar="مشروبات")
        unit = Unit.objects.create(name_en="Piece", name_ar="قطعة", symbol="pc")
        self.warehouse = Warehouse.objects.create(code="W1", name="Main")
        self.product = Product.objects.create(
            code="P1",
            name_en="Ice Tea",
            name_ar="شاي مثلج",
            category=category,
            unit=unit,
            purchase_price=Decimal("0.250"),
            selling_price=Decimal("0.350"),
            minimum_stock=Decimal("20.000"),
        )
        StockBalance.objects.create(
            product=self.product, warehouse=self.warehouse, quantity=Decimal("5.000")
        )
        # One test restocks via a stock adjustment, which posts to accounting.
        seed_posting_setup(self.user, self.today)

    def _batch(self, number, expires_in_days, quantity="10.000"):
        return StockBatch.objects.create(
            product=self.product,
            warehouse=self.warehouse,
            batch_number=number,
            expiration_date=self.today + timedelta(days=expires_in_days),
            received_date=self.today,
            unit_cost=Decimal("0.250"),
            quantity_received=Decimal(quantity),
            quantity_remaining=Decimal(quantity),
            status=StockBatch.BatchStatus.ACTIVE,
        )

    def test_low_stock_alert_is_raised(self):
        generate_notifications()
        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.NotificationType.LOW_STOCK,
                is_read=False,
            ).count(),
            1,
        )

    def test_repeated_runs_do_not_duplicate(self):
        generate_notifications()
        result = generate_notifications()

        self.assertEqual(result["created"], 0)
        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.NotificationType.LOW_STOCK
            ).count(),
            1,
        )

    def test_alert_clears_itself_once_restocked(self):
        generate_notifications()

        adjustment = StockAdjustment.objects.create(
            adjustment_number="ADJ-TEST-1",
            warehouse=self.warehouse,
            date=self.today,
            created_by=self.user,
        )
        adjustment.items.create(
            product=self.product,
            counted_quantity=Decimal("50.000"),
            system_quantity=Decimal("5.000"),
        )
        confirm_stock_adjustment(adjustment.id, self.user)

        result = generate_notifications()

        self.assertEqual(result["resolved"], 1)
        self.assertFalse(
            Notification.objects.filter(
                notification_type=Notification.NotificationType.LOW_STOCK,
                is_read=False,
            ).exists()
        )

    def test_expired_batch_raises_an_expired_alert(self):
        self._batch("B-OLD", expires_in_days=-3)
        generate_notifications()

        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.NotificationType.EXPIRED,
                is_read=False,
            ).count(),
            1,
        )

    def test_batch_inside_the_warning_window_raises_expiring_soon(self):
        self._batch("B-SOON", expires_in_days=5)
        generate_notifications()

        self.assertEqual(
            Notification.objects.filter(
                notification_type=Notification.NotificationType.EXPIRING_SOON,
                is_read=False,
            ).count(),
            1,
        )

    def test_batch_far_in_the_future_raises_nothing(self):
        self._batch("B-FAR", expires_in_days=400)
        generate_notifications()

        self.assertFalse(
            Notification.objects.filter(
                notification_type__in=[
                    Notification.NotificationType.EXPIRED,
                    Notification.NotificationType.EXPIRING_SOON,
                ]
            ).exists()
        )


class NotificationViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="viewer", email="v@example.com", password="x"
        )
        self.client.force_login(self.user)

    def test_list_page_renders(self):
        self.assertEqual(self.client.get("/notifications/").status_code, 200)

    def test_mark_all_read(self):
        Notification.objects.create(
            notification_type=Notification.NotificationType.SYSTEM,
            title="Test",
            message="Test",
        )
        self.client.post("/notifications/read-all/")

        self.assertFalse(Notification.objects.filter(is_read=False).exists())
