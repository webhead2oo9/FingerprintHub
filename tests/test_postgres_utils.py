from unittest.mock import Mock

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from utils import postgres_utils


@pytest.mark.no_database
def test_pool_checks_connections_before_checkout(monkeypatch):
    """The application pool wires in psycopg's checkout check callback."""
    connection_pool = Mock(return_value=Mock())
    connection_pool.check_connection = ConnectionPool.check_connection
    monkeypatch.setattr(postgres_utils, "ConnectionPool", connection_pool)

    manager = postgres_utils.PostgresPoolManager()
    manager.get_pool(
        {
            "database": {
                "url": "postgresql://test.invalid/fingerprinthub",
                "pool_min_size": 1,
                "pool_max_size": 2,
                "connect_timeout_seconds": 3,
            }
        }
    )

    assert connection_pool.call_args.kwargs["check"] is ConnectionPool.check_connection


def _terminate_idle_connection(pool: ConnectionPool, database_url: str) -> int:
    with pool.connection() as conn:
        backend_pid = conn.info.backend_pid

    with psycopg.connect(database_url, autocommit=True) as terminator:
        terminated = terminator.execute(
            "SELECT pg_terminate_backend(%s)", (backend_pid,)
        ).fetchone()[0]

    assert terminated is True
    return backend_pid


def test_pool_replaces_a_terminated_idle_connection(database_url):
    """A checkout check prevents a stale socket from reaching application code."""
    with ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=1,
        timeout=3,
        check=None,
        open=True,
    ) as unchecked_pool:
        unchecked_pool.wait(timeout=3)
        terminated_pid = _terminate_idle_connection(unchecked_pool, database_url)

        with pytest.raises(psycopg.OperationalError):
            with unchecked_pool.connection() as conn:
                assert conn.info.backend_pid == terminated_pid
                conn.execute("SELECT 1").fetchone()

    manager = postgres_utils.PostgresPoolManager()
    checked_pool = manager.get_pool(
        {
            "database": {
                "url": database_url,
                "pool_min_size": 1,
                "pool_max_size": 1,
                "connect_timeout_seconds": 3,
            }
        }
    )
    try:
        checked_pool.wait(timeout=3)
        terminated_pid = _terminate_idle_connection(checked_pool, database_url)

        with checked_pool.connection() as conn:
            replacement_pid = conn.info.backend_pid
            result = conn.execute("SELECT 1 AS value").fetchone()["value"]

        assert replacement_pid != terminated_pid
        assert result == 1
    finally:
        manager.close()
