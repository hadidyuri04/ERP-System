from django.core.management.base import BaseCommand

from inventory.services import mark_expired_batches


class Command(BaseCommand):
    help = (
        "Mark every active stock batch whose expiration date has passed as "
        "EXPIRED. Safe to run repeatedly; intended for a daily schedule."
    )

    def handle(self, *args, **options):
        count = mark_expired_batches()

        if count:
            self.stdout.write(
                self.style.WARNING(f"Marked {count} batch(es) as expired.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("No batches needed marking.")
            )
