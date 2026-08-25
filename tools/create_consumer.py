#!/usr/bin/env python3
"""Create a FingerprintHub consumer and print its API key once.

The raw key is shown once and never stored; only its SHA-256 hash is
persisted. Run on the host with FINGERPRINTHUB_DATABASE_URL set, e.g.:

    venv/bin/python tools/create_consumer.py --name community-client --scopes read,write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from api import consumers_store  # noqa: E402
from config import load_config_from_env  # noqa: E402
from utils.postgres_utils import get_postgres_pool  # noqa: E402
from utils.time_utils import utc_now_ms  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a FingerprintHub consumer.")
    parser.add_argument(
        "--name", required=True, help="Unique consumer name, e.g. 'community-client'."
    )
    parser.add_argument(
        "--scopes",
        default="read,write",
        help="Comma-separated scopes from {read,write,admin}. Default: read,write",
    )
    args = parser.parse_args()

    load_dotenv(".env")
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]

    pool = get_postgres_pool(load_config_from_env().postgres_config())
    try:
        consumer_id, raw_key = consumers_store.create_consumer(
            pool, name=args.name, scopes=scopes, now_ms=utc_now_ms()
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except psycopg.errors.UniqueViolation:
        print(f"error: a consumer named {args.name!r} already exists", file=sys.stderr)
        return 2

    print(f"Created consumer id={consumer_id} name={args.name!r} scopes={scopes}")
    print()
    print("API key (shown ONCE — store it now):")
    print(f"  {raw_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
