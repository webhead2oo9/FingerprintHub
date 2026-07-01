"""End-to-end API tests: contribute, sync, hit, flag/auto-hide, delete, redaction."""

from __future__ import annotations

from tests.conftest import auth

PHASH_A = "0123456789abcdef"
PHASH_B = "fedcba9876543210"
PHASH_C = "aaaabbbbccccdddd"


async def _contribute(client, key, phash, **overrides):
    payload = {
        "phash_hex": phash,
        "category": "scam",
        "action": "kick",
        "reason": "secret note",
        "source_url": "https://example.invalid/x.png",
    }
    payload.update(overrides)
    return await client.post("/v1/fingerprints", headers=auth(key), json=payload)


async def test_contribute_and_duplicate(client, make_consumer):
    _cid, key = make_consumer("nexarion", ["read", "write"])
    resp = await _contribute(client, key, PHASH_A)
    assert resp.status == 201
    row = await resp.json()
    assert row["phash_hex"] == PHASH_A
    assert row["status"] == "active"
    # owner sees their own reason/source_url
    assert row["reason"] == "secret note"

    dup = await _contribute(client, key, PHASH_A)
    assert dup.status == 409
    assert (await dup.json())["existing_id"] == row["id"]


async def test_validation_rejects_bad_input(client, make_consumer):
    _cid, key = make_consumer("nexarion", ["read", "write"])
    bad_hash = await _contribute(client, key, "NOTHEX")
    assert bad_hash.status == 400
    bad_action = await _contribute(client, key, PHASH_A, action="nuke")
    assert bad_action.status == 400
    bad_cat = await _contribute(client, key, PHASH_A, category="memes")
    assert bad_cat.status == 400


async def test_sync_excludes_own_but_returns_peer(client, make_consumer):
    _a, key_a = make_consumer("bot-a", ["read", "write"])
    _b, key_b = make_consumer("bot-b", ["read", "write"])
    await _contribute(client, key_a, PHASH_A)
    await _contribute(client, key_b, PHASH_B)

    # bot-a should NOT see its own contribution, but SHOULD see bot-b's.
    resp = await client.get("/v1/fingerprints/sync?since=0", headers=auth(key_a))
    assert resp.status == 200
    body = await resp.json()
    phashes = {f["phash_hex"] for f in body["fingerprints"]}
    assert PHASH_B in phashes
    assert PHASH_A not in phashes
    # sync feed never leaks secrets
    assert all("reason" not in f for f in body["fingerprints"])


async def test_hit_increments_count(client, make_consumer):
    _cid, key = make_consumer("nexarion", ["read", "write"])
    row = await (await _contribute(client, key, PHASH_A)).json()
    r1 = await client.post(
        f"/v1/fingerprints/{row['id']}/hit", headers=auth(key), json={"distance": 2}
    )
    assert r1.status == 200
    assert (await r1.json())["hit_count"] == 1
    r2 = await client.post(f"/v1/fingerprints/{row['id']}/hit", headers=auth(key), json={})
    assert (await r2.json())["hit_count"] == 2


async def test_flag_auto_hides_at_threshold_and_tombstones(client, make_consumer):
    _owner, key_owner = make_consumer("owner", ["read", "write"])
    _f1, key_f1 = make_consumer("flagger-1", ["read", "write"])
    _f2, key_f2 = make_consumer("flagger-2", ["read", "write"])
    row = await (await _contribute(client, key_owner, PHASH_A)).json()
    fid = row["id"]

    one = await client.post(f"/v1/fingerprints/{fid}/flag", headers=auth(key_f1), json={})
    assert one.status == 200
    one_body = await one.json()
    assert one_body["flag_count"] == 1 and one_body["status"] == "active"

    two = await client.post(f"/v1/fingerprints/{fid}/flag", headers=auth(key_f2), json={})
    two_body = await two.json()
    assert two_body["flag_count"] == 2 and two_body["status"] == "hidden"

    # A flagger now syncing should receive the hidden tombstone (status carried).
    resp = await client.get("/v1/fingerprints/sync?since=0", headers=auth(key_f1))
    rows = (await resp.json())["fingerprints"]
    hidden = [f for f in rows if f["id"] == fid]
    assert hidden and hidden[0]["status"] == "hidden"

    # Non-owner GET of a hidden row is 404; owner still sees it.
    assert (await client.get(f"/v1/fingerprints/{fid}", headers=auth(key_f1))).status == 404
    owner_get = await client.get(f"/v1/fingerprints/{fid}", headers=auth(key_owner))
    assert owner_get.status == 200 and (await owner_get.json())["status"] == "hidden"


async def test_delete_trust_model(client, make_consumer):
    _owner, key_owner = make_consumer("owner", ["read", "write"])
    _other, key_other = make_consumer("other", ["read", "write"])
    row = await (await _contribute(client, key_owner, PHASH_A)).json()
    fid = row["id"]

    # Non-owner cannot delete.
    assert (await client.delete(f"/v1/fingerprints/{fid}", headers=auth(key_other))).status == 403
    # Owner can; second delete is 404.
    assert (await client.delete(f"/v1/fingerprints/{fid}", headers=auth(key_owner))).status == 204
    assert (await client.delete(f"/v1/fingerprints/{fid}", headers=auth(key_owner))).status == 404

    # Deleted row surfaces as a tombstone in a peer's sync feed.
    resp = await client.get("/v1/fingerprints/sync?since=0", headers=auth(key_other))
    rows = (await resp.json())["fingerprints"]
    tomb = [f for f in rows if f["id"] == fid]
    assert tomb and tomb[0]["status"] == "deleted"


async def test_redaction_for_non_owner(client, make_consumer):
    _owner, key_owner = make_consumer("owner", ["read", "write"])
    _other, key_other = make_consumer("other", ["read", "write"])
    row = await (await _contribute(client, key_owner, PHASH_A)).json()
    fid = row["id"]
    other_view = await client.get(f"/v1/fingerprints/{fid}", headers=auth(key_other))
    assert other_view.status == 200
    body = await other_view.json()
    assert body["reason"] is None and body["source_url"] is None


async def test_resurrect_after_delete(client, make_consumer):
    _cid, key = make_consumer("nexarion", ["read", "write"])
    row = await (await _contribute(client, key, PHASH_A)).json()
    await client.delete(f"/v1/fingerprints/{row['id']}", headers=auth(key))
    # Re-contributing the same phash for the same consumer resurrects it.
    again = await _contribute(client, key, PHASH_A)
    assert again.status == 201
    assert (await again.json())["status"] == "active"
