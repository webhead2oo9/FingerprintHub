"""Retention-policy behavior against disposable Postgres."""

from __future__ import annotations

from api import fingerprint_store
from utils.time_utils import utc_now_ms


def test_apply_retention_minimizes_old_audit_and_tombstone_data(pool, make_consumer):
    now_ms = utc_now_ms()
    consumer_id, _key = make_consumer("owner", ["read", "write"])

    def add(phash: str):
        row, created = fingerprint_store.contribute(
            pool,
            consumer_id=consumer_id,
            phash_hex=phash,
            algorithm="phash",
            algorithm_version="imagehash.phash",
            normalization_version="alpha_white_v1",
            category="scam",
            action="kick",
            source_guild_id="source-community",
            reason="private source note",
            source_url="https://example.invalid/private",
            auto_added=False,
            provenance="manual_staff",
            now_ms=now_ms,
        )
        assert created
        return int(row["id"])

    active_id = add("0123456789abcdef")
    recent_tombstone_id = add("fedcba9876543210")
    old_tombstone_id = add("aaaabbbbccccdddd")

    day_ms = 24 * 60 * 60 * 1000
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO fingerprint_hits "
                "(fingerprint_id, consumer_id, guild_id, distance, hit_at_ms) "
                "VALUES (%s, %s, %s, 1, %s), (%s, %s, %s, 2, %s)",
                (
                    active_id,
                    consumer_id,
                    "old-hit-community",
                    now_ms - 120 * day_ms,
                    active_id,
                    consumer_id,
                    "recent-hit-community",
                    now_ms - day_ms,
                ),
            )
            conn.execute(
                "INSERT INTO fingerprint_flags "
                "(fingerprint_id, consumer_id, reason, created_at_ms) "
                "VALUES (%s, %s, %s, %s)",
                (active_id, consumer_id, "old private note", now_ms - 120 * day_ms),
            )
            conn.execute(
                "UPDATE fingerprints SET status = 'deleted', updated_at_ms = %s WHERE id = %s",
                (now_ms - 45 * day_ms, recent_tombstone_id),
            )
            conn.execute(
                "UPDATE fingerprints SET status = 'deleted', updated_at_ms = %s WHERE id = %s",
                (now_ms - 200 * day_ms, old_tombstone_id),
            )

    policy = {
        "now_ms": now_ms,
        "hit_retention_ms": 90 * day_ms,
        "flag_reason_retention_ms": 90 * day_ms,
        "tombstone_metadata_retention_ms": 30 * day_ms,
        "tombstone_retention_ms": 180 * day_ms,
    }
    preview = fingerprint_store.apply_retention(pool, dry_run=True, **policy)
    assert preview == {
        "hits_deleted": 1,
        "flag_reasons_cleared": 1,
        "tombstones_anonymized": 1,
        "tombstones_deleted": 1,
    }
    with pool.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM fingerprint_hits").fetchone()["c"] == 2

    result = fingerprint_store.apply_retention(
        pool,
        **policy,
    )
    assert result == {
        "hits_deleted": 1,
        "flag_reasons_cleared": 1,
        "tombstones_anonymized": 1,
        "tombstones_deleted": 1,
    }

    with pool.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM fingerprint_hits").fetchone()["c"] == 1
        assert conn.execute(
            "SELECT reason FROM fingerprint_flags WHERE fingerprint_id = %s", (active_id,)
        ).fetchone()["reason"] is None
        recent = conn.execute(
            "SELECT source_guild_id, reason, source_url FROM fingerprints WHERE id = %s",
            (recent_tombstone_id,),
        ).fetchone()
        assert dict(recent) == {"source_guild_id": None, "reason": None, "source_url": None}
        assert conn.execute(
            "SELECT 1 FROM fingerprints WHERE id = %s", (old_tombstone_id,)
        ).fetchone() is None