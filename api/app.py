"""aiohttp application factory for FingerprintHub."""

from __future__ import annotations

import logging

import psycopg
from aiohttp import web

from api import fingerprints
from api.auth import make_auth_middleware
from api.keys import CONFIG_KEY, POOL_KEY
from config import ServiceConfig

logger = logging.getLogger(__name__)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except psycopg.OperationalError:
        # DB unavailable/connection lost (incl. psycopg_pool.PoolTimeout, a
        # subclass) => 503, matching /v1/health's degraded signal, not a 500.
        logger.exception("database unavailable handling %s %s", request.method, request.path)
        return web.json_response(
            {"error": "database temporarily unavailable"}, status=503
        )
    except Exception:  # noqa: BLE001 - last-resort 500
        logger.exception("unhandled error handling %s %s", request.method, request.path)
        return web.json_response({"error": "internal server error"}, status=500)


def create_app(*, pool, config: ServiceConfig) -> web.Application:
    # error_middleware first => outermost (catches everything below it).
    app = web.Application(
        middlewares=[error_middleware, make_auth_middleware(pool, config)]
    )
    app[POOL_KEY] = pool
    app[CONFIG_KEY] = config

    app.router.add_get("/v1/health", fingerprints.health)
    # Static subpaths registered before the dynamic /{id} route.
    app.router.add_get("/v1/fingerprints/sync", fingerprints.sync_fingerprints)
    app.router.add_get("/v1/fingerprints/stats", fingerprints.stats)
    app.router.add_post("/v1/fingerprints", fingerprints.contribute_fingerprint)
    app.router.add_get("/v1/fingerprints", fingerprints.list_fingerprints)
    app.router.add_post("/v1/fingerprints/{id}/hit", fingerprints.report_hit)
    app.router.add_post("/v1/fingerprints/{id}/flag", fingerprints.flag_fingerprint)
    app.router.add_get("/v1/fingerprints/{id}", fingerprints.get_fingerprint)
    app.router.add_delete("/v1/fingerprints/{id}", fingerprints.delete_fingerprint)
    return app
