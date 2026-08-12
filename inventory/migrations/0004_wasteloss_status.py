from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "inventory",
            "0003_warehousetransfer_warehousetransferitem_wasteloss_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="wasteloss",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("confirmed", "Confirmed"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=20,
                verbose_name="Status",
            ),
        ),
    ]
