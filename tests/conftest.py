"""Shared fixtures for FingerprintHub tests.

Postgres-backed and destructive: skipped unless
FINGERPRINTHUB_TEST_DATABASE_URL points at a disposable database already
migrated to Alembic head.
"""

from __future__ import annotations

import os

import pytest
from aiohttp.test_utils import TestClient, TestServer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Provide deterministic test encryption keys before any module loads the keyring.
os.environ.setdefault(
    "FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS",
    "v1:" + "A" * 43 + "=",  # 32 zero-ish bytes base64; valid 32-byte key
)
os.environ.setdefault("FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID", "v1")

from api import consumers_store  # noqa: E402
from api.app import create_app  # noqa: E402
from config import ServiceConfig  # noqa: E402
from utils.time_utils import utc_now_ms  # noqa: E402

TEST_DSN_ENV = "FINGERPRINTHUB_TEST_DATABASE_URL"


def _dsn() -> str:
    dsn = os.getenv(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} not set; skipping Postgres-backed tests")
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def pool():
    p = ConnectionPool(
        conninfo=_dsn(),
        min_size=1,
        max_size=4,
        kwargs={"row_factory": dict_row},
        open=True,
    )
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _clean_tables(pool):
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "TRUNCATE fingerprint_flags, fingerprint_hits, fingerprints, "
                "consumers RESTART IDENTITY CASCADE"
            )
    yield


@pytest.fixture
def make_consumer(pool):
    def _make(name: str, scopes):
        return consumers_store.create_consumer(
            pool, name=name, scopes=list(scopes), now_ms=utc_now_ms()
        )

    return _make


@pytest.fixture
async def client(pool):
    app = create_app(pool=pool, config=ServiceConfig())
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


def auth(api_key: str):
    return {"X-API-Key": api_key}
