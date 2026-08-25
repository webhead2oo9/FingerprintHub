"""Per-consumer API-key authentication, scope checks, and rate limiting.

FingerprintHub is multi-tenant: each request carries a consumer's API key in
``X-API-Key``; we hash it and look the consumer up by exact hash match. The
resolved consumer (id, name, scopes) is attached to ``request['consumer']`` for
handlers; per-route scope enforcement is done in the handlers via
:func:`require_scope`.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict

from aiohttp import web

from api import consumers_store
from api.keys import CONSUMER_KEY
from config import ServiceConfig
from utils.async_utils import run_blocking_io
from utils.time_utils import utc_now_ms

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset({"/v1/health"})
_LAST_SEEN_THROTTLE_SECONDS = 60.0


def get_consumer(request: web.Request) -> Dict[str, Any]:
    return request[CONSUMER_KEY]


def require_scope(request: web.Request, scope: str) -> None:
    """Raise 403 unless the authenticated consumer holds ``scope``."""
    scopes = set(request[CONSUMER_KEY].get("scopes") or ())
    if scope not in scopes:
        raise web.HTTPForbidden(
            text=f'{{"error": "missing required scope: {scope}"}}',
            content_type="application/json",
        )


def make_auth_middleware(pool, config: ServiceConfig):
    rate_limit = config.rate_limit_per_minute
    request_times: Dict[int, Deque[float]] = defaultdict(deque)
    last_seen_marked: Dict[int, float] = {}

    def _rate_limited(consumer_id: int, now: float) -> bool:
        window = request_times[consumer_id]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= rate_limit:
            return True
        window.append(now)
        return False

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if request.path in _PUBLIC_PATHS:
            return await handler(request)

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            return web.json_response({"error": "missing X-API-Key"}, status=401)

        key_hash = consumers_store.hash_api_key(api_key)
        consumer = await run_blocking_io(
            lambda: consumers_store.find_by_key_hash(pool, key_hash)
        )
        if consumer is None or not consumer["enabled"]:
            return web.json_response(
                {"error": "invalid or disabled API key"}, status=401
            )

        now = time.monotonic()
        if _rate_limited(int(consumer["id"]), now):
            return web.json_response({"error": "rate limit exceeded"}, status=429)

        request[CONSUMER_KEY] = consumer

        # Throttled best-effort last-seen update (avoids a write per request).
        consumer_id = int(consumer["id"])
        if now - last_seen_marked.get(consumer_id, 0.0) >= _LAST_SEEN_THROTTLE_SECONDS:
            last_seen_marked[consumer_id] = now
            try:
                await run_blocking_io(
                    lambda: consumers_store.touch_last_seen(
                        pool, consumer_id, utc_now_ms()
                    )
                )
            except Exception as exc:  # noqa: BLE001 - last-seen is non-critical
                logger.debug("touch_last_seen failed for consumer %s: %s", consumer_id, exc)

        return await handler(request)

    return auth_middleware
