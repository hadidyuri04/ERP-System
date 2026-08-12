from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0002_paymentvoucher_receiptvoucher"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(source_id__isnull=False),
                fields=("source_type", "source_id"),
                name="unique_journal_source",
            ),
        ),
    ]
