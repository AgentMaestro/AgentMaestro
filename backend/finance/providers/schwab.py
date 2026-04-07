from __future__ import annotations

import base64
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from datetime import date as date_class
from typing import Any

import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from django.conf import settings
from django.utils import timezone
from logging_utils import get_app_logger, scrub_sensitive_text, scrub_sensitive_value

from finance.models import SchwabOAuthCredential

from .base import BrokerageProvider, MarketDataProvider

_FERNET_SALT = b"agentmaestro-schwab-token-v1"
logger = get_app_logger(__name__)


@dataclass(frozen=True, slots=True)
class SchwabProviderConfig:
    market_data_url: str
    market_data_key: str
    market_data_secret: str
    market_data_callback_url: str
    trader_url: str
    trader_key: str
    trader_secret: str
    authorize_url: str
    token_url: str
    callback_url: str
    oauth_scope: str


def _build_config() -> SchwabProviderConfig:
    return SchwabProviderConfig(
        market_data_url=getattr(settings, "SCHWAB_MARKET_DATA_URL", "https://api.schwabapi.com/marketdata/v1").rstrip("/"),
        market_data_key=getattr(settings, "SCHWAB_MARKET_DATA_KEY", ""),
        market_data_secret=getattr(settings, "SCHWAB_MARKET_DATA_SECRET", ""),
        market_data_callback_url=getattr(settings, "SCHWAB_MARKET_DATA_CALLBACK_URL", ""),
        trader_url=getattr(settings, "SCHWAB_TRADER_URL", "https://api.schwabapi.com/trader/v1").rstrip("/"),
        trader_key=getattr(settings, "SCHWAB_TRADER_KEY", ""),
        trader_secret=getattr(settings, "SCHWAB_TRADER_SECRET", ""),
        authorize_url=getattr(settings, "SCHWAB_OAUTH_AUTHORIZE_URL", "https://api.schwabapi.com/v1/oauth/authorize").rstrip("/"),
        token_url=getattr(settings, "SCHWAB_OAUTH_TOKEN_URL", "https://api.schwabapi.com/v1/oauth/token").rstrip("/"),
        callback_url=getattr(settings, "SCHWAB_CALLBACK_URL", ""),
        oauth_scope=getattr(settings, "SCHWAB_OAUTH_SCOPE", "readonly"),
    )


def _derive_fernet() -> Fernet:
    secret = str(getattr(settings, "SECRET_KEY", "") or "agentmaestro-schwab").encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        iterations=390000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret))
    return Fernet(key)


def _encrypt_payload(payload: dict[str, Any]) -> str:
    return _derive_fernet().encrypt(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")).decode("utf-8")


def _decrypt_payload(payload: str) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        decrypted = _derive_fernet().decrypt(payload.encode("utf-8"))
        data = json.loads(decrypted.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mask_secret(value: str, keep: int = 4) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}...{text[-keep:]}"


def _build_authorize_url(*, authorize_url: str, client_id: str, callback_url: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": callback_url,
    }
    query = httpx.QueryParams(params)
    return f"{authorize_url}?{query}"


def _exchange_authorization_code(*, code: str, client_id: str, client_secret: str, callback_url: str, token_url: str) -> dict[str, Any]:
    masked_client_id = _mask_secret(client_id)
    masked_code = _mask_secret(code, keep=6)
    request_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
    }
    logger.info(
        "schwab token exchange start client_id=%s callback_url=%s token_url=%s code=%s request_payload=%s auth=basic",
        masked_client_id,
        callback_url,
        token_url,
        masked_code,
        scrub_sensitive_value(request_payload),
    )
    with httpx.Client(timeout=30.0) as client:
        try:
            response = client.post(
                token_url,
                data=request_payload,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token_payload = response.json()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            response_text = ""
            response_json: dict[str, Any] | list[Any] | None = None
            try:
                response_text = str(response.text or "").strip()
            except Exception:
                response_text = ""
            try:
                parsed_response = response.json()
                if isinstance(parsed_response, (dict, list)):
                    response_json = scrub_sensitive_value(parsed_response)
            except Exception:
                response_json = None
            logger.exception(
                "schwab token exchange http error status=%s client_id=%s callback_url=%s token_url=%s request_payload=%s response_json=%s response_body=%s",
                getattr(response, "status_code", ""),
                masked_client_id,
                callback_url,
                token_url,
                scrub_sensitive_value(request_payload),
                response_json,
                scrub_sensitive_text(response_text[:2000]),
            )
            raise
        except httpx.RequestError:
            logger.exception(
                "schwab token exchange request error client_id=%s callback_url=%s token_url=%s request_payload=%s",
                masked_client_id,
                callback_url,
                token_url,
                scrub_sensitive_value(request_payload),
            )
            raise
        except ValueError:
            response_text = ""
            try:
                response_text = str(response.text or "").strip()
            except Exception:
                response_text = ""
            logger.exception(
                "schwab token exchange decode error client_id=%s callback_url=%s token_url=%s request_payload=%s response_body=%s",
                masked_client_id,
                callback_url,
                token_url,
                scrub_sensitive_value(request_payload),
                scrub_sensitive_text(response_text[:2000]),
            )
            raise
    logger.info(
        "schwab token exchange ok client_id=%s callback_url=%s token_url=%s",
        masked_client_id,
        callback_url,
        token_url,
    )
    return token_payload


def _refresh_schwab_token(refresh_token: str, *, client_id: str, client_secret: str, token_url: str) -> dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            token_url,
            data=payload,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


def build_schwab_authorize_url() -> str:
    config = _build_config()
    if not config.trader_key:
        logger.error(
            "schwab authorize url missing client_id authorize_url=%s redirect_uri=%s",
            config.authorize_url,
            config.callback_url,
        )
        raise RuntimeError("SCHWAB_TRADER_KEY is not configured.")
    if not config.callback_url:
        logger.error(
            "schwab authorize url missing redirect_uri authorize_url=%s client_id=%s",
            config.authorize_url,
            _mask_secret(config.trader_key),
        )
        raise RuntimeError("SCHWAB_CALLBACK_URL is not configured.")
    authorize_url = _build_authorize_url(
        authorize_url=config.authorize_url,
        client_id=config.trader_key,
        callback_url=config.callback_url,
    )
    masked_client_id = _mask_secret(config.trader_key)
    masked_query = authorize_url.split("?", 1)[1] if "?" in authorize_url else ""
    if config.trader_key:
        masked_query = masked_query.replace(config.trader_key, _mask_secret(config.trader_key))
    if config.callback_url:
        masked_query = masked_query.replace(config.callback_url, _mask_secret(config.callback_url, keep=8))
    logger.info(
        "schwab authorize url built authorize_url=%s client_id=%s redirect_uri=%s query=%s",
        config.authorize_url,
        masked_client_id,
        config.callback_url,
        masked_query,
    )
    return authorize_url


def exchange_schwab_authorization_code(*, code: str) -> dict[str, Any]:
    config = _build_config()
    logger.info(
        "schwab authorization exchange config client_id=%s callback_url=%s token_url=%s",
        _mask_secret(config.trader_key),
        config.callback_url,
        config.token_url,
    )
    return _exchange_authorization_code(
        code=code,
        client_id=config.trader_key,
        client_secret=config.trader_secret,
        callback_url=config.callback_url,
        token_url=config.token_url,
    )


def build_schwab_market_authorize_url() -> str:
    config = _build_config()
    if not config.market_data_key:
        logger.error(
            "schwab market authorize url missing client_id authorize_url=%s redirect_uri=%s",
            config.authorize_url,
            config.market_data_callback_url,
        )
        raise RuntimeError("SCHWAB_MARKET_DATA_KEY is not configured.")
    if not config.market_data_secret:
        logger.error(
            "schwab market authorize url missing client_secret authorize_url=%s client_id=%s",
            config.authorize_url,
            _mask_secret(config.market_data_key),
        )
        raise RuntimeError("SCHWAB_MARKET_DATA_SECRET is not configured.")
    if not config.market_data_callback_url:
        logger.error(
            "schwab market authorize url missing redirect_uri authorize_url=%s client_id=%s",
            config.authorize_url,
            _mask_secret(config.market_data_key),
        )
        raise RuntimeError("SCHWAB_MARKET_DATA_CALLBACK_URL is not configured.")
    authorize_url = _build_authorize_url(
        authorize_url=config.authorize_url,
        client_id=config.market_data_key,
        callback_url=config.market_data_callback_url,
    )
    masked_client_id = _mask_secret(config.market_data_key)
    masked_query = authorize_url.split("?", 1)[1] if "?" in authorize_url else ""
    if config.market_data_key:
        masked_query = masked_query.replace(config.market_data_key, _mask_secret(config.market_data_key))
    if config.market_data_callback_url:
        masked_query = masked_query.replace(config.market_data_callback_url, _mask_secret(config.market_data_callback_url, keep=8))
    logger.info(
        "schwab market authorize url built authorize_url=%s client_id=%s redirect_uri=%s query=%s",
        config.authorize_url,
        masked_client_id,
        config.market_data_callback_url,
        masked_query,
    )
    return authorize_url


def exchange_schwab_market_authorization_code(*, code: str) -> dict[str, Any]:
    config = _build_config()
    logger.info(
        "schwab market authorization exchange config client_id=%s callback_url=%s token_url=%s",
        _mask_secret(config.market_data_key),
        config.market_data_callback_url,
        config.token_url,
    )
    return _exchange_authorization_code(
        code=code,
        client_id=config.market_data_key,
        client_secret=config.market_data_secret,
        callback_url=config.market_data_callback_url,
        token_url=config.token_url,
    )


def store_schwab_credential(
    *,
    token_payload: dict[str, Any],
    workspace=None,
    owner=None,
    primary_account_hash: str = "",
    account_hashes: list[dict[str, Any]] | list[str] | None = None,
    source: str = "schwab_oauth_callback",
) -> SchwabOAuthCredential:
    access_expires_in = int(token_payload.get("expires_in") or token_payload.get("expiresIn") or 0)
    expires_at = timezone.now() + timedelta(seconds=max(0, access_expires_in)) if access_expires_in else None
    token = SchwabOAuthCredential.objects.create(
        workspace=workspace,
        owner=owner,
        is_active=True,
        token_payload_encrypted=_encrypt_payload(token_payload),
        expires_at=expires_at,
        token_type=str(token_payload.get("token_type") or token_payload.get("tokenType") or "").strip(),
        scope=str(token_payload.get("scope") or "").strip(),
        primary_account_hash=primary_account_hash,
        account_hashes=account_hashes or [],
        raw_payload=token_payload,
        metadata={"source": source},
    )
    return token


class SchwabMarketDataProvider(MarketDataProvider):
    provider_name = "schwab_market"
    credential_source = "schwab_market_oauth_callback"

    def __init__(self, *, config: SchwabProviderConfig | None = None, timeout_seconds: float = 20.0) -> None:
        self.config = config or _build_config()
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def _client(self, access_token: str) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "AgentMaestro/finance",
            "Authorization": f"Bearer {access_token}",
        }
        with httpx.Client(base_url=self.config.market_data_url, timeout=self.timeout_seconds, headers=headers) as client:
            yield client

    def _credential_qs(self):
        return (
            SchwabOAuthCredential.objects.filter(is_active=True, metadata__source=self.credential_source)
            .order_by("-updated_at", "-created_at")
        )

    def _get_credential(self) -> SchwabOAuthCredential | None:
        return self._credential_qs().first()

    def _persist_credential(self, credential: SchwabOAuthCredential, payload: dict[str, Any]) -> SchwabOAuthCredential:
        expires_in = int(payload.get("expires_in") or payload.get("expiresIn") or 0)
        credential.token_payload_encrypted = _encrypt_payload(payload)
        credential.raw_payload = payload
        credential.token_type = str(payload.get("token_type") or payload.get("tokenType") or credential.token_type or "").strip()
        credential.scope = str(payload.get("scope") or credential.scope or "").strip()
        if expires_in:
            credential.expires_at = timezone.now() + timedelta(seconds=max(0, expires_in))
        credential.save(
            update_fields=[
                "token_payload_encrypted",
                "raw_payload",
                "token_type",
                "scope",
                "expires_at",
                "updated_at",
            ]
        )
        return credential

    def _load_token_payload(self) -> tuple[SchwabOAuthCredential | None, dict[str, Any]]:
        credential = self._get_credential()
        if credential is None:
            return None, {}
        payload = dict(credential.raw_payload or {})
        if not payload and credential.token_payload_encrypted:
            payload = _decrypt_payload(credential.token_payload_encrypted)
        return credential, payload

    def _ensure_access_token(self) -> str | None:
        credential, payload = self._load_token_payload()
        if credential is None:
            return None

        access_token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
        refresh_token = str(payload.get("refresh_token") or payload.get("refreshToken") or "").strip()
        expires_at = credential.expires_at
        if access_token and expires_at and expires_at > timezone.now():
            return access_token

        if refresh_token:
            try:
                refreshed = _refresh_schwab_token(
                    refresh_token,
                    client_id=self.config.market_data_key,
                    client_secret=self.config.market_data_secret,
                    token_url=self.config.token_url,
                )
            except Exception:
                return access_token or None
            credential = self._persist_credential(credential, refreshed)
            payload = dict(credential.raw_payload or {})
            access_token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
            return access_token or None

        return access_token or None

    def _request_json(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]] | Any:
        access_token = self._ensure_access_token()
        if not access_token:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "Schwab OAuth credential is not configured yet.",
            }
        started = time.perf_counter()
        try:
            with self._client(access_token) as client:
                response = client.request(method, path, params=params)
                response.raise_for_status()
                payload = response.json()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("schwab market request ok method=%s path=%s elapsed_ms=%s", method, path, elapsed_ms)
                return payload
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            response = exc.response
            response_text = ""
            response_payload: Any = {}
            try:
                response_payload = response.json()
            except Exception:
                response_payload = {}
            try:
                response_text = (response.text or "").strip()
            except Exception:
                response_text = ""
            error_payload = scrub_sensitive_value(response_payload) if response_payload else {}
            request_params = scrub_sensitive_value(params or {})
            logger.warning(
                "schwab market request failed method=%s path=%s elapsed_ms=%s status=%s request_id=%s params=%s response=%s response_text=%s",
                method,
                path,
                elapsed_ms,
                response.status_code if response is not None else "",
                response.headers.get("x-request-id") if response is not None else "",
                request_params,
                error_payload,
                response_text[:500],
            )
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(exc),
                "status_code": response.status_code if response is not None else None,
                "request_id": response.headers.get("x-request-id") if response is not None else "",
                "response_payload": error_payload,
                "response_text": response_text[:500],
            }
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("schwab market request failed method=%s path=%s elapsed_ms=%s error=%s", method, path, elapsed_ms, exc)
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(exc),
            }

    @staticmethod
    def _first_quote_dict(payload: Any, symbol: str) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key in (symbol, symbol.upper(), symbol.lower()):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
            for key in ("quotes", "quote", "results", "ticker"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value[0]
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return {}

    @staticmethod
    def _first_positive_number(*values: Any) -> float | None:
        for value in values:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric > 0:
                return numeric
        return None

    @staticmethod
    def _quote_payload_for_symbol(payload: Any, symbol: str) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key in (symbol, symbol.upper(), symbol.lower()):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
            for key in ("quotes", "quote", "results", "ticker"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value[0]
            if len(payload) == 1:
                only_value = next(iter(payload.values()))
                if isinstance(only_value, dict):
                    return only_value
            return {}
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return {}

    def _normalize_quote_response(self, symbol: str, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() == "unavailable":
            return {
                "provider": self.provider_name,
                "symbol": symbol,
                "status": payload.get("status") or "unavailable",
                "message": payload.get("message") or "Schwab quote request failed.",
                "status_code": payload.get("status_code"),
            }
        quote_payload = self._quote_payload_for_symbol(payload, symbol)
        if not quote_payload:
            payload_dict = payload if isinstance(payload, dict) else {}
            return {
                "provider": self.provider_name,
                "symbol": symbol,
                "status": payload_dict.get("status") or "unavailable",
                "message": "Schwab quote response did not include the requested symbol.",
                "request_id": payload_dict.get("request_id") or payload_dict.get("requestId") or "",
            }
        payload_dict = payload if isinstance(payload, dict) else {}
        quote = quote_payload.get("quote") or quote_payload.get("regular") or quote_payload.get("regularMarket") or quote_payload
        last_quote = quote_payload.get("lastQuote") or quote_payload.get("last_quote") or {}
        last_trade = quote_payload.get("lastTrade") or quote_payload.get("last_trade") or {}
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "request_id": payload_dict.get("request_id") or payload_dict.get("requestId") or "",
            "status": payload_dict.get("status") or "ok",
            "as_of": datetime.utcnow().isoformat() + "Z",
            "ticker": quote_payload.get("symbol") or quote_payload.get("ticker") or symbol,
            "snapshot": quote_payload,
            "last_quote": last_quote,
            "last_trade": last_trade,
            "quote": {
                "bid": quote.get("bidPrice") or quote.get("bid") or last_quote.get("bid") or last_quote.get("bp"),
                "ask": quote.get("askPrice") or quote.get("ask") or last_quote.get("ask") or last_quote.get("ap"),
                "bid_size": quote.get("bidSize") or quote.get("bid_size") or last_quote.get("bid_size") or last_quote.get("bs"),
                "ask_size": quote.get("askSize") or quote.get("ask_size") or last_quote.get("ask_size") or last_quote.get("as"),
                "last": self._first_positive_number(
                    quote.get("lastPrice"),
                    quote.get("last"),
                    quote.get("mark"),
                    last_trade.get("price"),
                    last_trade.get("p"),
                    quote.get("closePrice"),
                    quote.get("close"),
                    quote.get("regularMarketLastPrice"),
                    quote.get("bid"),
                    quote.get("ask"),
                ),
                "volume": quote.get("totalVolume") or quote.get("volume") or last_trade.get("size"),
                "updated": quote.get("quoteTime") or quote.get("tradeTime") or quote.get("updated") or last_quote.get("t") or last_trade.get("t"),
            },
        }

    def get_quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        normalized_symbols: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            candidate = self._normalize_symbol(symbol)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized_symbols.append(candidate)
        if not normalized_symbols:
            return {}
        payload = self._request_json(
            "GET",
            "/quotes",
            params={
                "symbols": ",".join(normalized_symbols),
                "indicative": "false",
            },
        )
        if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() == "unavailable":
            return {
                symbol: {
                    "provider": self.provider_name,
                    "symbol": symbol,
                    "status": payload.get("status") or "unavailable",
                    "message": payload.get("message") or "Schwab quote request failed.",
                    "status_code": payload.get("status_code"),
                }
                for symbol in normalized_symbols
            }
        return {symbol: self._normalize_quote_response(symbol, payload) for symbol in normalized_symbols}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        if not symbol:
            return {
                "provider": self.provider_name,
                "symbol": "",
                "status": "unavailable",
                "message": "Schwab quote symbol is empty.",
            }
        return self.get_quotes([symbol]).get(
            symbol,
            {
                "provider": self.provider_name,
                "symbol": symbol,
                "status": "unavailable",
                "message": "Schwab quote request returned no data.",
            },
        )

    def get_history(self, symbol: str, *, timeframe: str, start=None, end=None) -> dict[str, Any]:
        symbol = self._normalize_symbol(symbol)
        normalized_timeframe = self._normalize_timeframe(timeframe)

        def _to_epoch_ms(value: Any) -> int | None:
            if value is None:
                return None
            if isinstance(value, (int, float)):
                numeric = int(value)
                return numeric if numeric > 0 else None
            if isinstance(value, datetime):
                candidate = value
                if candidate.tzinfo is None:
                    candidate = candidate.replace(tzinfo=dt_timezone.utc)
                return int(candidate.astimezone(dt_timezone.utc).timestamp() * 1000)
            return None

        period_type = "year"
        period = 1
        frequency_type = "daily"
        frequency = 1
        if normalized_timeframe in {"minute", "1m", "5m", "10m", "15m", "30m", "intraday", "hour", "hourly"}:
            period_type = "day"
            period = 10
            frequency_type = "minute"
            if normalized_timeframe in {"5m"}:
                frequency = 5
            elif normalized_timeframe in {"10m"}:
                frequency = 10
            elif normalized_timeframe in {"15m"}:
                frequency = 15
            elif normalized_timeframe in {"30m", "hour", "hourly"}:
                frequency = 30
            else:
                frequency = 1
        elif normalized_timeframe in {"weekly", "1w", "week"}:
            period_type = "year"
            period = 1
            frequency_type = "weekly"
            frequency = 1
        elif normalized_timeframe in {"monthly", "1mo", "month"}:
            period_type = "year"
            period = 1
            frequency_type = "monthly"
            frequency = 1

        end_dt = end or datetime.now(dt_timezone.utc)
        start_dt = start
        if start_dt is None:
            if period_type == "day":
                start_dt = end_dt - timedelta(days=10)
            else:
                start_dt = end_dt - timedelta(days=365)
        params: dict[str, Any] = {
            "symbol": symbol,
            "periodType": period_type,
            "period": period,
            "frequencyType": frequency_type,
            "frequency": frequency,
            "needExtendedHoursData": "false",
            "needPreviousClose": "true",
        }
        start_ms = _to_epoch_ms(start_dt)
        end_ms = _to_epoch_ms(end_dt)
        if start_ms is not None:
            params["startDate"] = start_ms
        if end_ms is not None:
            params["endDate"] = end_ms

        payload = self._request_json("GET", "/pricehistory", params=params)
        if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() == "unavailable":
            return payload

        payload_dict = payload if isinstance(payload, dict) else {}
        candles = payload_dict.get("candles") if isinstance(payload, dict) else []
        normalized_candles: list[dict[str, Any]] = []
        if isinstance(candles, list):
            for candle in candles:
                if not isinstance(candle, dict):
                    continue
                normalized_candles.append(
                    {
                        "timestamp": candle.get("datetime"),
                        "open": candle.get("open"),
                        "high": candle.get("high"),
                        "low": candle.get("low"),
                        "close": candle.get("close"),
                        "volume": candle.get("volume"),
                    }
                )
        previous_close = payload_dict.get("previousClose")
        previous_close_date = payload_dict.get("previousCloseDate")
        bars = [
            {
                "timestamp": candle["timestamp"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
                "vwap": None,
                "transactions": None,
            }
            for candle in normalized_candles
        ]
        return {
            "provider": self.provider_name,
            "symbol": symbol,
            "timeframe": normalized_timeframe or frequency_type,
            "period_type": period_type,
            "period": period,
            "frequency_type": frequency_type,
            "frequency": frequency,
            "start": start_ms,
            "end": end_ms,
            "request_id": payload_dict.get("request_id") or payload_dict.get("requestId") or "",
            "status": payload_dict.get("status") or "ok",
            "previous_close": previous_close,
            "previous_close_date": previous_close_date,
            "candles": normalized_candles,
            "bars": bars,
            "count": len(bars),
        }

    def get_market_hours(self, markets: list[str], *, date: date_class | None = None) -> dict[str, Any]:
        normalized_markets: list[str] = []
        seen: set[str] = set()
        for market in markets:
            candidate = str(market or "").strip().lower()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized_markets.append(candidate)
        if not normalized_markets:
            normalized_markets = ["equity"]
        params: dict[str, Any] = {"markets": normalized_markets}
        if date is not None:
            params["date"] = date.isoformat()
        payload = self._request_json("GET", "/markets", params=params)
        if isinstance(payload, dict):
            return {
                "provider": self.provider_name,
                "status": payload.get("status") or "ok",
                "request_id": payload.get("request_id") or payload.get("requestId") or "",
                "markets": normalized_markets,
                "date": date.isoformat() if date is not None else "",
                "payload": payload,
            }
        return {
            "provider": self.provider_name,
            "status": "unavailable",
            "message": "Schwab market hours request returned no data.",
            "markets": normalized_markets,
            "date": date.isoformat() if date is not None else "",
            "payload": {},
        }

    def get_news(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data news is not wired yet.")

    def get_options_chain(self, symbol: str, *, expiration: str | None = None) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data options chain is not wired yet.")

    def get_option_quote(self, contract_symbol: str) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data option quote is not wired yet.")

    def get_option_greeks(self, contract_symbol: str) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data option greeks are not wired yet.")

    def price_option_black_scholes(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data pricing is not wired yet.")

    def price_option_binomial(self, *, symbol: str, strike: float, spot: float, rate: float, volatility: float, time_to_expiry_years: float, option_type: str, steps: int = 100) -> dict[str, Any]:
        raise NotImplementedError("Schwab market-data pricing is not wired yet.")


class SchwabBrokerageProvider(BrokerageProvider):
    provider_name = "schwab"
    credential_source = "schwab_oauth_callback"

    def __init__(
        self,
        *,
        config: SchwabProviderConfig | None = None,
        workspace_id: str | None = None,
        owner_id: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.config = config or _build_config()
        self.workspace_id = str(workspace_id or "").strip() or None
        self.owner_id = str(owner_id or "").strip() or None
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def _client(self, access_token: str) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "AgentMaestro/finance",
            "Authorization": f"Bearer {access_token}",
        }
        with httpx.Client(base_url=self.config.trader_url, timeout=self.timeout_seconds, headers=headers) as client:
            yield client

    def _credential_qs(self):
        qs = SchwabOAuthCredential.objects.filter(is_active=True, metadata__source=self.credential_source).order_by("-updated_at", "-created_at")
        if self.workspace_id:
            qs = qs.filter(workspace_id=self.workspace_id)
        if self.owner_id:
            qs = qs.filter(owner_id=self.owner_id)
        return qs

    def _get_credential(self) -> SchwabOAuthCredential | None:
        return self._credential_qs().first()

    def _persist_credential(self, credential: SchwabOAuthCredential, payload: dict[str, Any]) -> SchwabOAuthCredential:
        expires_in = int(payload.get("expires_in") or payload.get("expiresIn") or 0)
        credential.token_payload_encrypted = _encrypt_payload(payload)
        credential.raw_payload = payload
        credential.token_type = str(payload.get("token_type") or payload.get("tokenType") or credential.token_type or "").strip()
        credential.scope = str(payload.get("scope") or credential.scope or "").strip()
        if expires_in:
            credential.expires_at = timezone.now() + timedelta(seconds=max(0, expires_in))
        credential.save(
            update_fields=[
                "token_payload_encrypted",
                "raw_payload",
                "token_type",
                "scope",
                "expires_at",
                "updated_at",
            ]
        )
        return credential

    def _load_token_payload(self) -> tuple[SchwabOAuthCredential | None, dict[str, Any]]:
        credential = self._get_credential()
        if credential is None:
            return None, {}
        payload = dict(credential.raw_payload or {})
        if not payload and credential.token_payload_encrypted:
            payload = _decrypt_payload(credential.token_payload_encrypted)
        return credential, payload

    def _ensure_access_token(self) -> tuple[SchwabOAuthCredential | None, dict[str, Any], str | None]:
        credential, payload = self._load_token_payload()
        if credential is None:
            return None, {}, None

        access_token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
        refresh_token = str(payload.get("refresh_token") or payload.get("refreshToken") or "").strip()
        expires_at = credential.expires_at
        if access_token and expires_at and expires_at > timezone.now():
            return credential, payload, access_token

        if refresh_token:
            try:
                refreshed = _refresh_schwab_token(
                    refresh_token,
                    client_id=self.config.trader_key,
                    client_secret=self.config.trader_secret,
                    token_url=self.config.token_url,
                )
            except Exception:
                return credential, payload, access_token or None
            credential = self._persist_credential(credential, refreshed)
            payload = dict(credential.raw_payload or {})
            access_token = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
            return credential, payload, access_token or None

        return credential, payload, access_token or None

    @staticmethod
    def _normalize_account_hashes(account_numbers: Any) -> list[dict[str, str]]:
        if isinstance(account_numbers, list):
            items = account_numbers
        elif isinstance(account_numbers, dict):
            items = account_numbers.get("accounts") or account_numbers.get("results") or []
        else:
            items = []
        normalized: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            account_number = str(item.get("accountNumber") or item.get("account_number") or "").strip()
            hash_value = str(item.get("hashValue") or item.get("hash_value") or "").strip()
            if account_number or hash_value:
                normalized.append({"accountNumber": account_number, "hashValue": hash_value})
        return normalized

    def _choose_primary_account_hash(self, account_hashes: list[dict[str, str]], credential: SchwabOAuthCredential | None) -> str:
        if credential and credential.primary_account_hash:
            for item in account_hashes:
                if item.get("hashValue") == credential.primary_account_hash:
                    return credential.primary_account_hash
        for item in account_hashes:
            hash_value = str(item.get("hashValue") or "").strip()
            if hash_value:
                return hash_value
        return ""

    def _resolve_account_hash(self, account_id: str | None = None) -> str:
        credential, payload, access_token = self._ensure_access_token()
        if not access_token:
            return ""
        if account_id:
            candidate = str(account_id).strip()
            if candidate:
                account_hashes = self._normalize_account_hashes(credential.account_hashes if credential else [])
                for item in account_hashes:
                    if candidate == str(item.get("hashValue") or "").strip():
                        return candidate
                    if candidate == str(item.get("accountNumber") or "").strip():
                        mapped = str(item.get("hashValue") or "").strip()
                        if mapped:
                            return mapped
                return candidate
        hashes = self._normalize_account_hashes(credential.account_hashes if credential else [])
        if hashes:
            return self._choose_primary_account_hash(hashes, credential)
        return ""

    @staticmethod
    def _extract_account_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            if "securitiesAccount" in payload and isinstance(payload["securitiesAccount"], dict):
                return payload["securitiesAccount"]
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            first = payload[0]
            if "securitiesAccount" in first and isinstance(first["securitiesAccount"], dict):
                return first["securitiesAccount"]
            return first
        return {}

    @staticmethod
    def _extract_transactions(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("transactions", "results", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_orders(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("orders", "results", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _request_json(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]] | Any:
        credential, _, access_token = self._ensure_access_token()
        if not access_token:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "Schwab OAuth credential is not configured yet.",
            }
        started = time.perf_counter()
        try:
            with self._client(access_token) as client:
                response = client.request(method, path, params=params)
                response.raise_for_status()
                payload = response.json()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.info("schwab request ok method=%s path=%s elapsed_ms=%s", method, path, elapsed_ms)
                return payload
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            response = exc.response
            response_text = ""
            response_payload: Any = {}
            try:
                response_payload = response.json()
            except Exception:
                response_payload = {}
            try:
                response_text = (response.text or "").strip()
            except Exception:
                response_text = ""
            error_payload = scrub_sensitive_value(response_payload) if response_payload else {}
            request_params = scrub_sensitive_value(params or {})
            logger.warning(
                "schwab request failed method=%s path=%s elapsed_ms=%s status=%s request_id=%s params=%s response=%s response_text=%s",
                method,
                path,
                elapsed_ms,
                response.status_code if response is not None else "",
                response.headers.get("x-request-id") if response is not None else "",
                request_params,
                error_payload,
                response_text[:500],
            )
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(exc),
                "status_code": response.status_code if response is not None else None,
                "request_id": response.headers.get("x-request-id") if response is not None else "",
                "response_payload": error_payload,
                "response_text": response_text[:500],
            }
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("schwab request failed method=%s path=%s elapsed_ms=%s error=%s", method, path, elapsed_ms, exc)
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(exc),
            }

    def list_accounts(self) -> dict[str, Any]:
        credential, _, access_token = self._ensure_access_token()
        if not access_token:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "Schwab OAuth credential is not configured yet.",
                "accounts": [],
            }
        started = time.perf_counter()
        try:
            with self._client(access_token) as client:
                response = client.get("/accounts/accountNumbers")
                response.raise_for_status()
                payload = response.json()
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("schwab accounts ok elapsed_ms=%s", elapsed_ms)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("schwab accounts failed elapsed_ms=%s error=%s", elapsed_ms, exc)
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(exc),
                "accounts": [],
            }

        account_hashes = self._normalize_account_hashes(payload)
        primary_account_hash = self._choose_primary_account_hash(account_hashes, credential)
        if credential is not None:
            credential.account_hashes = account_hashes
            if primary_account_hash:
                credential.primary_account_hash = primary_account_hash
            credential.raw_payload = {
                **(credential.raw_payload or {}),
                "account_numbers": account_hashes,
            }
            credential.save(update_fields=["account_hashes", "primary_account_hash", "raw_payload", "updated_at"])

        return {
            "provider": self.provider_name,
            "status": "ok",
            "accounts": account_hashes,
            "primary_account_hash": primary_account_hash,
            "as_of": timezone.now().isoformat(),
        }

    def get_balances(self, account_id: str | None = None) -> dict[str, Any]:
        account_hash = self._resolve_account_hash(account_id)
        if not account_hash:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "No Schwab account hash is available yet.",
                "balances": {},
            }
        payload = self._request_json("GET", f"/accounts/{account_hash}")
        if isinstance(payload, dict) and payload.get("status") == "unavailable":
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(payload.get("message") or "Unable to fetch balances."),
                "balances": {},
            }
        account = self._extract_account_payload(payload)
        balances = {
            "initial": account.get("initialBalances") or {},
            "current": account.get("currentBalances") or {},
            "projected": account.get("projectedBalances") or {},
        }
        return {
            "provider": self.provider_name,
            "status": "ok",
            "account_number": str(account.get("accountNumber") or ""),
            "account_hash": account_hash,
            "balances": balances,
            "raw": account,
            "as_of": timezone.now().isoformat(),
        }

    def list_positions(self, account_id: str | None = None) -> dict[str, Any]:
        account_hash = self._resolve_account_hash(account_id)
        if not account_hash:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "No Schwab account hash is available yet.",
                "positions": [],
            }
        payload = self._request_json("GET", f"/accounts/{account_hash}", params={"fields": "positions"})
        if isinstance(payload, dict) and payload.get("status") == "unavailable":
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(payload.get("message") or "Unable to fetch positions."),
                "positions": [],
            }
        account = self._extract_account_payload(payload)
        positions = account.get("positions") or []
        normalized_positions: list[dict[str, Any]] = []
        for position in positions if isinstance(positions, list) else []:
            if not isinstance(position, dict):
                continue
            instrument = position.get("instrument") or {}
            symbol = str(instrument.get("symbol") or "").strip()
            normalized_positions.append(
                {
                    "symbol": symbol,
                    "description": instrument.get("description") or "",
                    "cusip": instrument.get("cusip") or "",
                    "quantity": float(position.get("longQuantity") or position.get("shortQuantity") or 0),
                    "long_quantity": float(position.get("longQuantity") or 0),
                    "short_quantity": float(position.get("shortQuantity") or 0),
                    "average_price": float(position.get("averagePrice") or position.get("averageLongPrice") or 0),
                    "market_value": float(position.get("marketValue") or 0),
                    "current_day_profit_loss": float(position.get("currentDayProfitLoss") or 0),
                    "current_day_profit_loss_percentage": float(position.get("currentDayProfitLossPercentage") or 0),
                    "raw": position,
                }
            )
        return {
            "provider": self.provider_name,
            "status": "ok",
            "account_number": str(account.get("accountNumber") or ""),
            "account_hash": account_hash,
            "positions": normalized_positions,
            "balances": {
                "initial": account.get("initialBalances") or {},
                "current": account.get("currentBalances") or {},
                "projected": account.get("projectedBalances") or {},
            },
            "raw": account,
            "as_of": timezone.now().isoformat(),
        }

    def list_activity(self, account_id: str | None = None, *, limit: int = 50) -> dict[str, Any]:
        account_hash = self._resolve_account_hash(account_id)
        if not account_hash:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "No Schwab account hash is available yet.",
                "transactions": [],
            }
        lookback_days = max(1, int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LOOKBACK_DAYS", 365)))
        end_date = timezone.now().astimezone(dt_timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "types": "TRADE",
        }
        payload = self._request_json("GET", f"/accounts/{account_hash}/transactions", params=params)
        if isinstance(payload, dict) and payload.get("status") == "unavailable":
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(payload.get("message") or "Unable to fetch transactions."),
                "status_code": payload.get("status_code"),
                "response_text": payload.get("response_text"),
                "transactions": [],
            }
        transactions = self._extract_transactions(payload)
        normalized_transactions = transactions[: max(1, int(limit or 50))]
        return {
            "provider": self.provider_name,
            "status": "ok",
            "account_hash": account_hash,
            "transactions": normalized_transactions,
            "raw": payload,
            "as_of": timezone.now().isoformat(),
        }

    def list_orders(self, account_id: str | None = None, *, limit: int = 100) -> dict[str, Any]:
        account_hash = self._resolve_account_hash(account_id)
        if not account_hash:
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": "No Schwab account hash is available yet.",
                "orders": [],
            }
        lookback_days = max(1, int(getattr(settings, "FINANCE_BROKERAGE_TRANSACTION_LOOKBACK_DAYS", 365)))
        end_date = timezone.now().astimezone(dt_timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)
        params = {
            "fromEnteredTime": start_date.isoformat(),
            "toEnteredTime": end_date.isoformat(),
            "maxResults": max(1, int(limit or 100)),
            "status": "FILLED",
        }
        payload = self._request_json("GET", f"/accounts/{account_hash}/orders", params=params)
        if isinstance(payload, dict) and payload.get("status") == "unavailable":
            return {
                "provider": self.provider_name,
                "status": "unavailable",
                "message": str(payload.get("message") or "Unable to fetch orders."),
                "status_code": payload.get("status_code"),
                "response_text": payload.get("response_text"),
                "orders": [],
            }
        orders = self._extract_orders(payload)
        normalized_orders = orders[: max(1, int(limit or 100))]
        return {
            "provider": self.provider_name,
            "status": "ok",
            "account_hash": account_hash,
            "orders": normalized_orders,
            "raw": payload,
            "as_of": timezone.now().isoformat(),
        }

    def preview_order(self, *, account_id: str, symbol: str, side: str, quantity: float, order_type: str, time_in_force: str, limit_price: float | None = None, stop_price: float | None = None) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": "not_wired",
            "message": "Order preview is reserved for phase 2.",
        }

    def place_order(self, *, account_id: str, symbol: str, side: str, quantity: float, order_type: str, time_in_force: str, limit_price: float | None = None, stop_price: float | None = None) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": "not_wired",
            "message": "Order placement is reserved for phase 2.",
        }

    def cancel_order(self, *, account_id: str, order_id: str) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": "not_wired",
            "message": "Order cancellation is reserved for phase 2.",
        }

    def get_order_status(self, *, account_id: str, order_id: str) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": "not_wired",
            "message": "Order status is reserved for phase 2.",
        }
