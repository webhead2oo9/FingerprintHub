#!/usr/bin/env python3
"""Preview or apply FingerprintHub's retention policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from api.fingerprint_store import apply_retention  # noqa: E402
from config import load_config_from_env  # noqa: E402
from utils.postgres_utils import close_postgres_pool, get_postgres_pool  # noqa: E402
from utils.time_utils import utc_now_ms  # noqa: E402

DAY_MS = 24 * 60 * 60 * 1000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview retention candidates; pass --apply to commit the changes."
    )
    parser.add_argument("--hit-days", type=int, default=90)
    parser.add_argument("--flag-reason-days", type=int, default=90)
    parser.add_argument("--tombstone-metadata-days", type=int, default=30)
    parser.add_argument("--tombstone-days", type=int, default=180)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the retention changes. Without this flag the transaction is rolled back.",
    )
    args = parser.parse_args()
    day_values = (
        args.hit_days,
        args.flag_reason_days,
        args.tombstone_metadata_days,
        args.tombstone_days,
    )
    if min(day_values) < 0:
        parser.error("retention days must be non-negative")
    if args.tombstone_metadata_days > args.tombstone_days:
        parser.error("tombstone metadata days cannot exceed tombstone days")

    load_dotenv(".env")
    pool = get_postgres_pool(load_config_from_env().postgres_config())
    try:
        result = apply_retention(
            pool,
            now_ms=utc_now_ms(),
            hit_retention_ms=args.hit_days * DAY_MS,
            flag_reason_retention_ms=args.flag_reason_days * DAY_MS,
            tombstone_metadata_retention_ms=args.tombstone_metadata_days * DAY_MS,
            tombstone_retention_ms=args.tombstone_days * DAY_MS,
            dry_run=not args.apply,
        )
    finally:
        close_postgres_pool()

    print(json.dumps({"mode": "apply" if args.apply else "preview", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
