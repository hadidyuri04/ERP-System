from django.core.management.base import BaseCommand

from notifications.services import generate_notifications


class Command(BaseCommand):
    help = (
        "Create low stock and expiry alerts, and clear ones whose cause has "
        "gone away. Safe to run repeatedly; intended for a daily schedule."
    )

    def handle(self, *args, **options):
        result = generate_notifications()

        if result["created"]:
            self.stdout.write(self.style.WARNING(
                f"Created {result['created']} notification(s): "
                f"{result['low_stock']} low stock, {result['expiry']} expiry."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("No new notifications."))

        if result["resolved"]:
            self.stdout.write(
                f"Cleared {result['resolved']} alert(s) that no longer apply."
            )
