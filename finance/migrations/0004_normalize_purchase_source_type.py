from django.db import migrations


def normalize_purchase_source_type(apps, schema_editor):
    JournalEntry = apps.get_model("finance", "JournalEntry")

    uppercase_entries = JournalEntry.objects.filter(source_type="PURCHASE")

    for entry in uppercase_entries.iterator():
        conflict_exists = (
            entry.source_id is not None
            and JournalEntry.objects.filter(
                source_type="purchase",
                source_id=entry.source_id,
            ).exclude(pk=entry.pk).exists()
        )

        if conflict_exists:
            raise RuntimeError(
                "Cannot normalize purchase journal "
                f"{entry.pk}: another purchase journal already uses "
                f"source_id={entry.source_id}."
            )

    uppercase_entries.update(source_type="purchase")


def reverse_purchase_source_type(apps, schema_editor):
    JournalEntry = apps.get_model("finance", "JournalEntry")
    JournalEntry.objects.filter(source_type="purchase").update(
        source_type="PURCHASE"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0003_unique_journal_source"),
    ]

    operations = [
        migrations.RunPython(
            normalize_purchase_source_type,
            reverse_purchase_source_type,
        ),
    ]
