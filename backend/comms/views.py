from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from comms.models import TransportEndpoint
from comms.services.ingest import ingest_normalized_event
from comms.transports.telegram import TelegramAdapter

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def telegram_webhook(request: HttpRequest, endpoint_id: int):
    endpoint = get_object_or_404(TransportEndpoint.objects.select_related("transport"), id=endpoint_id)
    if endpoint.transport.key != "telegram":
        return HttpResponseBadRequest("Endpoint is not a Telegram transport")

    expected_secret = str((endpoint.config or {}).get("webhook_secret") or "").strip()
    if expected_secret:
        actual_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if actual_secret != expected_secret:
            return JsonResponse({"ok": False, "error": "invalid_webhook_secret"}, status=403)

    try:
        update = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HttpResponseBadRequest("Invalid Telegram webhook payload")

    processed = 0
    for event in TelegramAdapter.normalize_update(update):
        ingest_normalized_event(endpoint.transport.key, endpoint.id, event)
        processed += 1
    return JsonResponse({"ok": True, "processed": processed})
