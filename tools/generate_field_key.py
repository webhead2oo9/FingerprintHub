#!/usr/bin/env python3
"""Generate a field-encryption key entry for FingerprintHub."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from utils.field_encryption import ACTIVE_KEY_ENV, KEYS_ENV, generate_key_entry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a base64 AES-256-GCM key entry for FingerprintHub field encryption."
    )
    parser.add_argument("--key-id", default="v1", help="Key id to embed in encrypted field envelopes.")
    args = parser.parse_args()

    entry = generate_key_entry(args.key_id)
    print(f"{KEYS_ENV}={entry}")
    print(f"{ACTIVE_KEY_ENV}={args.key_id}")
    print()
    print("Back up this key outside the repo. Losing it permanently loses encrypted field access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
