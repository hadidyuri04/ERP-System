from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Notification


def _exists_unread(notification_type, related_model, related_id):
    """
    True when this exact alert is already sitting unread.

    Without this the daily run would post the same "low stock" line every
    morning until somebody fixed the stock, and the bell would be useless.
    """
    return Notification.objects.filter(
        notification_type=notification_type,
        related_model=related_model,
        related_id=related_id,
        is_read=False,
    ).exists()


def _clear_resolved(notification_type, related_model, still_valid_ids):
    """
    Drop unread alerts whose cause has gone away.

    Once stock is topped up the warning should disappear on its own rather
    than waiting for someone to dismiss it by hand.
    """
    stale = Notification.objects.filter(
        notification_type=notification_type,
        related_model=related_model,
        is_read=False,
    ).exclude(related_id__in=still_valid_ids)

    count = stale.count()
    stale.delete()
    return count


@transaction.atomic
def generate_low_stock_notifications():
    """Warn about items at or below their minimum stock (signed module 17)."""
    from inventory.models import Product

    created = 0
    flagged_ids = []

    products = (
        Product.objects
        .filter(is_active=True, minimum_stock__gt=0)
        .annotate(on_hand=Sum("balances__quantity"))
    )

    for product in products:
        on_hand = product.on_hand or Decimal("0.000")
        if on_hand > product.minimum_stock:
            continue

        flagged_ids.append(product.id)

        if _exists_unread(
            Notification.NotificationType.LOW_STOCK, "inventory.Product", product.id
        ):
            continue

        Notification.objects.create(
            notification_type=Notification.NotificationType.LOW_STOCK,
            title=_("Low stock: %(product)s") % {"product": product.name},
            message=_(
                "%(product)s is down to %(on_hand)s, at or below its minimum of "
                "%(minimum)s."
            ) % {
                "product": product.name,
                "on_hand": on_hand,
                "minimum": product.minimum_stock,
            },
            related_model="inventory.Product",
            related_id=product.id,
        )
        created += 1

    resolved = _clear_resolved(
        Notification.NotificationType.LOW_STOCK, "inventory.Product", flagged_ids
    )
    return created, resolved


@transaction.atomic
def generate_expiry_notifications():
    """
    Warn about batches that have expired or are close to it.

    The warning window is CompanySettings.expiration_warning_days, which the
    signed requirements ask to be configurable.
    """
    from core.models import CompanySettings
    from inventory.models import StockBatch

    today = timezone.now().date()
    warning_days = CompanySettings.load().expiration_warning_days
    cutoff = today + timedelta(days=warning_days)

    batches = (
        StockBatch.objects
        .select_related("product", "warehouse")
        .filter(
            expiration_date__isnull=False,
            expiration_date__lte=cutoff,
            quantity_remaining__gt=0,
        )
        .exclude(status=StockBatch.BatchStatus.DEPLETED)
    )

    created = 0
    expired_ids = []
    soon_ids = []

    for batch in batches:
        is_expired = batch.expiration_date < today

        if is_expired:
            kind = Notification.NotificationType.EXPIRED
            expired_ids.append(batch.id)
            title = _("Expired: %(product)s") % {"product": batch.product.name}
            message = _(
                "Batch %(batch)s of %(product)s in %(warehouse)s expired on "
                "%(date)s. %(qty)s remain and cannot be sold. Write it off "
                "with a waste document."
            ) % {
                "batch": batch.batch_number,
                "product": batch.product.name,
                "warehouse": batch.warehouse.name,
                "date": batch.expiration_date,
                "qty": batch.quantity_remaining,
            }
        else:
            kind = Notification.NotificationType.EXPIRING_SOON
            soon_ids.append(batch.id)
            title = _("Expiring soon: %(product)s") % {"product": batch.product.name}
            message = _(
                "Batch %(batch)s of %(product)s in %(warehouse)s expires on "
                "%(date)s. %(qty)s remain."
            ) % {
                "batch": batch.batch_number,
                "product": batch.product.name,
                "warehouse": batch.warehouse.name,
                "date": batch.expiration_date,
                "qty": batch.quantity_remaining,
            }

        if _exists_unread(kind, "inventory.StockBatch", batch.id):
            continue

        Notification.objects.create(
            notification_type=kind,
            title=title,
            message=message,
            related_model="inventory.StockBatch",
            related_id=batch.id,
        )
        created += 1

    resolved = _clear_resolved(
        Notification.NotificationType.EXPIRED, "inventory.StockBatch", expired_ids
    )
    resolved += _clear_resolved(
        Notification.NotificationType.EXPIRING_SOON, "inventory.StockBatch", soon_ids
    )
    return created, resolved


def generate_notifications():
    """Run every check. Safe to call repeatedly; intended for a daily schedule."""
    low_created, low_resolved = generate_low_stock_notifications()
    exp_created, exp_resolved = generate_expiry_notifications()

    return {
        "created": low_created + exp_created,
        "resolved": low_resolved + exp_resolved,
        "low_stock": low_created,
        "expiry": exp_created,
    }
