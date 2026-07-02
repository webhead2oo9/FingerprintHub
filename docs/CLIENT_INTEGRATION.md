# FingerprintHub — Client Integration Guide

How a Discord bot becomes a FingerprintHub consumer. Nexarion is the reference
implementation (`helpers/scam_detection/fingerprint_store.py`); this guide
generalizes it.

## Prerequisites

1. An API key: an operator runs
   `tools/create_consumer.py --name <bot> --scopes read,write` and gives you the
   `fph_…` key. Store it in your bot's env, never in committed config.
2. Your bot must compute pHashes **the same way** as other participating clients
   — same `algorithm` / `algorithm_version` / `normalization_version` triple
   (the reference triple is `phash` / `imagehash.phash` / `alpha_white_v1`:
   `imagehash.phash` over the image flattened onto a white background for alpha).
   If your preprocessing differs, your hashes are NOT Hamming-comparable and you
   must use (and filter sync on) your own triple.

## The cache model (do this, not a naive remote client)

**Do not** call the hub on your moderation hot path. Keep your existing local
store + in-memory index and treat the hub as a sync source + contribution
target:

- **Match locally, in memory.** No network call per message. Matching stays
  exactly as fast as a local-only implementation.
- **Load from your local DB at boot.** No hard dependency on the hub being
  reachable at startup.
- **Background sync loop** pulls new/changed rows and upserts them locally.
- **Contribute local-first**: write locally immediately, then push to the hub in
  the background.
- **Report hits fire-and-forget**: never block enforcement on the hub.

## Local schema additions

Add to your fingerprint table:
- `hub_fingerprint_id` (nullable) — the hub row id once linked.
- `origin` (`local` | `hub`) — `local` = you contributed it; `hub` = synced from
  a peer. Drives delete-vs-flag behavior.
- a single-row **sync watermark** (`last_sync_seq BIGINT`).
- a **suppression** table keyed by `hub_fingerprint_id` — rows a moderator
  removed locally, so sync won't resurrect them.

A partial unique index on `hub_fingerprint_id WHERE NOT NULL` lets you upsert
synced rows with `ON CONFLICT`.

## The four flows

### Sync (background, every N seconds)
```
watermark = read local watermark
loop:
  GET /v1/fingerprints/sync?since=watermark&limit=200
      &algorithm=…&algorithm_version=…&normalization_version=…
  for row in fingerprints:
     if triple(row) != your triple:            continue   # defensive
     if row.status in (hidden, deleted):       delete local row by hub id; continue
     if hub id in suppressions:                continue
     if row.action/category not in your enums: skip (log)  # poison-pill guard
     upsert local row (origin='hub', hub_fingerprint_id=row.id, added_by="hub:<consumer_id>")
  watermark = next_since (persist AFTER applying the page)
  stop when has_more is false
if anything changed: rebuild your in-memory index
```
Isolate per-row failures so one bad row can't break a whole page.

### Contribute (staff marks an image)
```
insert locally now (origin='local')  -> return the LOCAL id to the UI
background:
  POST /v1/fingerprints {phash_hex, category, action, triple, reason?, source_url?, …}
  on 201 -> stamp local row: hub_fingerprint_id = resp.id, origin='local'
  on 409 -> stamp with resp.existing_id (a prior attempt already landed it)
  on error -> leave unlinked; the next contribute/backfill reconciles
```

### Remove (staff removes a fingerprint)
```
delete locally
if origin == 'local' and hub_fingerprint_id:  DELETE /v1/fingerprints/{hub_id}   (fire-and-forget)
if origin == 'hub'  and hub_fingerprint_id:
     add hub_id to local suppression (await this — it must beat the next sync)
     POST /v1/fingerprints/{hub_id}/flag                                          (fire-and-forget)
```

### Hit (after enforcement)
```
bump your local hit_count as before
fire-and-forget POST /v1/fingerprints/{hub_id}/hit   (only if the row has a hub id)
```
Schedule the fire-and-forget task from async code with a running event loop — not
from inside a thread-pool DB call.

## Config knobs (recommended)

Gate everything behind an enable flag defaulted **off**, plus a base URL and the
API key from env. This lets you ship the code + schema migration first (inert),
then flip it on as a separate step. Recommended: `hub_enabled` (bool),
`hub_base_url`, `hub_sync_interval_seconds` (~300), `hub_request_timeout_seconds`
(~5), and the API key in env.

## One-time backfill

To seed the hub with your existing catalog: read local rows that aren't linked
(`hub_fingerprint_id IS NULL`), `POST` each, and stamp the returned id back
locally. Make it idempotent (skip already-linked rows; treat `409` as success
using `existing_id`) so it's safely resumable. No reference script exists yet —
Nexarion only contributes on new adds, so rows created before its hub
integration stay unlinked until backfilled.

## Gotchas

- The hub **excludes your own contributions** from your sync feed, so you never
  re-ingest your own rows — but only if you contribute under the same consumer
  key you sync with. Use one key per bot.
- Advance the watermark to `next_since` **after** applying the page, not before.
- Suppression on remove of a `hub`-origin row is what stops the next sync from
  resurrecting it (a single flag won't hide it hub-side until the threshold).
