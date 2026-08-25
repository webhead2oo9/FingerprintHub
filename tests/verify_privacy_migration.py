#!/usr/bin/env python3
"""Exercise the populated 0001 <-> 0002 privacy migration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from utils.field_encryption import decrypt_text, is_encrypted_value  # noqa: E402
from utils.postgres_utils import normalize_psycopg_dsn  # noqa: E402


def migrate(revision: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    if revision.startswith("-"):
        command.downgrade(cfg, revision[1:])
    else:
        command.upgrade(cfg, revision)


def main() -> int:
    raw_dsn = os.environ["FINGERPRINTHUB_DATABASE_URL"]
    dsn = normalize_psycopg_dsn(raw_dsn)
    originals = {
        "source_guild_id": "enc:v1:legacy-prefix-collision",
        "reason": "legacy source reason",
        "source_url": "https://example.invalid/legacy-source",
        "hit_guild_id": "legacy hit community",
        "flag_reason": "legacy flag reason",
    }

    migrate("-0001_init")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        consumer_id = conn.execute(
            "INSERT INTO consumers (name, api_key_hash, scopes, created_at_ms) "
            "VALUES ('migration-fixture', repeat('a', 64), ARRAY['read','write'], 1) "
            "RETURNING id"
        ).fetchone()["id"]
        fingerprint_id = conn.execute(
            "INSERT INTO fingerprints "
            "(phash_hex, category, action, consumer_id, source_guild_id, reason, "
            "source_url, added_at_ms, updated_at_ms) "
            "VALUES ('0123456789abcdef', 'scam', 'kick', %s, %s, %s, %s, 1, 1) "
            "RETURNING id",
            (
                consumer_id,
                originals["source_guild_id"],
                originals["reason"],
                originals["source_url"],
            ),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO fingerprint_hits "
            "(fingerprint_id, consumer_id, guild_id, hit_at_ms) VALUES (%s, %s, %s, 1)",
            (fingerprint_id, consumer_id, originals["hit_guild_id"]),
        )
        conn.execute(
            "INSERT INTO fingerprint_flags "
            "(fingerprint_id, consumer_id, reason, created_at_ms) VALUES (%s, %s, %s, 1)",
            (fingerprint_id, consumer_id, originals["flag_reason"]),
        )

    migrate("head")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        fp = conn.execute(
            "SELECT source_guild_id, reason, source_url FROM fingerprints WHERE id = %s",
            (fingerprint_id,),
        ).fetchone()
        hit = conn.execute(
            "SELECT guild_id FROM fingerprint_hits WHERE fingerprint_id = %s",
            (fingerprint_id,),
        ).fetchone()
        flag = conn.execute(
            "SELECT reason FROM fingerprint_flags WHERE fingerprint_id = %s",
            (fingerprint_id,),
        ).fetchone()
        encrypted = {
            "source_guild_id": fp["source_guild_id"],
            "reason": fp["reason"],
            "source_url": fp["source_url"],
            "hit_guild_id": hit["guild_id"],
            "flag_reason": flag["reason"],
        }
    assert all(is_encrypted_value(value) for value in encrypted.values())
    assert {key: decrypt_text(value) for key, value in encrypted.items()} == originals

    migrate("-0001_init")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        fp = conn.execute(
            "SELECT source_guild_id, reason, source_url FROM fingerprints WHERE id = %s",
            (fingerprint_id,),
        ).fetchone()
        hit = conn.execute(
            "SELECT guild_id FROM fingerprint_hits WHERE fingerprint_id = %s",
            (fingerprint_id,),
        ).fetchone()
        flag = conn.execute(
            "SELECT reason FROM fingerprint_flags WHERE fingerprint_id = %s",
            (fingerprint_id,),
        ).fetchone()
        restored = {
            "source_guild_id": fp["source_guild_id"],
            "reason": fp["reason"],
            "source_url": fp["source_url"],
            "hit_guild_id": hit["guild_id"],
            "flag_reason": flag["reason"],
        }
        assert restored == originals
        conn.execute(
            "TRUNCATE fingerprint_flags, fingerprint_hits, fingerprints, "
            "consumers RESTART IDENTITY CASCADE"
        )

    migrate("head")
    print("privacy migration round-trip passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
