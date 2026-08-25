# FingerprintHub Client Integration Guide

How a community-safety client becomes a FingerprintHub consumer.

## Prerequisites

1. An API key. An operator runs
   `tools/create_consumer.py --name <client> --scopes read,write` and gives you
   the `fph_...` key. Store it in the client's environment, never in committed
   configuration.
2. pHashes computed the same way as every other participant: the same
   `algorithm` / `algorithm_version` / `normalization_version` triple. The
   reference triple is `phash` / `imagehash.phash` / `alpha_white_v1`, meaning
   `imagehash.phash` over the image flattened onto a white background for
   alpha. If your preprocessing differs, your hashes are not
   Hamming-comparable. Use your own triple, and filter sync on it.

## The cache model

Do not call the hub on your moderation hot path. Keep your existing local store
and in-memory index, and treat the hub as a sync source and a contribution
target.

Matching happens locally, in memory. There is no network call per message, so
it runs as fast as a local-only implementation. The index loads from your local
DB at boot, so the hub does not have to be reachable at startup. A background
sync loop pulls new and changed rows and upserts them locally. Contributions go
local-first: write locally immediately, then push to the hub in the background.
Hit reports are fire-and-forget; enforcement never blocks on the hub.

## Local schema additions

Add to your fingerprint table:
- `hub_fingerprint_id` (nullable): the hub row id once linked.
- `origin` (`local` | `hub`): `local` = you contributed it; `hub` = synced from
  a peer. Drives delete-vs-flag behavior.
- a single-row sync watermark (`last_sync_seq BIGINT`).
- a suppression table keyed by `hub_fingerprint_id`, holding rows a moderator
  removed locally so sync won't resurrect them.

A partial unique index on `hub_fingerprint_id WHERE NOT NULL` lets you upsert
synced rows with `ON CONFLICT`.

## The four flows

### Sync (background, every N seconds)
```
watermark = read local watermark
loop:
  GET /v1/fingerprints/sync?since=watermark&limit=200
      &algorithm=...&algorithm_version=...&normalization_version=...
  for row in fingerprints:
     if triple(row) != your triple:            continue   # defensive
     if row.status in (hidden, deleted):       delete local row by hub id; continue
     if hub id in suppressions:                continue
     if row.action/category not in your enums: skip (log)  # poison-pill guard
     upsert local row (origin='hub', hub_fingerprint_id=row.id, added_by="hub")
  watermark = next_since (persist AFTER applying the page)
  stop when has_more is false
if anything changed: rebuild your in-memory index
```
Isolate per-row failures so one bad row can't break a whole page.

### Contribute (staff marks an image)
```
insert locally now (origin='local')  -> return the LOCAL id to the UI
background:
  POST /v1/fingerprints {phash_hex, category, action, triple, reason?, source_url?, ...}
  on 201 -> stamp local row: hub_fingerprint_id = resp.id, origin='local'
  on 409 -> read back resp.existing_id and reconcile before stamping
  on error -> leave unlinked; the next contribute/backfill reconciles
```
A 409 only tells you a live row already exists for your `(phash_hex,
consumer_id)` pair. It may carry a different triple, category, or action than
the one you just sent, and it may already be `hidden` if peers flagged it. Read
it back before you treat your local row as linked and active.

### Remove (staff removes a fingerprint)
```
delete locally
if origin == 'local' and hub_fingerprint_id:  DELETE /v1/fingerprints/{hub_id}   (fire-and-forget)
if origin == 'hub'  and hub_fingerprint_id:
     add hub_id to local suppression (await this; it must beat the next sync)
     POST /v1/fingerprints/{hub_id}/flag                                          (fire-and-forget)
```

### Hit (after enforcement)
```
bump your local hit_count as before
fire-and-forget POST /v1/fingerprints/{hub_id}/hit   (only if the row has a hub id)
```
Schedule the fire-and-forget task from async code with a running event loop,
not from inside a thread-pool DB call.

## Config knobs (recommended)

Gate everything behind an enable flag that defaults to off, and take the base
URL and the API key from env. That way you can ship the code and the schema
migration first, inert, then flip the flag as a separate step. The knobs:
`hub_enabled` (bool), `hub_base_url`, `hub_sync_interval_seconds` (~300),
`hub_request_timeout_seconds` (~5), and the API key in env.

## One-time backfill

To seed the hub with your existing catalog: read local rows that aren't linked
(`hub_fingerprint_id IS NULL`), `POST` each, and stamp the returned id back
locally. Make it idempotent (skip already-linked rows; reconcile `409` through
`existing_id`) so it's resumable. There is no reference backfill script,
so implement this flow against your own local schema and retry model.

## Gotchas

- The hub excludes your own contributions from your sync feed, so you never
  re-ingest your own rows. That holds only if you contribute under the same
  consumer key you sync with, so use one key per client.
- Advance the watermark to `next_since` after applying the page, not before.
- A watermark only means anything for the triple you synced with. If you change
  your triple, reset it to 0 and re-pull, or every older row matching the new
  triple stays permanently below your cursor.
- Suppression on remove of a `hub`-origin row is what stops the next sync from
  resurrecting it (a single flag won't hide it hub-side until the threshold).
