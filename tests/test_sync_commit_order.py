"""Real PostgreSQL commit interleavings; use only the disposable test DSN."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import time

import pytest

from api import fingerprint_store as store


def contribute(pool, consumer, phash):
    return store.contribute(
        pool, consumer_id=consumer, phash_hex=phash, algorithm="phash",
        algorithm_version="v1", normalization_version="v1", category="scam",
        action="kick", source_guild_id=None, reason=None, source_url=None,
        auto_added=False, provenance="manual_staff", now_ms=1000,
    )[0]


class PausedPool:
    """Pause after the real write but before its real transaction commits."""
    def __init__(self, pool, *, rollback=False):
        self.pool = pool
        self.ready = threading.Event()
        self.release = threading.Event()
        self.rollback = rollback
        self.pid = None

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            self.pid = conn.info.backend_pid
            outer = self

            class Connection:
                execute = conn.execute

                @contextmanager
                def transaction(self):
                    with conn.transaction(force_rollback=outer.rollback):
                        yield
                        outer.ready.set()
                        assert outer.release.wait(10), "test did not release writer"

            yield Connection()


class ObservedPool:
    def __init__(self, pool):
        self.pool = pool
        self.ready = threading.Event()
        self.pid = None

    @contextmanager
    def connection(self):
        with self.pool.connection() as conn:
            self.pid = conn.info.backend_pid
            self.ready.set()
            yield conn


def mutation(kind, pool, consumer, row, phash):
    if kind in {"insert", "resurrect"}:
        return contribute(pool, consumer, phash)
    if kind == "hide":
        return store.flag(pool, fingerprint_id=row["id"], consumer_id=consumer,
                          reason=None, now_ms=2000, auto_hide_threshold=1)
    return store.soft_delete(pool, fingerprint_id=row["id"], now_ms=2000)


def setup_row(pool, consumer, kind, phash):
    if kind == "insert":
        return None
    row = contribute(pool, consumer, phash)
    if kind == "resurrect":
        store.soft_delete(pool, fingerprint_id=row["id"], now_ms=1500)
    return row


def sync(pool, cursor):
    return store.sync(pool, requester_consumer_id=0, since_seq=cursor, limit=100)


@pytest.mark.parametrize("first", ["insert", "resurrect", "hide", "delete"])
@pytest.mark.parametrize("second", ["insert", "resurrect", "hide", "delete"])
@pytest.mark.parametrize("rollback", [False, True])
def test_sync_never_skips_late_committing_writer(pool, make_consumer, first, second, rollback):
    consumer, _ = make_consumer("writer", ["write"])
    a = setup_row(pool, consumer, first, "aaaaaaaaaaaaaaaa")
    b = setup_row(pool, consumer, second, "bbbbbbbbbbbbbbbb")
    _, cursor, _ = sync(pool, 0)
    paused = PausedPool(pool, rollback=rollback)
    observed = ObservedPool(pool)
    with ThreadPoolExecutor(max_workers=2) as executor:
        slow = executor.submit(mutation, first, paused, consumer, a, "aaaaaaaaaaaaaaaa")
        assert paused.ready.wait(5)
        fast = executor.submit(mutation, second, observed, consumer, b, "bbbbbbbbbbbbbbbb")
        try:
            assert observed.ready.wait(5)
            deadline = time.monotonic() + 5
            while not fast.done():
                with pool.connection() as conn:
                    state = conn.execute(
                        "SELECT wait_event FROM pg_stat_activity WHERE pid = %s", (observed.pid,)
                    ).fetchone()
                if state and state["wait_event"] == "advisory":
                    break
                assert time.monotonic() < deadline, "writer neither committed nor waited on ordering lock"
                time.sleep(0.01)
            before, cursor, _ = sync(pool, cursor)
        finally:
            paused.release.set()
        slow.result(timeout=5)
        fast.result(timeout=5)
    after, cursor, _ = sync(pool, cursor)
    seen = {row["phash_hex"]: row for row in before + after}
    expected = {"bbbbbbbbbbbbbbbb"} if rollback else {"aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"}
    assert set(seen) == expected
    for kind, phash in [(first, "aaaaaaaaaaaaaaaa"), (second, "bbbbbbbbbbbbbbbb")]:
        if phash in seen:
            assert seen[phash]["status"] == {"hide": "hidden", "delete": "deleted"}.get(kind, "active")
    # Rollback gaps and released locks must not prevent the next legitimate write.
    contribute(pool, consumer, "cccccccccccccccc")
    later, _, _ = sync(pool, cursor)
    assert [row["phash_hex"] for row in later] == ["cccccccccccccccc"]


def test_disconnected_writer_releases_ordering_lock(pool, make_consumer):
    import psycopg

    consumer, _ = make_consumer("writer", ["write"])
    paused = PausedPool(pool)
    with ThreadPoolExecutor(max_workers=2) as executor:
        lost = executor.submit(contribute, paused, consumer, "aaaaaaaaaaaaaaaa")
        assert paused.ready.wait(5)
        try:
            with pool.connection() as conn:
                assert conn.execute("SELECT pg_terminate_backend(%s) AS stopped",
                                    (paused.pid,)).fetchone()["stopped"]
        finally:
            paused.release.set()
        with pytest.raises(psycopg.OperationalError):
            lost.result(timeout=5)
        contribute(pool, consumer, "bbbbbbbbbbbbbbbb")
    rows, _, _ = sync(pool, 0)
    assert [row["phash_hex"] for row in rows] == ["bbbbbbbbbbbbbbbb"]
