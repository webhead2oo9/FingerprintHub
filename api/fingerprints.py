"""HTTP handlers for the FingerprintHub API."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from aiohttp import web

from api import fingerprint_store
from api.auth import get_consumer, require_scope
from api.errors import handle_errors, parse_json_body
from api.keys import CONFIG_KEY, POOL_KEY
from utils.async_utils import run_blocking_io
from utils.field_encryption import FieldDecryptionError, decrypt_text_field
from utils.time_utils import utc_now_ms

logger = logging.getLogger(__name__)

_PHASH_RE = re.compile(r"^[0-9a-f]{16}$")
_DEFAULT_ALGORITHM = "phash"
_DEFAULT_ALGORITHM_VERSION = "imagehash.phash"
_DEFAULT_NORMALIZATION_VERSION = "alpha_white_v1"


def _pool(request: web.Request):
    return request.app[POOL_KEY]


def _config(request: web.Request):
    return request.app[CONFIG_KEY]


def _path_id(request: web.Request) -> Optional[int]:
    try:
        return int(request.match_info["id"])
    except (KeyError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: Any, *, default: bool = False) -> Optional[bool]:
    """Return a real JSON boolean, ``default`` if absent, or None if invalid.

    Deliberately strict: ``bool(value)`` would read the string "false" as True
    and quietly store the opposite of what the client sent.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return None


def _can_see_secrets(request: web.Request, row: Dict[str, Any]) -> bool:
    consumer = get_consumer(request)
    if "admin" in set(consumer.get("scopes") or ()):
        return True
    return int(row["consumer_id"]) == int(consumer["id"])


def _decrypt(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return decrypt_text_field(value)
    except FieldDecryptionError:
        logger.warning("failed to decrypt a fingerprint field")
        return None


def _peer_fp(row: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal record shared with a non-owning consumer."""
    return {
        "id": int(row["id"]),
        "sync_seq": int(row["sync_seq"]),
        "phash_hex": row["phash_hex"],
        "algorithm": row["algorithm"],
        "algorithm_version": row["algorithm_version"],
        "normalization_version": row["normalization_version"],
        "category": row["category"],
        "action": row["action"],
        "status": row["status"],
    }


def _detail_fp(request: web.Request, row: Dict[str, Any]) -> Dict[str, Any]:
    """Browse/detail shape: full record, secrets gated by ownership/admin."""
    if not _can_see_secrets(request, row):
        return _peer_fp(row)
    data = {
        "id": int(row["id"]),
        "sync_seq": int(row["sync_seq"]),
        "phash_hex": row["phash_hex"],
        "algorithm": row["algorithm"],
        "algorithm_version": row["algorithm_version"],
        "normalization_version": row["normalization_version"],
        "category": row["category"],
        "action": row["action"],
        "consumer_id": int(row["consumer_id"]),
        "source_guild_id": _decrypt(row.get("source_guild_id")),
        "added_at_ms": int(row["added_at_ms"]),
        "updated_at_ms": int(row["updated_at_ms"]),
        "hit_count": int(row["hit_count"]),
        "last_hit_at_ms": row["last_hit_at_ms"],
        "auto_added": bool(row["auto_added"]),
        "provenance": row["provenance"],
        "status": row["status"],
        "flag_count": int(row["flag_count"]),
    }
    data["reason"] = _decrypt(row.get("reason"))
    data["source_url"] = _decrypt(row.get("source_url"))
    return data


@handle_errors
async def health(request: web.Request) -> web.Response:
    try:
        await run_blocking_io(lambda: _ping(_pool(request)))
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        logger.warning("health check DB ping failed: %s", exc)
        return web.json_response({"status": "degraded", "db": False}, status=503)
    return web.json_response({"status": "ok", "db": True})


def _ping(pool) -> None:
    with pool.connection() as conn:
        conn.execute("SELECT 1")


@handle_errors
async def sync_fingerprints(request: web.Request) -> web.Response:
    require_scope(request, "read")
    config = _config(request)
    try:
        since = int(request.query.get("since", "0"))
    except ValueError:
        return web.json_response({"error": "since must be an integer"}, status=400)
    try:
        limit = int(request.query.get("limit", str(config.default_sync_limit)))
    except ValueError:
        return web.json_response({"error": "limit must be an integer"}, status=400)
    limit = max(1, min(limit, config.max_sync_limit))

    consumer = get_consumer(request)
    rows, next_since, has_more = await run_blocking_io(
        lambda: fingerprint_store.sync(
            _pool(request),
            requester_consumer_id=int(consumer["id"]),
            since_seq=max(0, since),
            limit=limit,
            algorithm=_str_or_none(request.query.get("algorithm")),
            algorithm_version=_str_or_none(request.query.get("algorithm_version")),
            normalization_version=_str_or_none(
                request.query.get("normalization_version")
            ),
        )
    )
    return web.json_response(
        {
            "fingerprints": [_peer_fp(r) for r in rows],
            "next_since": next_since,
            "has_more": has_more,
        }
    )


@handle_errors
async def contribute_fingerprint(request: web.Request) -> web.Response:
    require_scope(request, "write")
    body, error = await parse_json_body(request)
    if error is not None:
        return error

    phash_hex = str(body.get("phash_hex", "")).strip().lower()
    if not _PHASH_RE.match(phash_hex):
        return web.json_response(
            {"error": "phash_hex must be 16 lowercase hex characters"}, status=400
        )
    category = str(body.get("category", "")).strip()
    if category not in fingerprint_store.VALID_CATEGORIES:
        return web.json_response(
            {"error": f"category must be one of {sorted(fingerprint_store.VALID_CATEGORIES)}"},
            status=400,
        )
    action = str(body.get("action", "")).strip()
    if action not in fingerprint_store.VALID_ACTIONS:
        return web.json_response(
            {"error": f"action must be one of {sorted(fingerprint_store.VALID_ACTIONS)}"},
            status=400,
        )
    auto_added = _bool_or_none(body.get("auto_added"))
    if auto_added is None:
        return web.json_response(
            {"error": "auto_added must be a boolean"}, status=400
        )

    consumer = get_consumer(request)
    row, created = await run_blocking_io(
        lambda: fingerprint_store.contribute(
            _pool(request),
            consumer_id=int(consumer["id"]),
            phash_hex=phash_hex,
            algorithm=_str_or_none(body.get("algorithm")) or _DEFAULT_ALGORITHM,
            algorithm_version=_str_or_none(body.get("algorithm_version"))
            or _DEFAULT_ALGORITHM_VERSION,
            normalization_version=_str_or_none(body.get("normalization_version"))
            or _DEFAULT_NORMALIZATION_VERSION,
            category=category,
            action=action,
            source_guild_id=_str_or_none(body.get("source_guild_id")),
            reason=_str_or_none(body.get("reason")),
            source_url=_str_or_none(body.get("source_url")),
            auto_added=auto_added,
            provenance=_str_or_none(body.get("provenance")) or "manual_staff",
            now_ms=utc_now_ms(),
        )
    )
    if not created:
        return web.json_response(
            {"error": "duplicate fingerprint for this consumer", "existing_id": int(row["id"])},
            status=409,
        )
    return web.json_response(_detail_fp(request, row), status=201)


@handle_errors
async def report_hit(request: web.Request) -> web.Response:
    require_scope(request, "write")
    fp_id = _path_id(request)
    if fp_id is None:
        return web.json_response({"error": "invalid fingerprint id"}, status=400)
    body, error = await parse_json_body(request)
    if error is not None:
        # hit body is optional; tolerate empty/no body
        body = {}
    distance_raw = body.get("distance")
    try:
        distance = int(distance_raw) if distance_raw is not None else None
    except (TypeError, ValueError):
        distance = None

    consumer = get_consumer(request)
    new_count = await run_blocking_io(
        lambda: fingerprint_store.report_hit(
            _pool(request),
            fingerprint_id=fp_id,
            consumer_id=int(consumer["id"]),
            guild_id=_str_or_none(body.get("guild_id")),
            distance=distance,
            now_ms=utc_now_ms(),
        )
    )
    if new_count is None:
        return web.json_response({"error": "fingerprint not found"}, status=404)
    return web.json_response({"id": fp_id, "hit_count": new_count})


@handle_errors
async def flag_fingerprint(request: web.Request) -> web.Response:
    # Flagging requires 'write' (a destructive-capable action via auto-hide);
    # trust-model tuning is deferred until a second consumer exists.
    require_scope(request, "write")
    fp_id = _path_id(request)
    if fp_id is None:
        return web.json_response({"error": "invalid fingerprint id"}, status=400)
    body, error = await parse_json_body(request)
    if error is not None:
        body = {}

    consumer = get_consumer(request)
    config = _config(request)
    result = await run_blocking_io(
        lambda: fingerprint_store.flag(
            _pool(request),
            fingerprint_id=fp_id,
            consumer_id=int(consumer["id"]),
            reason=_str_or_none(body.get("reason")),
            now_ms=utc_now_ms(),
            auto_hide_threshold=config.auto_hide_flag_threshold,
        )
    )
    if result is None:
        return web.json_response({"error": "fingerprint not found"}, status=404)
    return web.json_response({"id": fp_id, **result})


@handle_errors
async def delete_fingerprint(request: web.Request) -> web.Response:
    require_scope(request, "write")
    fp_id = _path_id(request)
    if fp_id is None:
        return web.json_response({"error": "invalid fingerprint id"}, status=400)

    row = await run_blocking_io(lambda: fingerprint_store.get(_pool(request), fp_id))
    if row is None or row["status"] == "deleted":
        return web.json_response({"error": "fingerprint not found"}, status=404)

    consumer = get_consumer(request)
    is_owner = int(row["consumer_id"]) == int(consumer["id"])
    is_admin = "admin" in set(consumer.get("scopes") or ())
    if not (is_owner or is_admin):
        return web.json_response(
            {"error": "only the owning consumer or an admin may delete; use /flag instead"},
            status=403,
        )

    await run_blocking_io(
        lambda: fingerprint_store.soft_delete(
            _pool(request), fingerprint_id=fp_id, now_ms=utc_now_ms()
        )
    )
    return web.Response(status=204)


@handle_errors
async def get_fingerprint(request: web.Request) -> web.Response:
    require_scope(request, "read")
    fp_id = _path_id(request)
    if fp_id is None:
        return web.json_response({"error": "invalid fingerprint id"}, status=400)

    row = await run_blocking_io(lambda: fingerprint_store.get(_pool(request), fp_id))
    if row is None or row["status"] == "deleted":
        return web.json_response({"error": "fingerprint not found"}, status=404)
    if row["status"] == "hidden" and not _can_see_secrets(request, row):
        return web.json_response({"error": "fingerprint not found"}, status=404)
    return web.json_response(_detail_fp(request, row))


@handle_errors
async def list_fingerprints(request: web.Request) -> web.Response:
    require_scope(request, "read")
    config = _config(request)
    consumer = get_consumer(request)
    is_admin = "admin" in set(consumer.get("scopes") or ())
    try:
        limit = int(request.query.get("limit", "50"))
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return web.json_response({"error": "limit/offset must be integers"}, status=400)
    limit = max(1, min(limit, config.max_list_limit))
    offset = max(0, offset)

    consumer_filter = request.query.get("consumer_id")
    consumer_id_val: Optional[int]
    if consumer_filter is not None:
        try:
            consumer_id_val = int(consumer_filter)
        except ValueError:
            return web.json_response({"error": "consumer_id must be an integer"}, status=400)
    else:
        consumer_id_val = None

    requester_id = int(consumer["id"])
    if not is_admin:
        if consumer_id_val is not None and consumer_id_val != requester_id:
            return web.json_response(
                {"error": "consumer_id filtering is limited to your own records"},
                status=403,
            )
        consumer_id_val = requester_id

    include_hidden = request.query.get("include_hidden", "").lower() in ("1", "true", "yes")
    rows = await run_blocking_io(
        lambda: fingerprint_store.list_fingerprints(
            _pool(request),
            category=_str_or_none(request.query.get("category")),
            algorithm=_str_or_none(request.query.get("algorithm")),
            consumer_id=consumer_id_val,
            include_hidden=include_hidden and is_admin,
            limit=limit,
            offset=offset,
        )
    )
    return web.json_response(
        {"fingerprints": [_detail_fp(request, r) for r in rows], "count": len(rows)}
    )


@handle_errors
async def stats(request: web.Request) -> web.Response:
    require_scope(request, "admin")
    result = await run_blocking_io(lambda: fingerprint_store.stats(_pool(request)))
    return web.json_response(result)
