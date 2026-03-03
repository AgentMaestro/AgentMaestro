from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("control", "0002_approvalrequest_approvalgrant"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestevent",
            name="result_meta",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
