"""Encrypt legacy community-linked metadata.

Revision ID: 0002_privacy_hardening
Revises: 0001_init
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from utils.field_encryption import (
    FieldDecryptionError,
    decrypt_text,
    encrypt_text,
    is_encrypted_value,
)

revision = "0002_privacy_hardening"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def _rewrite_column(table: str, column: str, *, encrypt: bool) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, {column} AS value FROM {table} WHERE {column} IS NOT NULL")
    ).mappings()
    for row in rows:
        value = row["value"]
        if encrypt:
            if is_encrypted_value(value):
                try:
                    decrypt_text(value)
                except FieldDecryptionError:
                    # A legacy plaintext value may coincidentally begin with the
                    # envelope prefix. Only authenticated decryption proves it
                    # is already encrypted.
                    pass
                else:
                    continue
            rewritten = encrypt_text(value)
        else:
            if not is_encrypted_value(value):
                continue
            try:
                rewritten = decrypt_text(value)
            except FieldDecryptionError:
                # Preserve malformed/plaintext prefix collisions rather than
                # aborting rollback or corrupting their value.
                continue
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
            {"value": rewritten, "id": row["id"]},
        )


def upgrade() -> None:
    op.execute(
        "LOCK TABLE fingerprints, fingerprint_hits, fingerprint_flags "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _rewrite_column("fingerprints", "source_guild_id", encrypt=True)
    _rewrite_column("fingerprints", "reason", encrypt=True)
    _rewrite_column("fingerprints", "source_url", encrypt=True)
    _rewrite_column("fingerprint_hits", "guild_id", encrypt=True)
    _rewrite_column("fingerprint_flags", "reason", encrypt=True)


def downgrade() -> None:
    op.execute(
        "LOCK TABLE fingerprints, fingerprint_hits, fingerprint_flags "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _rewrite_column("fingerprints", "source_guild_id", encrypt=False)
    _rewrite_column("fingerprints", "reason", encrypt=False)
    _rewrite_column("fingerprints", "source_url", encrypt=False)
    _rewrite_column("fingerprint_hits", "guild_id", encrypt=False)
    _rewrite_column("fingerprint_flags", "reason", encrypt=False)
