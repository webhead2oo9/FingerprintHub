# FingerprintHub API Reference

Base URL (default): `http://127.0.0.1:58751`. All paths are under `/v1`.

## Authentication

Every endpoint except `/v1/health` requires an `X-API-Key: fph_...` header. The
key is hashed (sha256) and looked up in `consumers`; the consumer must be
`enabled`. Requests are rate-limited per consumer, default 300/min; over that
limit you get `429`.

Scopes (a consumer holds a subset of `read`, `write`, `admin`):

| Scope | Grants |
|-------|--------|
| `read`  | sync, get, list, stats |
| `write` | contribute, hit, flag, delete-own |
| `admin` | delete/see-hidden across all consumers (reserved; not issued yet) |

Error bodies are JSON: `{"error": "..."}` (plus `existing_id` on 409).

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
excluding the caller's own contributions. Omits `reason`/`source_url` and hit
stats.
```json
200 {
  "fingerprints": [
    {"id": 48, "sync_seq": 48, "phash_hex": "...", "algorithm": "phash",
     "algorithm_version": "imagehash.phash", "normalization_version": "alpha_white_v1",
     "category": "scam", "action": "timeout", "consumer_id": 2,
     "source_guild_id": null, "added_at_ms": 1782..., "updated_at_ms": 1782...,
     "auto_added": false, "provenance": "manual_staff", "status": "active"}
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
{"phash_hex": "0123456789abcdef",   // required, 16 lowercase hex
 "category": "scam",                 // required, enum
 "action": "kick",                   // required, kick|timeout
 "algorithm": "phash",               // optional (defaults shown)
 "algorithm_version": "imagehash.phash",
 "normalization_version": "alpha_white_v1",
 "source_guild_id": "...",           // optional
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
```json
200 {"id": 48, "hit_count": 3}
404 {"error": "fingerprint not found"}   // missing or deleted
```

## POST /v1/fingerprints/{id}/flag
Scope: `write`. Idempotent per consumer. Auto-hides the row once distinct
flaggers reach `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD` (default 2). Body:
`{"reason": "..."}` (optional).
```json
200 {"id": 48, "flag_count": 1, "status": "active", "hidden": false}
200 {"id": 48, "flag_count": 2, "status": "hidden", "hidden": true}
404 {"error": "fingerprint not found"}
```

## DELETE /v1/fingerprints/{id}
Scope: `write` plus ownership (or `admin`). Soft-delete (tombstone).
```json
204   // owner or admin
403 {"error": "only the owning consumer or an admin may delete; use /flag instead"}
404   // missing or already deleted
```

## GET /v1/fingerprints/{id}
Scope: `read`. Full record. `reason`/`source_url` are `null` unless you own the
row or hold `admin`. A `hidden` row returns `404` to non-owner/non-admin.

## GET /v1/fingerprints
Scope: `read`. Browse. Query: `category`, `algorithm`, `consumer_id`,
`limit` (at most 200), `offset`, `include_hidden` (honored only with `admin`).
```json
200 {"fingerprints": [ {...detail row...} ], "count": 25}
```

## GET /v1/fingerprints/stats
Scope: `read`. Aggregates only.
```json
200 {"total_active": 47, "total_hidden": 0, "total_deleted": 1, "total_hits": 0,
     "by_category": {"scam": 46, "crypto": 1},
     "by_provenance": {"llm_review_approved": 46, "manual_staff": 1},
     "active_consumers": 1}
```
