import logging

from django.db import migrations

logger = logging.getLogger("comms.migrations")


def _populate_endpoints(apps, schema_editor):
    TransportEndpoint = apps.get_model("comms", "TransportEndpoint")
    CommsConversation = apps.get_model("comms", "CommsConversation")

    for conversation in CommsConversation.objects.filter(endpoint__isnull=True):
        endpoints = list(
            TransportEndpoint.objects.filter(
                transport_id=conversation.transport_id,
                kind="bot",
                transport__is_enabled=True,
            ).order_by("id")
        )
        if len(endpoints) == 1:
            conversation.endpoint_id = endpoints[0].id
            conversation.save(update_fields=["endpoint"])
        elif len(endpoints) == 0:
            logger.warning(
                "No enabled bot endpoints found for transport %s; conversation %s left unassigned",
                conversation.transport_id,
                conversation.pk,
            )
        else:
            logger.warning(
                "Multiple enabled bot endpoints for transport %s; conversation %s left unassigned",
                conversation.transport_id,
                conversation.pk,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0002_alter_commsconversation_unique_together_and_more"),
    ]

    operations = [
        migrations.RunPython(_populate_endpoints, migrations.RunPython.noop),
    ]
