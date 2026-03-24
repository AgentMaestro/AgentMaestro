import os
from collections import defaultdict

from celery import shared_task
from django.db import transaction
from openai import OpenAI
from logging_utils import get_app_logger

from .models import ModelsAvailable

logger = get_app_logger(__name__)
OPENAI_MODELS_COMPANY = "openai"
OPENAI_MODELS_API = "responses"


def _as_dict(entry: object) -> dict[str, object] | None:
    if isinstance(entry, dict):
        return entry
    if hasattr(entry, "model_dump"):
        return entry.model_dump()
    try:
        return dict(entry)
    except Exception:
        return None


def _extract_name(entry: dict[str, object]) -> str | None:
    return str(entry.get("id") or entry.get("name") or entry.get("model") or "").strip() or None


def _extract_company(entry: dict[str, object]) -> str:
    return str(entry.get("owned_by") or entry.get("company") or OPENAI_models_COMPANY)


def _extract_api(entry: dict[str, object]) -> str:
    metadata = entry.get("metadata") or {}
    if isinstance(metadata, dict):
        api_value = metadata.get("api") or metadata.get("platform")
    else:
        api_value = None
    if not api_value:
        api_value = entry.get("api") or entry.get("interface") or None
    if not api_value:
        tags = entry.get("tags")
        if isinstance(tags, list) and "responses" in tags:
            api_value = "responses"
    return str(api_value or OPENAI_MODELS_API)


def _collect_entries(response: object) -> list[dict[str, object]]:
    data_candidates = []
    if hasattr(response, "data"):
        data_candidates = list(response.data)
    elif isinstance(response, dict):
        data_candidates = response.get("data") or []
    entries: list[dict[str, object]] = []
    for item in data_candidates:
        normalized = _as_dict(item)
        if normalized:
            entries.append(normalized)
    return entries


@shared_task(ignore_result=True)
def refresh_openai_models() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("refresh_openai_models skipped: OPENAI_API_KEY is not set.")
        return
    client_kwargs = {"api_key": api_key}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)
    try:
        response = client.models.list()
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to fetch OpenAI models: %s", exc)
        return
    entries = _collect_entries(response)
    records: list[tuple[str, str, str, dict[str, object]]] = []
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in entries:
        name = _extract_name(entry)
        if not name:
            continue
        company = _extract_company(entry)
        api = _extract_api(entry)
        records.append((company, api, name, entry))
        grouped[(company, api)].append(name)
    if not records:
        logger.info("refresh_openai_models found no valid model rows to sync.")
        return
    with transaction.atomic():
        for (company, api), names in grouped.items():
            ModelsAvailable.objects.filter(company=company, api=api).delete()
            for record in records:
                if record[0] == company and record[1] == api:
                    ModelsAvailable.objects.create(
                        company=company,
                        api=api,
                        name=record[2],
                        metadata=record[3],
                    )
    logger.info(
        "Refreshed %d OpenAI models across %d company/api groups.",
        len(records),
        len(grouped),
    )
