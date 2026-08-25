"""Fingerprint persistence layer for FingerprintHub.

Pure DB CRUD. No perceptual-hash matching happens server-side; clients match
locally in memory. Two invariants this module enforces:

* ``sync_seq`` (the incremental-sync cursor) is advanced via
  ``nextval('fingerprints_sync_seq')`` only on content changes: inserts,
  resurrections, status flips (hide/delete). Stats-only writes (hit_count,
  last_hit, sub-threshold flag counts) never touch it, so popular fingerprints
  don't churn every client's sync.
* Deletes are soft (``status='deleted'``) so the sync feed can emit tombstones
  and clients can remove the row locally. Physical purge is a later retention
  concern.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from psycopg_pool import ConnectionPool

from utils.field_encryption import encrypt_text

VALID_ACTIONS = frozenset({"kick", "timeout"})
VALID_CATEGORIES = frozenset({"scam", "nsfw", "crypto", "phishing", "other"})

# Minimal peer replication contract. Tenant attribution, source identifiers,
# timestamps, provenance, automation flags, free text, and activity statistics
# stay owner/admin-only.
_SYNC_COLUMNS = (
    "id, sync_seq, phash_hex, algorithm, algorithm_version, normalization_version, "
    "category, action, status"
)


def contribute(
    pool: ConnectionPool,
    *,
    consumer_id: int,
    phash_hex: str,
    algorithm: str,
    algorithm_version: str,
    normalization_version: str,
    category: str,
    action: str,
    source_guild_id: Optional[str],
    reason: Optional[str],
    source_url: Optional[str],
    auto_added: bool,
    provenance: str,
    now_ms: int,
) -> Tuple[Dict[str, Any], bool]:
    """Insert a fingerprint (or resurrect a previously-deleted one for this
    consumer). Returns (row, created). created=False means a live duplicate
    already exists (caller returns 409 with its id)."""
    enc_reason = encrypt_text(reason)
    enc_source_url = encrypt_text(source_url)
    enc_source_guild_id = encrypt_text(source_guild_id)
    with pool.connection() as conn:
        with conn.transaction():
            existing = conn.execute(
                "SELECT id, status FROM fingerprints "
                "WHERE phash_hex = %s AND consumer_id = %s",
                (phash_hex, consumer_id),
            ).fetchone()
            if existing is not None:
                if existing["status"] != "deleted":
                    return existing, False
                # Resurrect a tombstoned row: fresh metadata, new sync_seq,
                # clear stale flags so it isn't instantly re-hidden.
                conn.execute(
                    "DELETE FROM fingerprint_flags WHERE fingerprint_id = %s",
                    (existing["id"],),
                )
                row = conn.execute(
                    """
                    UPDATE fingerprints SET
                        sync_seq = nextval('fingerprints_sync_seq'),
                        algorithm = %s, algorithm_version = %s,
                        normalization_version = %s, category = %s, action = %s,
                        source_guild_id = %s, reason = %s, source_url = %s,
                        auto_added = %s, provenance = %s, status = 'active',
                        added_at_ms = %s, updated_at_ms = %s, flag_count = 0
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        algorithm, algorithm_version, normalization_version,
                        category, action, enc_source_guild_id, enc_reason,
                        enc_source_url, auto_added, provenance, now_ms, now_ms,
                        existing["id"],
                    ),
                ).fetchone()
                return row, True

            row = conn.execute(
                """
                INSERT INTO fingerprints (
                    phash_hex, algorithm, algorithm_version, normalization_version,
                    category, action, consumer_id, source_guild_id, reason,
                    source_url, added_at_ms, updated_at_ms, auto_added, provenance
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (phash_hex, consumer_id) DO NOTHING
                RETURNING *
                """,
                (
                    phash_hex, algorithm, algorithm_version, normalization_version,
                    category, action, consumer_id, enc_source_guild_id, enc_reason,
                    enc_source_url, now_ms, now_ms, auto_added, provenance,
                ),
            ).fetchone()
            if row is None:
                # A concurrent contribute for the same (phash_hex, consumer)
                # won the race between our SELECT above and this INSERT. Let the
                # unique constraint arbitrate and surface it as a duplicate
                # (caller returns 409) rather than a 500 on IntegrityError.
                existing = conn.execute(
                    "SELECT id, status FROM fingerprints "
                    "WHERE phash_hex = %s AND consumer_id = %s",
                    (phash_hex, consumer_id),
                ).fetchone()
                return existing, False
            return row, True


def get(pool: ConnectionPool, fingerprint_id: int) -> Optional[Dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            "SELECT * FROM fingerprints WHERE id = %s", (fingerprint_id,)
        ).fetchone()


def list_fingerprints(
    pool: ConnectionPool,
    *,
    category: Optional[str] = None,
    algorithm: Optional[str] = None,
    consumer_id: Optional[int] = None,
    include_hidden: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clauses = ["status <> 'deleted'"] if include_hidden else ["status = 'active'"]
    params: List[Any] = []
    if category:
        clauses.append("category = %s")
        params.append(category)
    if algorithm:
        clauses.append("algorithm = %s")
        params.append(algorithm)
    if consumer_id is not None:
        clauses.append("consumer_id = %s")
        params.append(consumer_id)
    where = " AND ".join(clauses)
    params.extend([limit, offset])
    with pool.connection() as conn:
        return conn.execute(
            f"SELECT * FROM fingerprints WHERE {where} "
            f"ORDER BY id DESC LIMIT %s OFFSET %s",
            params,
        ).fetchall()


def sync(
    pool: ConnectionPool,
    *,
    requester_consumer_id: int,
    since_seq: int,
    limit: int,
    algorithm: Optional[str] = None,
    algorithm_version: Optional[str] = None,
    normalization_version: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int, bool]:
    """Return (rows, next_since, has_more) for rows with sync_seq > since.

    Excludes the requester's own contributions (they already have them locally;
    re-ingesting would create duplicate local rows). Includes hidden/deleted
    rows as tombstones so clients can remove them. The optional
    compatibility-triple filter lets a client pull only the fingerprints it can
    compare against.
    """
    clauses = ["sync_seq > %s", "consumer_id <> %s"]
    params: List[Any] = [since_seq, requester_consumer_id]
    if algorithm:
        clauses.append("algorithm = %s")
        params.append(algorithm)
    if algorithm_version:
        clauses.append("algorithm_version = %s")
        params.append(algorithm_version)
    if normalization_version:
        clauses.append("normalization_version = %s")
        params.append(normalization_version)
    where = " AND ".join(clauses)
    params.append(limit + 1)  # fetch one extra to detect has_more
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {_SYNC_COLUMNS} FROM fingerprints WHERE {where} "
            f"ORDER BY sync_seq ASC LIMIT %s",
            params,
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_since = int(rows[-1]["sync_seq"]) if rows else since_seq
    return rows, next_since, has_more


def report_hit(
    pool: ConnectionPool,
    *,
    fingerprint_id: int,
    consumer_id: int,
    guild_id: Optional[str],
    distance: Optional[int],
    now_ms: int,
) -> Optional[int]:
    """Record a hit and bump hit_count/last_hit_at. Returns new hit_count, or
    None if the fingerprint is missing/deleted. Does not advance sync_seq."""
    with pool.connection() as conn:
        with conn.transaction():
            fp = conn.execute(
                "SELECT id, status FROM fingerprints WHERE id = %s FOR UPDATE",
                (fingerprint_id,),
            ).fetchone()
            if fp is None or fp["status"] == "deleted":
                return None
            conn.execute(
                """
                INSERT INTO fingerprint_hits
                    (fingerprint_id, consumer_id, guild_id, distance, hit_at_ms)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (fingerprint_id, consumer_id, encrypt_text(guild_id), distance, now_ms),
            )
            row = conn.execute(
                "UPDATE fingerprints SET hit_count = hit_count + 1, last_hit_at_ms = %s "
                "WHERE id = %s RETURNING hit_count",
                (now_ms, fingerprint_id),
            ).fetchone()
            return int(row["hit_count"])


def flag(
    pool: ConnectionPool,
    *,
    fingerprint_id: int,
    consumer_id: int,
    reason: Optional[str],
    now_ms: int,
    auto_hide_threshold: int,
) -> Optional[Dict[str, Any]]:
    """Record a flag (idempotent per consumer) and auto-hide once distinct
    flaggers reach the threshold. Returns {flag_count, status, hidden}, or None
    if the fingerprint is missing/deleted. Advances sync_seq only on the
    transition from active to hidden."""
    with pool.connection() as conn:
        with conn.transaction():
            # FOR UPDATE serializes concurrent flaggers of the same fingerprint.
            # Without it, two flags racing to the threshold can each COUNT before
            # the other commits, both see a sub-threshold count, and neither
            # hides. That miss never self-heals once every consumer has flagged.
            fp = conn.execute(
                "SELECT id, status FROM fingerprints WHERE id = %s FOR UPDATE",
                (fingerprint_id,),
            ).fetchone()
            if fp is None or fp["status"] == "deleted":
                return None
            conn.execute(
                """
                INSERT INTO fingerprint_flags
                    (fingerprint_id, consumer_id, reason, created_at_ms)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (fingerprint_id, consumer_id) DO NOTHING
                """,
                (fingerprint_id, consumer_id, encrypt_text(reason), now_ms),
            )
            flag_count = int(
                conn.execute(
                    "SELECT COUNT(DISTINCT consumer_id) AS c "
                    "FROM fingerprint_flags WHERE fingerprint_id = %s",
                    (fingerprint_id,),
                ).fetchone()["c"]
            )
            hidden_now = False
            status = fp["status"]
            if flag_count >= auto_hide_threshold and status == "active":
                conn.execute(
                    "UPDATE fingerprints SET status = 'hidden', flag_count = %s, "
                    "sync_seq = nextval('fingerprints_sync_seq'), updated_at_ms = %s "
                    "WHERE id = %s",
                    (flag_count, now_ms, fingerprint_id),
                )
                status = "hidden"
                hidden_now = True
            else:
                conn.execute(
                    "UPDATE fingerprints SET flag_count = %s, updated_at_ms = %s "
                    "WHERE id = %s",
                    (flag_count, now_ms, fingerprint_id),
                )
            return {"flag_count": flag_count, "status": status, "hidden": hidden_now}


def soft_delete(pool: ConnectionPool, *, fingerprint_id: int, now_ms: int) -> bool:
    """Tombstone a fingerprint (status='deleted', advance sync_seq). Returns
    True if a non-deleted row was changed."""
    with pool.connection() as conn:
        with conn.transaction():
            row = conn.execute(
                "UPDATE fingerprints SET status = 'deleted', "
                "sync_seq = nextval('fingerprints_sync_seq'), updated_at_ms = %s "
                "WHERE id = %s AND status <> 'deleted' RETURNING id",
                (now_ms, fingerprint_id),
            ).fetchone()
            return row is not None


def apply_retention(
    pool: ConnectionPool,
    *,
    now_ms: int,
    hit_retention_ms: int,
    flag_reason_retention_ms: int,
    tombstone_metadata_retention_ms: int,
    tombstone_retention_ms: int,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Apply the policy atomically, or preview it without committing."""
    if min(
        hit_retention_ms,
        flag_reason_retention_ms,
        tombstone_metadata_retention_ms,
        tombstone_retention_ms,
    ) < 0:
        raise ValueError("retention periods must be non-negative")
    if tombstone_metadata_retention_ms > tombstone_retention_ms:
        raise ValueError("tombstone metadata retention cannot exceed tombstone retention")

    with pool.connection() as conn:
        with conn.transaction(force_rollback=dry_run):
            hits = conn.execute(
                "DELETE FROM fingerprint_hits WHERE hit_at_ms < %s",
                (now_ms - hit_retention_ms,),
            ).rowcount
            flag_reasons = conn.execute(
                "UPDATE fingerprint_flags SET reason = NULL "
                "WHERE reason IS NOT NULL AND created_at_ms < %s",
                (now_ms - flag_reason_retention_ms,),
            ).rowcount
            deleted = conn.execute(
                "DELETE FROM fingerprints "
                "WHERE status = 'deleted' AND updated_at_ms < %s",
                (now_ms - tombstone_retention_ms,),
            ).rowcount
            anonymized = conn.execute(
                "UPDATE fingerprints "
                "SET source_guild_id = NULL, reason = NULL, source_url = NULL "
                "WHERE status = 'deleted' AND updated_at_ms < %s "
                "AND (source_guild_id IS NOT NULL OR reason IS NOT NULL OR source_url IS NOT NULL)",
                (now_ms - tombstone_metadata_retention_ms,),
            ).rowcount
    return {
        "hits_deleted": int(hits),
        "flag_reasons_cleared": int(flag_reasons),
        "tombstones_anonymized": int(anonymized),
        "tombstones_deleted": int(deleted),
    }


def stats(pool: ConnectionPool) -> Dict[str, Any]:
    with pool.connection() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'active') AS total_active,
                COUNT(*) FILTER (WHERE status = 'hidden') AS total_hidden,
                COUNT(*) FILTER (WHERE status = 'deleted') AS total_deleted,
                COALESCE(SUM(hit_count), 0) AS total_hits
            FROM fingerprints
            """
        ).fetchone()
        by_category = conn.execute(
            "SELECT category, COUNT(*) AS c FROM fingerprints "
            "WHERE status = 'active' GROUP BY category"
        ).fetchall()
        by_provenance = conn.execute(
            "SELECT provenance, COUNT(*) AS c FROM fingerprints "
            "WHERE status = 'active' GROUP BY provenance"
        ).fetchall()
        consumer_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM consumers WHERE enabled"
            ).fetchone()["c"]
        )
    return {
        "total_active": int(totals["total_active"]),
        "total_hidden": int(totals["total_hidden"]),
        "total_deleted": int(totals["total_deleted"]),
        "total_hits": int(totals["total_hits"]),
        "by_category": {r["category"]: int(r["c"]) for r in by_category},
        "by_provenance": {r["provenance"]: int(r["c"]) for r in by_provenance},
        "active_consumers": consumer_count,
    }
