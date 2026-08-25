"""Consumer (API client) persistence and API-key primitives.

A consumer is a client that contributes and syncs fingerprints. Each holds
exactly one API key; only its SHA-256 hash is stored. Lookup is by exact hash
match on a UNIQUE index. The hash itself is the lookup key, so there is no
per-request scan and no need for a constant-time-compare loop; the stored
value is a preimage-resistant hash, not the secret.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any, Dict, List, Optional, Tuple

from psycopg_pool import ConnectionPool

API_KEY_PREFIX = "fph_"
VALID_SCOPES = frozenset({"read", "write", "admin"})


def hash_api_key(raw_key: str) -> str:
    """Return the hex SHA-256 of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Generate a new high-entropy, prefixed API key (shown to the operator once)."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def find_by_key_hash(pool: ConnectionPool, key_hash: str) -> Optional[Dict[str, Any]]:
    """Return the consumer row for an API-key hash, or None."""
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT id, name, scopes, enabled
            FROM consumers
            WHERE api_key_hash = %s
            """,
            (key_hash,),
        ).fetchone()


def touch_last_seen(pool: ConnectionPool, consumer_id: int, now_ms: int) -> None:
    """Best-effort update of a consumer's last_seen_at_ms."""
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE consumers SET last_seen_at_ms = %s WHERE id = %s",
                (now_ms, consumer_id),
            )


def create_consumer(
    pool: ConnectionPool,
    *,
    name: str,
    scopes: List[str],
    now_ms: int,
) -> Tuple[int, str]:
    """Insert a consumer and return (id, raw_api_key). Raw key is returned once."""
    invalid = sorted(set(scopes) - VALID_SCOPES)
    if invalid:
        raise ValueError(f"invalid scope(s): {', '.join(invalid)}")
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    with pool.connection() as conn:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO consumers (name, api_key_hash, scopes, created_at_ms)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (name, key_hash, list(scopes), now_ms),
            ).fetchone()
    return int(row["id"]), raw_key
