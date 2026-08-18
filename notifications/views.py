from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from .models import Notification
from .services import generate_notifications


def _visible_to(user):
    """Alerts addressed to this user, plus system-wide ones."""
    return Q(user=user) | Q(user__isnull=True)


@login_required
def notification_list_view(request):
    """
    All alerts, newest first.

    Regenerating on load means the list is current even before anyone
    schedules the management command.
    """
    generate_notifications()

    show = request.GET.get("show", "unread")
    queryset = Notification.objects.filter(_visible_to(request.user))

    if show == "unread":
        queryset = queryset.filter(is_read=False)

    return render(request, "notifications/notification_list.html", {
        "notifications": queryset.order_by("-created_at")[:200],
        "show": show,
        "unread_total": Notification.objects.filter(
            _visible_to(request.user), is_read=False
        ).count(),
    })


@login_required
@require_POST
def mark_read_view(request, pk):
    notification = get_object_or_404(
        Notification.objects.filter(_visible_to(request.user)), pk=pk
    )
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return redirect(request.POST.get("next") or "notifications:list")


@login_required
@require_POST
def mark_all_read_view(request):
    updated = Notification.objects.filter(
        _visible_to(request.user), is_read=False
    ).update(is_read=True)

    messages.success(
        request,
        _("%(count)s notification(s) marked as read.") % {"count": updated},
    )
    return redirect("notifications:list")
