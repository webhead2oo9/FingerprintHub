# FingerprintHub Architecture

## Purpose

FingerprintHub is a small multi-tenant service that shares perceptual-hash
(pHash) fingerprints of known-bad images across community-safety clients. When
one client fingerprints an image, every other participating client can
recognize it without repeating the manual review.

The hub knows nothing about images. All it stores and serves is the `phash_hex`
string; decoding, pHash computation, and matching happen in the clients. That
keeps the hub small, and client moderation hot paths never touch the network.
No image bytes ever reach the server.

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
alembic/                     schema history and privacy data migration
tools/
  create_consumer.py         operator CLI: mint a consumer + one-time API key
  generate_field_key.py      operator CLI: generate an AES-256 field-encryption key
  purge_retention.py         preview/apply the documented retention policy
```

The runtime stack is Python 3.12 through 3.14, aiohttp for HTTP, psycopg plus
psycopg-pool for Postgres, Alembic for schema history, and AES-GCM for
application-level encryption of community-linked metadata. The default bind is
localhost-only on port 58751. Startup verifies that the database is reachable
and exactly at the repository's Alembic head; if either check fails it refuses
to serve.

## Request lifecycle

1. `error_middleware` (outermost) wraps everything. A lost Postgres connection
   becomes a `503`, matching `/v1/health`'s degraded signal; anything else
   unhandled becomes a `500`.
2. `auth_middleware` skips `/v1/health`; otherwise hashes `X-API-Key`, looks the
   consumer up by exact hash, checks `enabled`, applies a per-consumer sliding
   rate limit, attaches the consumer to `request['consumer']`, and then
   throttled-updates `last_seen_at_ms`.
3. The handler calls `require_scope(request, ...)` then does its work via
   `run_blocking_io` (psycopg is synchronous; DB work is offloaded to a thread).

## Data model

See `alembic/versions/0001_init.py` for exact DDL.

- `consumers`: one row per client. `api_key_hash` = `sha256(raw_key)` (the raw
  key is never stored). `scopes TEXT[]` is a subset of read, write, and admin.
  `enabled`.
- `fingerprints`: the shared catalog. Key columns:
  - `phash_hex`, plus the compatibility triple `(algorithm, algorithm_version,
    normalization_version)`.
  - `category` is one of scam, nsfw, crypto, phishing, other; `action` is one
    of kick or timeout (a softban-intent hint clients may honor or override
    locally).
  - `consumer_id`, the contributing owner. `UNIQUE (phash_hex, consumer_id)`.
  - `source_guild_id`, `reason`, and `source_url`, encrypted at rest and
    owner/admin-only.
  - `status` is one of active, hidden, deleted. `flag_count`.
  - `sync_seq`, the incremental-sync cursor (see below).
  - `hit_count` and `last_hit_at_ms`, stats that are never synced to peers.
- `fingerprint_hits`: per-hit audit rows (who enforced, encrypted location,
  distance).
- `fingerprint_flags`: one row per (fingerprint, flagging consumer); drives
  auto-hide. Its optional reason is encrypted.

## The sync protocol

Clients pull incrementally from `GET /v1/fingerprints/sync?since=<seq>`:

- Rows are ordered by `sync_seq`, a monotonic value from a dedicated Postgres
  sequence (`fingerprints_sync_seq`). The endpoint returns rows with
  `sync_seq > since`, plus `next_since` (advance the watermark to this only after
  durably applying the page) and `has_more`.
- `sync_seq` advances on content changes only: insert, resurrect, status flips
  (hide/delete). Stats-only writes (hits, sub-threshold flags) leave it alone.
  That is why a popular fingerprint taking hits every minute doesn't churn
  every client's sync feed.
- The feed excludes the requesting consumer's own contributions (`consumer_id
  <> requester`). A client already has its own rows locally; re-ingesting them
  would create duplicates.
- Tombstones: the feed includes `hidden`/`deleted` rows, which carry a fresh
  `sync_seq` from the status flip. Clients delete these locally. Deletes are
  soft (`status='deleted'`) so the tombstone can propagate; the scheduled
  retention policy minimizes its source metadata after 30 days and physically
  purges it after 180 days.
- Compatibility filter: clients pass their `(algorithm, algorithm_version,
  normalization_version)` triple, and the feed returns only matching rows, so a
  client never ingests hashes it cannot Hamming-compare against.
- Peer rows intentionally contain only `id`, `sync_seq`, `phash_hex`, the
  compatibility triple, `category`, `action`, and `status`. Stable tenant or
  community identifiers and activity metadata never cross that boundary.

The cursor is a monotonic sequence rather than a wall-clock timestamp, so sync
is immune to clock skew and same-millisecond ordering bugs, and pages with
strict `>`.

## Trust model (cross-consumer)

The shared dataset is security-relevant, so deletion power is conservative. A
consumer may soft-delete its own contributions and nothing else; the status
change leaves a tombstone. A consumer that disagrees with someone else's row
can only flag it. Once `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD` distinct
consumers flag a row it auto-hides: dropped from browse, and pushed to peers
as a tombstone on the sync feed so they drop their local copies. A consumer
holding `write` and `admin` together can soft-delete anything.

So one client's mistake or bad actor can't silently delete protection for
everyone. Operators are responsible for choosing a threshold and participant
governance model suitable for their deployment.

## Out of scope

- No image handling, pHash computation, or Hamming matching; all of that is
  client-side.
- No cross-consumer near-duplicate dedup beyond exact `(phash_hex,
  consumer_id)`. Clients dedup against their own local fuzzy model.
- No direct external network exposure. The default bind address is `127.0.0.1`;
  production deployments should use a TLS-terminating reverse proxy.

See [CLIENT_INTEGRATION.md](CLIENT_INTEGRATION.md) for how a client consumes all
this, and [API.md](API.md) for the endpoint reference.
