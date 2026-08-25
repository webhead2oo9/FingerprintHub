"""Postgres connection and schema-validation helpers for FingerprintHub."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from utils.config_access import mapping_at

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL_ENV = "FINGERPRINTHUB_DATABASE_URL"
DEFAULT_POOL_MIN_SIZE = 1
DEFAULT_POOL_MAX_SIZE = 10
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5

@dataclass(frozen=True)
class PostgresSettings:
    dsn: str
    pool_min_size: int = DEFAULT_POOL_MIN_SIZE
    pool_max_size: int = DEFAULT_POOL_MAX_SIZE
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS


class PostgresPoolManager:
    """Owns the process Postgres pool for the active runtime config."""

    def __init__(self) -> None:
        self.pool: Optional[ConnectionPool] = None
        self.pool_dsn: Optional[str] = None

    def get_pool(self, config: Dict[str, Any]) -> ConnectionPool:
        settings = get_postgres_settings(config)
        if self.pool is not None and self.pool_dsn == settings.dsn:
            return self.pool
        if self.pool is not None:
            self.pool.close()

        self.pool = ConnectionPool(
            conninfo=settings.dsn,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            timeout=settings.connect_timeout_seconds,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": settings.connect_timeout_seconds,
            },
            open=True,
        )
        self.pool_dsn = settings.dsn
        logger.info(
            "Initialized Postgres pool (min=%s, max=%s)",
            settings.pool_min_size,
            settings.pool_max_size,
        )
        return self.pool

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
        self.pool = None
        self.pool_dsn = None


postgres_pool_manager = PostgresPoolManager()


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)


def normalize_psycopg_dsn(dsn: str) -> str:
    """Convert SQLAlchemy-style Postgres URLs to a psycopg-compatible URL."""
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def get_database_url(config: Dict[str, Any]) -> str:
    database_config = mapping_at(config, "database")
    env_name = str(database_config.get("url_env") or DEFAULT_DATABASE_URL_ENV)
    dsn = os.getenv(env_name) or database_config.get("url")
    if not dsn:
        raise RuntimeError(
            f"Postgres database URL is required; set {env_name} or database.url"
        )
    return str(dsn)


def get_postgres_settings(config: Dict[str, Any]) -> PostgresSettings:
    database_config = mapping_at(config, "database")
    return PostgresSettings(
        dsn=normalize_psycopg_dsn(get_database_url(config)),
        pool_min_size=int(database_config.get("pool_min_size", DEFAULT_POOL_MIN_SIZE)),
        pool_max_size=int(database_config.get("pool_max_size", DEFAULT_POOL_MAX_SIZE)),
        connect_timeout_seconds=int(
            database_config.get(
                "connect_timeout_seconds",
                DEFAULT_CONNECT_TIMEOUT_SECONDS,
            )
        ),
    )


def get_postgres_pool(config: Dict[str, Any]) -> ConnectionPool:
    """Return the process-wide Postgres connection pool for the configured DSN."""
    return postgres_pool_manager.get_pool(config)


def close_postgres_pool() -> None:
    postgres_pool_manager.close()


def validate_postgres_runtime(
    config: Dict[str, Any],
    *,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate connectivity and require the database to be at Alembic head."""
    settings = get_postgres_settings(config)
    root = project_root or Path(__file__).resolve().parents[1]
    alembic_ini = root / "alembic.ini"
    alembic_cfg = AlembicConfig(str(alembic_ini))
    script = ScriptDirectory.from_config(alembic_cfg)
    expected_heads = set(script.get_heads())

    with psycopg.connect(
        settings.dsn,
        connect_timeout=settings.connect_timeout_seconds,
        row_factory=dict_row,
    ) as conn:
        version_row = conn.execute("SHOW server_version").fetchone()
        exists_row = conn.execute("SELECT to_regclass('public.alembic_version') AS table_name").fetchone()
        if not exists_row or not exists_row["table_name"]:
            raise RuntimeError(
                "Postgres schema is not initialized; run Alembic upgrade head before starting FingerprintHub"
            )
        revision_rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()

    current_heads = {row["version_num"] for row in revision_rows}
    if current_heads != expected_heads:
        raise RuntimeError(
            "Postgres schema revision mismatch; "
            f"database={sorted(current_heads)} expected={sorted(expected_heads)}. "
            "Run Alembic upgrade head out of band before starting FingerprintHub."
        )

    return {
        "server_version": version_row["server_version"] if version_row else None,
        "alembic_heads": sorted(current_heads),
    }
