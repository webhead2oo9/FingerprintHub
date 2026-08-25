# FingerprintHub API Reference

Base URL (default): `http://127.0.0.1:58751`. All paths are under `/v1`.

## Authentication

Every endpoint except `/v1/health` requires an `X-API-Key: fph_...` header. The
key is hashed (sha256) and looked up in `consumers`; the consumer must be
`enabled`. Requests are rate-limited per consumer, default 300/min; over that
limit you get `429`. That window is tracked in memory per serving process, so
it isn't shared across workers or replicas.

Scopes (a consumer holds a subset of `read`, `write`, `admin`):

| Scope | Grants |
|-------|--------|
| `read`  | sync, get, list-own |
| `write` | contribute, hit, flag, delete-own |
| `admin` | stats and cross-owner browse/ownership override; pairs with another scope |

Scopes are independent flags, not levels. `admin` overrides the ownership check
but does not imply the scope a route needs, so deleting another consumer's row
takes `write` plus `admin`, and seeing their hidden row takes `read` plus
`admin`.

Error bodies are JSON: `{"error": "..."}` (plus `existing_id` on 409, and
`details` on a JSON parse failure). A
missing or unrecognized key is `401`; a missing scope is `403`. If Postgres is
unreachable, any route answers `503` with
`{"error": "database temporarily unavailable"}`.

Every route taking an `{id}` returns `400` for a non-integer id, and the routes
with integer query params (`since`, `limit`, `offset`, `consumer_id`) return
`400` when those don't parse. A malformed JSON body is `400` on contribute;
`hit` and `flag` treat an unparseable body as an empty one.

---

## GET /v1/health
No auth. Liveness + DB ping.
```json
200 {"status": "ok", "db": true}
503 {"status": "degraded", "db": false}
```

## GET /v1/fingerprints/sync
Scope: `read`. Incremental pull. Query params:

| Param | Default | Notes |
|-------|---------|-------|
| `since` | `0` | last `sync_seq` durably applied |
| `limit` | 200 | clamped to `[1, FINGERPRINTHUB_MAX_SYNC_LIMIT]` (500) |
| `algorithm`, `algorithm_version`, `normalization_version` | none | optional compatibility filter |

Returns active rows and hidden/deleted tombstones with `sync_seq > since`,
excluding the caller's own contributions. The peer contract contains only the
fields needed to replicate and retire a fingerprint; tenant attribution,
source metadata, timestamps, provenance, automation flags, free text, flags,
and activity statistics are omitted.
```json
200 {
  "fingerprints": [
    {"id": 48, "sync_seq": 48, "phash_hex": "...", "algorithm": "phash",
     "algorithm_version": "imagehash.phash", "normalization_version": "alpha_white_v1",
     "category": "scam", "action": "timeout", "status": "active"}
  ],
  "next_since": 48,
  "has_more": false
}
```
Client contract: apply the page, then set your watermark to `next_since`; if
`has_more`, immediately request again with `since=next_since`. Rows whose
`status` is `hidden` or `deleted` should be removed locally.

## POST /v1/fingerprints
Scope: `write`. Contribute a fingerprint (or resurrect a previously-deleted one
for this consumer). Body:
```json
{"phash_hex": "0123456789abcdef",   // required, 16 hex chars, lowercased
 "category": "scam",                 // required, enum
 "action": "kick",                   // required, kick|timeout
 "algorithm": "phash",               // optional (defaults shown)
 "algorithm_version": "imagehash.phash",
 "normalization_version": "alpha_white_v1",
 "source_guild_id": "...",           // optional, stored encrypted
 "reason": "...",                    // optional, stored encrypted
 "source_url": "...",                // optional, stored encrypted
 "auto_added": false, "provenance": "manual_staff"}
```
- `201` returns the created row (owner sees `reason`/`source_url`).
- `409 {"error": "duplicate...", "existing_id": N}` means a live row already
  exists for this `(phash_hex, consumer)`.
- `400` means an invalid `phash_hex`/`category`/`action`.

## POST /v1/fingerprints/{id}/hit
Scope: `write`. Record enforcement; bumps `hit_count`/`last_hit_at_ms` (does not
change `sync_seq`). Body (all optional): `{"guild_id": "...", "distance": 2}`.
The guild/community identifier is encrypted at rest and never returned to peers.
```json
200 {"id": 48, "hit_count": 3}
404 {"error": "fingerprint not found"}   // missing or deleted
```

## POST /v1/fingerprints/{id}/flag
Scope: `write`. Idempotent per consumer. Auto-hides the row once distinct
flaggers reach `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD` (default 2). Body:
`{"reason": "..."}` (optional).
The reason is encrypted at rest and is not returned by the API.
```json
200 {"id": 48, "flag_count": 1, "status": "active", "hidden": false}
200 {"id": 48, "flag_count": 2, "status": "hidden", "hidden": true}
404 {"error": "fingerprint not found"}
```
`hidden` reports whether this call is what hid the row, not whether the row is
currently hidden. Flagging an already-hidden row returns `status: "hidden"`
with `hidden: false`.

## DELETE /v1/fingerprints/{id}
Scope: `write` plus ownership (or `admin`). Soft-delete (tombstone).
```json
204   // owner or admin
403 {"error": "only the owning consumer or an admin may delete; use /flag instead"}
404   // missing or already deleted
```

## GET /v1/fingerprints/{id}
Scope: `read`. Owners and admins receive the full record with encrypted fields
decrypted. A non-owner receives the same minimal shape as sync. A `hidden` row
returns `404` to non-owner/non-admin, and a `deleted` row returns `404` to
everyone, owners and admins included.

## GET /v1/fingerprints
Scope: `read`. Browse your own rows. Query: `category`, `algorithm`, `consumer_id`,
`limit` (default 50, clamped to `FINGERPRINTHUB_MAX_LIST_LIMIT`, 200),
`offset` (default 0), `include_hidden` (honored only with `admin`).

Non-admin callers are forced to their own consumer id; filtering for another
consumer returns `403`. Admins may browse all consumers, filter by consumer id,
and use `include_hidden`. Deleted rows never appear. `count` is the page size,
not the total number of matching rows, so page until a short page.
```json
200 {"fingerprints": [ {...detail row...} ], "count": 25}
```

## GET /v1/fingerprints/stats
Scope: `admin`. Aggregates only. `by_category` and `by_provenance` count active
rows only, and `active_consumers` counts enabled consumers.
```json
200 {"total_active": 47, "total_hidden": 0, "total_deleted": 1, "total_hits": 0,
     "by_category": {"scam": 46, "crypto": 1},
     "by_provenance": {"llm_review_approved": 46, "manual_staff": 1},
     "active_consumers": 1}
```
