"""Minimal time helpers for FingerprintHub."""

from __future__ import annotations

import time


def utc_now_ms() -> int:
    """Return the current UTC time as integer milliseconds since the epoch."""
    return int(time.time() * 1000)
