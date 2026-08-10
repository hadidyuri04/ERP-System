from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import JournalEntry


@transaction.atomic
def post_journal_entry(entry_id):
    # Lock the journal entry while we are posting it
    entry = JournalEntry.objects.select_for_update().get(pk=entry_id)

    # 1. Only draft entries can be posted
    if entry.status != JournalEntry.Status.DRAFT:
        raise ValidationError(
            "Only draft journal entries can be posted."
        )

    # Get all journal lines
    lines = list(
        entry.lines.select_related("account").all()
    )

    # 2. A journal entry needs at least two lines
    if len(lines) < 2:
        raise ValidationError(
            "A journal entry must contain at least two lines."
        )

    total_debit = Decimal("0.000")
    total_credit = Decimal("0.000")

    # 3. Validate every line
    for line in lines:
        line.full_clean()

        if not line.account.is_active:
            raise ValidationError(
                f"Account {line.account.code} is inactive."
            )

        if not line.account.allow_posting:
            raise ValidationError(
                f"Account {line.account.code} does not allow posting."
            )

        total_debit += line.debit
        total_credit += line.credit

    # 4. Debit must equal credit
    if total_debit != total_credit:
        raise ValidationError(
            f"Journal entry is not balanced. "
            f"Debit = {total_debit}, "
            f"Credit = {total_credit}."
        )

    # 5. Total cannot be zero
    if total_debit == Decimal("0.000"):
        raise ValidationError(
            "Journal entry total cannot be zero."
        )

    # 6. Everything is valid → POST it
    entry.status = JournalEntry.Status.POSTED

    entry.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return entry