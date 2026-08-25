"""Flat environment-variable configuration for FingerprintHub.

The service has a small, flat config surface (host/port + a few knobs), so it
reads straight from the environment. The Postgres DSN itself comes from
``FINGERPRINTHUB_DATABASE_URL`` (read inside ``utils.postgres_utils``);
``postgres_config()`` returns the dict shape that helper expects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 58751
DEFAULT_RATE_LIMIT_PER_MINUTE = 300
DEFAULT_AUTO_HIDE_FLAG_THRESHOLD = 2
DEFAULT_MAX_SYNC_LIMIT = 500
DEFAULT_SYNC_LIMIT = 200
DEFAULT_MAX_LIST_LIMIT = 200


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ServiceConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    auto_hide_flag_threshold: int = DEFAULT_AUTO_HIDE_FLAG_THRESHOLD
    max_sync_limit: int = DEFAULT_MAX_SYNC_LIMIT
    default_sync_limit: int = DEFAULT_SYNC_LIMIT
    max_list_limit: int = DEFAULT_MAX_LIST_LIMIT

    def postgres_config(self) -> Dict[str, Any]:
        """Return the dict shape ``utils.postgres_utils`` consumes.

        An empty ``database`` mapping means the DSN comes from
        ``FINGERPRINTHUB_DATABASE_URL`` and the pool sizes fall back to the
        defaults in ``utils.postgres_utils``.
        """
        return {"database": {}}


def load_config_from_env() -> ServiceConfig:
    return ServiceConfig(
        host=os.getenv("FINGERPRINTHUB_HOST", DEFAULT_HOST),
        port=_int_env("FINGERPRINTHUB_PORT", DEFAULT_PORT),
        rate_limit_per_minute=_int_env(
            "FINGERPRINTHUB_RATE_LIMIT", DEFAULT_RATE_LIMIT_PER_MINUTE
        ),
        auto_hide_flag_threshold=max(
            1,
            _int_env(
                "FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD",
                DEFAULT_AUTO_HIDE_FLAG_THRESHOLD,
            ),
        ),
        max_sync_limit=_int_env("FINGERPRINTHUB_MAX_SYNC_LIMIT", DEFAULT_MAX_SYNC_LIMIT),
        default_sync_limit=_int_env("FINGERPRINTHUB_DEFAULT_SYNC_LIMIT", DEFAULT_SYNC_LIMIT),
        max_list_limit=_int_env("FINGERPRINTHUB_MAX_LIST_LIMIT", DEFAULT_MAX_LIST_LIMIT),
    )
