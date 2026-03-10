import json
import os
import time
from typing import Iterable, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

try:
    import redis
except ImportError as exc:  # pragma: no cover
    raise CommandError("The redis package is required for this command.") from exc


DEFAULT_PATTERNS = [
    "*tool*",
    "*result*",
    "*run*",
    "*channel*",
]


def _coerce_redis_url(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_redis_url_from_channel_layers() -> Optional[str]:
    channel_layers = getattr(settings, "CHANNEL_LAYERS", {}) or {}
    default_layer = channel_layers.get("default", {}) or {}
    config = default_layer.get("CONFIG", {}) or {}
    hosts = config.get("hosts") or []

    if not hosts:
        return None

    first = hosts[0]
    if isinstance(first, str):
        return _coerce_redis_url(first)

    if isinstance(first, (list, tuple)) and first:
        return _coerce_redis_url(first[0])

    if isinstance(first, dict):
        address = first.get("address")
        return _coerce_redis_url(address)

    return None


def _extract_redis_url_from_caches() -> Optional[str]:
    caches = getattr(settings, "CACHES", {}) or {}
    default_cache = caches.get("default", {}) or {}
    location = default_cache.get("LOCATION")
    return _coerce_redis_url(location)


def _get_redis_url() -> str:
    candidates = [
        _extract_redis_url_from_channel_layers(),
        _extract_redis_url_from_caches(),
        getattr(settings, "CELERY_BROKER_URL", None),
        os.environ.get("REDIS_URL"),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    raise CommandError(
        "Could not determine Redis URL from CHANNEL_LAYERS, CACHES, or REDIS_URL."
    )


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_json_pretty(raw: str) -> str:
    try:
        parsed = json.loads(raw)
        return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception:
        return raw


class Command(BaseCommand):
    help = (
        "Poll Redis pub/sub channels for tool/run/result activity and print findings "
        "to the terminal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll-freq",
            type=float,
            default=1.0,
            help="Poll frequency in times per second. Default: 1",
        )
        parser.add_argument(
            "--total_secs",
            type=float,
            default=10.0,
            help="Total number of seconds to poll. Default: 10",
        )
        parser.add_argument(
            "--channel",
            type=str,
            default="",
            help=(
                "Exact Redis pub/sub channel name to watch. "
                "If omitted, the command discovers likely tool/result/run channels."
            ),
        )
        parser.add_argument(
            "--pattern",
            action="append",
            dest="patterns",
            default=[],
            help=(
                "Additional PUBSUB CHANNELS pattern(s) to search when --channel is omitted. "
                "May be supplied multiple times."
            ),
        )
        parser.add_argument(
            "--show-keys",
            action="store_true",
            help=(
                "Also scan for likely Redis keys such as tool_result:* and "
                "run:*:pending_tool_results on each poll."
            ),
        )
        parser.add_argument(
            "--key-pattern",
            action="append",
            dest="key_patterns",
            default=[],
            help=(
                "Additional Redis key SCAN pattern(s) to inspect when --show-keys is enabled. "
                "May be supplied multiple times."
            ),
        )

    def handle(self, *args, **options):
        poll_freq = options["poll_freq"]
        total_secs = options["total_secs"]
        channel = (options["channel"] or "").strip()
        extra_patterns: List[str] = options["patterns"] or []
        show_keys = bool(options["show_keys"])
        extra_key_patterns: List[str] = options["key_patterns"] or []

        if poll_freq <= 0:
            raise CommandError("--poll-freq must be greater than 0.")
        if total_secs <= 0:
            raise CommandError("--total_secs must be greater than 0.")

        redis_url = _get_redis_url()
        self.stdout.write(self.style.SUCCESS(f"Using Redis URL: {redis_url}"))

        client = redis.Redis.from_url(redis_url)
        try:
            client.ping()
        except Exception as exc:
            raise CommandError(f"Could not connect to Redis: {exc}") from exc

        pubsub = client.pubsub(ignore_subscribe_messages=True)

        watched_channels: List[str] = []
        if channel:
            watched_channels = [channel]
        else:
            watched_channels = self._discover_channels(client, extra_patterns)

        if watched_channels:
            pubsub.subscribe(*watched_channels)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Subscribed to {len(watched_channels)} channel(s): {watched_channels}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No matching pub/sub channels found. "
                    "Polling will still run and can inspect keys if enabled."
                )
            )

        key_patterns = [
            "tool_result:*",
            "run:*:pending_tool_results",
            "run:*tool*",
            "tool:*",
            *extra_key_patterns,
        ]

        interval = 1.0 / poll_freq
        deadline = time.monotonic() + total_secs
        poll_num = 0

        while time.monotonic() < deadline:
            poll_num += 1
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"[poll {poll_num}] {now}"))

            if not channel:
                latest_channels = self._discover_channels(client, extra_patterns)
                if latest_channels != watched_channels:
                    new_channels = [c for c in latest_channels if c not in watched_channels]
                    if new_channels:
                        pubsub.subscribe(*new_channels)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"New channel(s) discovered and subscribed: {new_channels}"
                            )
                        )
                    watched_channels = latest_channels

            got_pubsub_message = False
            while True:
                message = pubsub.get_message(timeout=0.01)
                if not message:
                    break
                got_pubsub_message = True

                msg_type = _decode(message.get("type"))
                msg_channel = _decode(message.get("channel"))
                raw_data = message.get("data")
                raw_text = _decode(raw_data)

                self.stdout.write(
                    self.style.HTTP_INFO(
                        f"PUBSUB message type={msg_type} channel={msg_channel}"
                    )
                )
                self.stdout.write(_safe_json_pretty(raw_text))

            if not got_pubsub_message:
                self.stdout.write("No pub/sub messages this poll.")

            if watched_channels:
                self.stdout.write(f"Watching channels: {watched_channels}")
            else:
                self.stdout.write("Watching channels: []")

            if show_keys:
                self._report_keys(client, key_patterns)

            time.sleep(interval)

        try:
            pubsub.close()
        except Exception:
            pass

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Polling complete."))

    def _discover_channels(self, client: "redis.Redis", extra_patterns: Iterable[str]) -> List[str]:
        patterns = list(DEFAULT_PATTERNS) + list(extra_patterns)
        discovered = set()

        for pattern in patterns:
            try:
                channels = client.execute_command("PUBSUB CHANNELS", pattern)
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(f"PUBSUB CHANNELS failed for pattern={pattern}: {exc}")
                )
                continue

            for channel_name in channels or []:
                discovered.add(_decode(channel_name))

        return sorted(discovered)

    def _report_keys(self, client: "redis.Redis", patterns: Iterable[str]) -> None:
        found_any = False
        for pattern in patterns:
            keys = []
            cursor = 0
            while True:
                cursor, batch = client.scan(cursor=cursor, match=pattern, count=50)
                keys.extend(batch)
                if cursor == 0:
                    break

            if not keys:
                continue

            found_any = True
            decoded_keys = [_decode(k) for k in keys]
            self.stdout.write(self.style.SQL_COLTYPE(f"Keys matching {pattern}: {decoded_keys}"))

            for key in decoded_keys[:20]:
                key_type = _decode(client.type(key))
                self.stdout.write(f"  - {key} (type={key_type})")
                try:
                    if key_type == "string":
                        value = client.get(key)
                        if value is not None:
                            self.stdout.write("    " + _safe_json_pretty(_decode(value)))
                    elif key_type == "list":
                        values = client.lrange(key, 0, 20)
                        for item in values:
                            self.stdout.write("    " + _safe_json_pretty(_decode(item)))
                    elif key_type == "hash":
                        values = client.hgetall(key)
                        pretty = {_decode(k): _decode(v) for k, v in values.items()}
                        self.stdout.write("    " + json.dumps(pretty, indent=2, sort_keys=True))
                    elif key_type == "set":
                        values = [_decode(v) for v in client.smembers(key)]
                        self.stdout.write("    " + json.dumps(values, indent=2, sort_keys=True))
                    elif key_type == "zset":
                        values = client.zrange(key, 0, 20, withscores=True)
                        pretty = [(_decode(v), score) for v, score in values]
                        self.stdout.write("    " + json.dumps(pretty, indent=2))
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"    Could not inspect key {key}: {exc}"))

        if not found_any:
            self.stdout.write("No matching Redis keys found this poll.")
