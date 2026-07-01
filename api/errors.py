"""Standardized error handling for FingerprintHub HTTP handlers.

Ported from Nexarion's ``utils/error_handling.py`` (generic, no Nexarion
specifics).
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, Dict, Optional, Tuple

from aiohttp import web
from aiohttp.client_exceptions import ContentTypeError
import psycopg

logger = logging.getLogger(__name__)


def handle_errors(func):
    """Decorator: log and re-raise unexpected handler errors (the error
    middleware turns them into a 500)."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except web.HTTPException:
            # Intentional HTTP responses raised as exceptions pass through.
            raise
        except Exception as exc:  # noqa: BLE001 - logged then re-raised
            logger.exception("Error in %s: %s", func.__name__, exc)
            raise

    return wrapper


def db_error_response(error: Exception, operation: str = "database operation") -> web.Response:
    """Return a standardized HTTP error response for database failures."""
    if isinstance(error, psycopg.OperationalError):
        logger.error("Database unavailable during %s: %s", operation, error)
        return web.json_response({"error": "Database temporarily unavailable"}, status=503)
    logger.error("Database error during %s: %s", operation, error)
    return web.json_response({"error": f"Database error during {operation}"}, status=500)


async def parse_json_body(
    request: web.Request,
) -> Tuple[Optional[Dict[str, Any]], Optional[web.Response]]:
    """Parse a JSON request body, returning (data, None) or (None, error_response)."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, ContentTypeError) as exc:
        logger.warning("Invalid JSON in request: %s", exc)
        return None, web.json_response(
            {"error": "Invalid JSON", "details": str(exc)}, status=400
        )
    if not isinstance(data, dict):
        return None, web.json_response(
            {"error": "JSON body must be an object"}, status=400
        )
    return data, None
