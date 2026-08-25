# FingerprintHub — Architecture

## Purpose

FingerprintHub is a small multi-tenant service that shares perceptual-hash
(pHash) fingerprints of known-bad images across community-safety clients. When
one client fingerprints an image, every other participating client can
recognize it without repeating the manual review.

The hub is deliberately dumb about images: it only stores and serves the
`phash_hex` string. **Image decoding and pHash computation happen entirely in
the clients**, and so does matching. This keeps the hub tiny, keeps client
moderation hot paths network-free, and means the hub never handles image bytes.

## Components

```
main.py                      entrypoint: validate Postgres@head -> pool -> create_app -> run
config.py                    flat env-var config (ServiceConfig)
api/
  app.py                     aiohttp app factory; route table; error middleware
  auth.py                    X-API-Key -> sha256 lookup -> scope + rate-limit middleware
  fingerprints.py            HTTP handlers; presentation + redaction
  fingerprint_store.py       DB CRUD; the sync_seq + tombstone invariants live here
  consumers_store.py         consumer rows + API-key primitives
  errors.py                  handle_errors decorator, parse_json_body
utils/                       postgres_utils, field_encryption, async_utils,
                             config_access, time_utils
alembic/                     schema history (0001_init)
tools/
  create_consumer.py         operator CLI: mint a consumer + one-time API key
  generate_field_key.py      operator CLI: generate an AES-256 field-encryption key
```

## Request lifecycle

1. `error_middleware` (outermost) wraps everything; unhandled exceptions become 500s.
2. `auth_middleware` skips `/v1/health`; otherwise hashes `X-API-Key`, looks the
   consumer up by exact hash, checks `enabled`, applies a per-consumer sliding
   rate limit, throttled-updates `last_seen_at_ms`, and attaches the consumer to
   `request['consumer']`.
3. The handler calls `require_scope(request, ...)` then does its work via
   `run_blocking_io` (psycopg is synchronous; DB work is offloaded to a thread).

## Data model

See `alembic/versions/0001_init.py` for exact DDL.

- **`consumers`** — one row per client. `api_key_hash` = `sha256(raw_key)`
  (raw key never stored). `scopes TEXT[]` ⊆ `{read, write, admin}`. `enabled`.
- **`fingerprints`** — the shared catalog. Key columns:
  - `phash_hex`, plus the compatibility triple `(algorithm, algorithm_version,
    normalization_version)`.
  - `category` ∈ {scam,nsfw,crypto,phishing,other}; `action` ∈ {kick,timeout}
    (a softban-intent hint clients may honor or override locally).
  - `consumer_id` — the contributing owner. `UNIQUE (phash_hex, consumer_id)`.
  - `reason`, `source_url` — encrypted at rest, redacted from non-owners.
  - `status` ∈ {active, hidden, deleted}. `flag_count`.
  - **`sync_seq`** — the incremental-sync cursor (see below).
  - `hit_count`, `last_hit_at_ms` — stats, never synced to peers.
- **`fingerprint_hits`** — per-hit audit rows (who enforced, where, distance).
- **`fingerprint_flags`** — one row per (fingerprint, flagging consumer); drives
  auto-hide.

## The sync protocol

Clients pull incrementally from `GET /v1/fingerprints/sync?since=<seq>`:

- Rows are ordered by **`sync_seq`**, a monotonic value from a dedicated Postgres
  sequence (`fingerprints_sync_seq`). The endpoint returns rows with
  `sync_seq > since`, plus `next_since` (advance the watermark to this only after
  durably applying the page) and `has_more`.
- **`sync_seq` advances on content changes only** — insert, resurrect, status
  flips (hide/delete). It is **not** touched by stats-only writes (hits,
  sub-threshold flags). This is why a popular fingerprint that gets hits every
  minute doesn't churn every client's sync feed.
- **The feed excludes the requesting consumer's own contributions** (`consumer_id
  <> requester`). A client already has its own rows locally; re-ingesting them
  would create duplicates.
- **Tombstones**: the feed includes `hidden`/`deleted` rows (they have a fresh
  `sync_seq` from the status flip). Clients delete these locally. Deletes are
  soft (`status='deleted'`) precisely so the tombstone can propagate; physical
  purge is a later retention concern.
- **Compatibility filter**: clients pass their `(algorithm, algorithm_version,
  normalization_version)` triple; the feed returns only matching rows, so a
  client never ingests hashes it cannot Hamming-compare against.

Because the cursor is a monotonic sequence (not a wall-clock timestamp), sync is
immune to clock skew and same-millisecond ordering bugs, and pages with strict
`>`.

## Trust model (cross-consumer)

The shared dataset is security-relevant, so deletion power is conservative:

- A consumer may **soft-delete only its own** contributions (status change →
  tombstone).
- A consumer that disagrees with **someone else's** row can only **flag** it.
  Once `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD` distinct consumers flag a row it
  auto-hides (excluded from sync/browse; a tombstone propagates). An `admin`
  scope can soft-delete anything (held in reserve; not issued to any consumer
  yet).

This means one client's mistake or bad actor can't silently delete protection
for everyone. Finer trust tuning (scaling the threshold to consumer count,
review queues, unhide flows) is intentionally deferred until a second consumer
actually exists.

## What is intentionally NOT here

- No image handling / pHash computation / Hamming matching (all client-side).
- No cross-consumer near-duplicate dedup beyond exact `(phash_hex, consumer_id)`
  — clients dedup against their own local fuzzy model.
- No direct external network exposure — the default bind address is
  `127.0.0.1`; production deployments should use a TLS-terminating reverse
  proxy.

See [CLIENT_INTEGRATION.md](CLIENT_INTEGRATION.md) for how a client consumes all
this, and [API.md](API.md) for the endpoint reference.
