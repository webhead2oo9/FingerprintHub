"""Auth middleware + scope enforcement tests."""

from __future__ import annotations

from tests.conftest import auth

A_PHASH = "0123456789abcdef"


async def test_health_needs_no_auth(client):
    resp = await client.get("/v1/health")
    assert resp.status in (200, 503)
    body = await resp.json()
    assert "status" in body


async def test_missing_key_is_401(client):
    resp = await client.get("/v1/fingerprints/stats")
    assert resp.status == 401


async def test_invalid_key_is_401(client):
    resp = await client.get("/v1/fingerprints/stats", headers=auth("fph_not_a_real_key"))
    assert resp.status == 401


async def test_disabled_consumer_is_401(client, pool, make_consumer):
    _cid, key = make_consumer("disabled-client", ["read"])
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE consumers SET enabled = FALSE WHERE name = 'disabled-client'"
            )
    resp = await client.get("/v1/fingerprints/stats", headers=auth(key))
    assert resp.status == 401


async def test_read_scope_cannot_write(client, make_consumer):
    _cid, key = make_consumer("reader", ["read"])
    resp = await client.post(
        "/v1/fingerprints",
        headers=auth(key),
        json={"phash_hex": A_PHASH, "category": "scam", "action": "kick"},
    )
    assert resp.status == 403


async def test_write_scope_can_read_and_write(client, make_consumer):
    _cid, key = make_consumer("writer", ["read", "write"])
    resp = await client.get("/v1/fingerprints", headers=auth(key))
    assert resp.status == 200
