from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("runs", "0011_agentrun_approval_fingerprint_agentrun_approval_mode_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subrunlink",
            name="failure_policy",
            field=models.CharField(
                choices=[
                    ("FAIL_FAST", "Fail parent on child failure (reserved for critical safety or security issues)"),
                    ("IGNORE_FAILURE", "Ignore child failures"),
                    ("CANCEL_SIBLINGS", "Cancel siblings on failure"),
                ],
                default="IGNORE_FAILURE",
                max_length=32,
            ),
        ),
    ]
