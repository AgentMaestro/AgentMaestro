from __future__ import annotations

import asyncio
from typing import Any

import httpx
from django.apps import apps
from django.db import OperationalError

from agents.utils import normalize_provider_for_model

DEFAULT_BACKUP_MODEL_PROVIDER_BY_COMPANY = {
    "openai": "openai",
    "google": "gemini",
}
DEFAULT_BACKUP_MODEL_API_BY_COMPANY = {
    "openai": "responses",
    "google": "gemini",
}
DEFAULT_BACKUP_RETRY_POLICY = {
    "retry_same_model_attempts": 1,
    "retryable_status_codes": [429, 502, 503, 504],
}
DEFAULT_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
DEFAULT_RETRYABLE_CLASSIFICATIONS = {
    "connection_limit",
    "network_error",
    "ratelimit",
    "service_unavailable",
    "timeout",
    "transient_error",
}


def _coerce_int(value: object | None) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _resolve_models_available_model():
    try:
        return apps.get_model("llm", "ModelsAvailable")
    except LookupError:
        return None


def _provider_for_company(company: str, model_name: str) -> str:
    company_key = str(company or "").strip().lower()
    if company_key in DEFAULT_BACKUP_MODEL_PROVIDER_BY_COMPANY:
        return DEFAULT_BACKUP_MODEL_PROVIDER_BY_COMPANY[company_key]
    return normalize_provider_for_model(company_key, model_name)


def _resolve_catalog_candidate(company: str, api: str, name: str) -> dict[str, str] | None:
    ModelsAvailable = _resolve_models_available_model()
    if ModelsAvailable is None:
        return None

    company_key = str(company or "").strip().lower()
    api_key = str(api or "").strip().lower()
    name_key = str(name or "").strip()
    if not company_key or not name_key:
        return None

    try:
        queryset = ModelsAvailable.objects.filter(
            company__iexact=company_key,
            name__iexact=name_key,
        )
        if api_key:
            queryset = queryset.filter(api__iexact=api_key)
        resolved = queryset.order_by("company", "api", "name").first()
        if resolved is None and api_key:
            resolved = (
                ModelsAvailable.objects.filter(
                    company__iexact=company_key,
                    name__iexact=name_key,
                )
                .order_by("company", "api", "name")
                .first()
            )
    except OperationalError:
        return None
    except Exception:
        return None

    if resolved is None:
        fallback_api = api_key or DEFAULT_BACKUP_MODEL_API_BY_COMPANY.get(company_key, "")
        return {
            "company": company_key,
            "api": fallback_api,
            "name": name_key,
        }
    return {
        "company": str(resolved.company or "").strip().lower(),
        "api": str(resolved.api or "").strip().lower(),
        "name": str(resolved.name or "").strip(),
    }


def build_model_failover_candidates(
    *,
    primary_provider: str,
    primary_model: str,
    backup_models: list[dict[str, Any]] | None = None,
    default_provider: str | None = None,
) -> list[dict[str, str]]:
    primary_model_name = str(primary_model or "").strip()
    provider = normalize_provider_for_model(default_provider or primary_provider, primary_model_name)
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if primary_model_name:
        primary_entry = {
            "provider": provider,
            "model": primary_model_name,
            "source": "primary",
        }
        candidates.append(primary_entry)
        seen.add((primary_entry["provider"], primary_entry["model"]))

    for raw_backup in backup_models or []:
        if not isinstance(raw_backup, dict):
            continue
        company = str(raw_backup.get("company") or "").strip().lower()
        api = str(raw_backup.get("api") or "").strip().lower()
        name = str(raw_backup.get("name") or "").strip()
        if not company or not name:
            continue
        resolved = _resolve_catalog_candidate(company, api, name)
        if resolved is None:
            continue
        candidate_provider = _provider_for_company(resolved["company"], resolved["name"])
        candidate = {
            "provider": candidate_provider,
            "model": resolved["name"],
            "company": resolved["company"],
            "api": resolved["api"],
            "source": "backup",
        }
        key = (candidate["provider"], candidate["model"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    return candidates


def normalize_backup_retry_policy(raw: object | None) -> dict[str, object]:
    policy = dict(DEFAULT_BACKUP_RETRY_POLICY)
    if not isinstance(raw, dict):
        return policy

    retry_same_model_attempts = _coerce_int(raw.get("retry_same_model_attempts"))
    if retry_same_model_attempts is not None and retry_same_model_attempts >= 0:
        policy["retry_same_model_attempts"] = retry_same_model_attempts

    retryable_status_codes = raw.get("retryable_status_codes")
    if isinstance(retryable_status_codes, (list, tuple, set)):
        normalized_codes: list[int] = []
        seen_codes: set[int] = set()
        for code in retryable_status_codes:
            normalized_code = _coerce_int(code)
            if normalized_code is None or normalized_code in seen_codes:
                continue
            seen_codes.add(normalized_code)
            normalized_codes.append(normalized_code)
        if normalized_codes:
            policy["retryable_status_codes"] = normalized_codes
    return policy


def _extract_status_code(exc: Exception) -> int | None:
    status_code = _coerce_int(getattr(exc, "status_code", None))
    if status_code is not None:
        return status_code
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = _coerce_int(getattr(response, "status_code", None))
        if status_code is not None:
            return status_code
    status = _coerce_int(getattr(exc, "status", None))
    if status is not None:
        return status
    return None


def is_retryable_model_failure(
    exc: Exception,
    *,
    client: object | None = None,
    retry_policy: dict[str, object] | None = None,
) -> bool:
    if client is not None:
        checker = getattr(client, "is_transient_error", None)
        if callable(checker):
            try:
                if checker(exc):
                    return True
            except Exception:
                pass

    policy = normalize_backup_retry_policy(retry_policy)
    status_code = _extract_status_code(exc)
    if status_code is not None and status_code in set(
        int(code) for code in policy.get("retryable_status_codes", DEFAULT_BACKUP_RETRY_POLICY["retryable_status_codes"])
    ):
        return True

    classification = str(getattr(exc, "classification", "") or getattr(exc, "error_type", "") or "").strip().lower()
    if classification and classification in DEFAULT_RETRYABLE_CLASSIFICATIONS:
        return True

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException, httpx.TransportError)):
        return True

    return False
