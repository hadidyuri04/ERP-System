from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

class Notification(models.Model):
    class NotificationType(models.TextChoices):
        LOW_STOCK = 'LOW_STOCK', _('Low Stock')
        EXPIRING_SOON = 'EXPIRING_SOON', _('Expiring Soon')
        EXPIRED = 'EXPIRED', _('Expired')
        PAYMENT_DUE = 'PAYMENT_DUE', _('Payment Due')
        SYSTEM = 'SYSTEM', _('System')

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("User"), on_delete=models.CASCADE, null=True, blank=True)
    notification_type = models.CharField(_("Notification Type"), max_length=30, choices=NotificationType.choices)
    title = models.CharField(_("Title"), max_length=255)
    message = models.TextField(_("Message"))
    related_model = models.CharField(_("Related Model"), max_length=100, blank=True, null=True)
    related_id = models.BigIntegerField(_("Related ID"), blank=True, null=True)
    is_read = models.BooleanField(_("Is Read"), default=False)
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")

    def __str__(self):
        return f"[{self.notification_type}] {self.title}"