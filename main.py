#!/usr/bin/env python3
"""FingerprintHub entrypoint: standalone shared image-fingerprint service.

Runs as its own process against a dedicated Postgres database. Requires the
schema to already be at Alembic head (run ``alembic upgrade head`` out of band).
"""

from __future__ import annotations

import logging
import os

from aiohttp import web
from dotenv import load_dotenv

from api.app import create_app
from api.keys import CONFIG_KEY
from config import load_config_from_env
from utils.postgres_utils import (
    close_postgres_pool,
    get_postgres_pool,
    validate_postgres_runtime,
)

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    level = os.getenv("FINGERPRINTHUB_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s",
    )


def create_app_from_environment() -> web.Application:
    load_dotenv(".env")
    setup_logging()
    config = load_config_from_env()
    pg_config = config.postgres_config()
    info = validate_postgres_runtime(pg_config)
    logger.info(
        "Postgres validated (server=%s, heads=%s)",
        info.get("server_version"),
        info.get("alembic_heads"),
    )
    pool = get_postgres_pool(pg_config)
    return create_app(pool=pool, config=config)


def main() -> None:
    app = create_app_from_environment()
    config = app[CONFIG_KEY]
    logger.info("Starting FingerprintHub on %s:%s", config.host, config.port)
    try:
        # Avoid collecting client IPs, request lines, referrers, and user agents
        # in application logs. Operators separately govern reverse-proxy logs.
        web.run_app(app, host=config.host, port=config.port, access_log=None)
    finally:
        close_postgres_pool()


if __name__ == "__main__":
    main()
