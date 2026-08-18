from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory.models import StockBalance, StockBatch


class Command(BaseCommand):
    help = (
        "Compare StockBalance against the batches behind it. POS sells from "
        "batches via FEFO but checks availability against StockBalance, so if "
        "the two disagree an item can look in stock and still refuse to sell. "
        "Use --fix to create a balancing batch where stock is missing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Create a correcting batch so the batches match the balance.",
        )

    def handle(self, *args, **options):
        today = timezone.now().date()
        problems = []

        balances = (
            StockBalance.objects
            .select_related("product", "warehouse")
            .order_by("product__code", "warehouse__name")
        )

        for balance in balances:
            batches = StockBatch.objects.filter(
                product=balance.product,
                warehouse=balance.warehouse,
                quantity_remaining__gt=0,
            ).exclude(status=StockBatch.BatchStatus.DEPLETED)

            total = sum((b.quantity_remaining for b in batches), Decimal("0.000"))

            sellable = sum(
                (
                    b.quantity_remaining
                    for b in batches
                    if b.status == StockBatch.BatchStatus.ACTIVE
                    and (b.expiration_date is None or b.expiration_date >= today)
                ),
                Decimal("0.000"),
            )

            if balance.quantity != total or sellable < balance.quantity:
                problems.append((balance, total, sellable))

        if not problems:
            self.stdout.write(self.style.SUCCESS(
                "Every balance matches its batches and is sellable."
            ))
            return

        self.stdout.write(self.style.WARNING(f"{len(problems)} problem(s) found:\n"))
        for balance, total, sellable in problems:
            self.stdout.write(
                f"  {balance.product.code} — {balance.product.name} "
                f"@ {balance.warehouse.name}"
            )
            self.stdout.write(f"      StockBalance says : {balance.quantity}")
            self.stdout.write(f"      batches hold      : {total}")
            self.stdout.write(f"      sellable by FEFO  : {sellable}")

            if sellable < balance.quantity:
                self.stdout.write(self.style.ERROR(
                    f"      -> POS will refuse to sell "
                    f"{balance.quantity - sellable} of these"
                ))
            self.stdout.write("")

        if not options["fix"]:
            self.stdout.write(
                "Run again with --fix to create a correcting batch for each gap."
            )
            return

        with transaction.atomic():
            created = 0
            for balance, total, sellable in problems:
                gap = balance.quantity - sellable
                if gap <= 0:
                    continue

                StockBatch.objects.create(
                    product=balance.product,
                    warehouse=balance.warehouse,
                    batch_number=f"FIX-{timezone.now().strftime('%Y%m%d%H%M%S')}-{balance.pk}",
                    expiration_date=None,
                    received_date=today,
                    unit_cost=balance.product.purchase_price,
                    quantity_received=gap,
                    quantity_remaining=gap,
                    status=StockBatch.BatchStatus.ACTIVE,
                )
                created += 1
                self.stdout.write(
                    f"  created a batch of {gap} for {balance.product.code} "
                    f"@ {balance.warehouse.name}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {created} correcting batch(es) created."
        ))
