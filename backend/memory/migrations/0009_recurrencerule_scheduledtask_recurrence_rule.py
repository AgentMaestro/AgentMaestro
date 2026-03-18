# Generated manually for recurrence rules.

from zoneinfo import ZoneInfo

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone



def backfill_recurrence_rules(apps, schema_editor):
    ScheduledTask = apps.get_model("memory", "ScheduledTask")
    RecurrenceRule = apps.get_model("memory", "RecurrenceRule")

    for task in ScheduledTask.objects.all().iterator():
        timezone_name = getattr(task, "timezone", "UTC") or "UTC"
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001
            zone = ZoneInfo("UTC")
            timezone_name = "UTC"
        created_at = task.created_at or django.utils.timezone.now()
        local_created = django.utils.timezone.localtime(created_at, timezone=zone)
        rule = RecurrenceRule.objects.create(
            name=task.title or "",
            timezone=timezone_name,
            frequency="daily",
            interval=1,
            by_weekday=[],
            by_month_day=[],
            week_of_month=None,
            weekday_of_month="",
            by_month=[],
            local_time=task.local_time,
            run_minute=None,
            window_start_time=None,
            window_end_time=None,
            start_date=local_created.date(),
            end_date=None,
            is_active=True,
        )
        task.recurrence_rule_id = rule.id
        task.save(update_fields=["recurrence_rule"])


class Migration(migrations.Migration):

    dependencies = [
        ("memory", "0008_scheduledtaskapproval"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecurrenceRule",
            fields=[
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, default="", max_length=160)),
                ("timezone", models.CharField(max_length=64)),
                ("frequency", models.CharField(choices=[("hourly", "Hourly"), ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("semiannual", "Semiannual"), ("annual", "Annual")], max_length=24)),
                ("interval", models.PositiveIntegerField(default=1)),
                ("by_weekday", models.JSONField(blank=True, default=list)),
                ("by_month_day", models.JSONField(blank=True, default=list)),
                ("week_of_month", models.SmallIntegerField(blank=True, null=True)),
                ("weekday_of_month", models.CharField(blank=True, choices=[("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday")], default="", max_length=3)),
                ("by_month", models.JSONField(blank=True, default=list)),
                ("local_time", models.TimeField(blank=True, null=True)),
                ("run_minute", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("window_start_time", models.TimeField(blank=True, null=True)),
                ("window_end_time", models.TimeField(blank=True, null=True)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["is_active", "frequency", "timezone"], name="memory_recu_is_acti_3663b1_idx"),
                    models.Index(fields=["timezone", "frequency"], name="memory_recu_timezon_99cee9_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="scheduledtask",
            name="recurrence_rule",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="scheduled_tasks", to="memory.recurrencerule"),
        ),
        migrations.AlterField(
            model_name="scheduledtask",
            name="schedule_kind",
            field=models.CharField(choices=[("daily_time", "Daily Time"), ("recurrence_rule", "Recurrence Rule")], default="daily_time", max_length=24),
        ),
        migrations.RunPython(backfill_recurrence_rules, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="scheduledtask",
            name="recurrence_rule",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="scheduled_tasks", to="memory.recurrencerule"),
        ),
    ]
