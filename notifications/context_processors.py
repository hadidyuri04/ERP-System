from django.db.models import Q

from .models import Notification


def notifications(request):
    """
    Feed the bell in the header on every page.

    Kept deliberately cheap: one count and five rows. Notifications are created
    by the scheduled command, never by rendering a page, so this stays a read.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    # A notification with no user set is a system-wide alert everyone sees.
    unread = Notification.objects.filter(
        Q(user=user) | Q(user__isnull=True),
        is_read=False,
    )

    return {
        "unread_count": unread.count(),
        "recent_notifications": unread.order_by("-created_at")[:5],
    }
