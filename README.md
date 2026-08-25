# FingerprintHub

Standalone shared **perceptual-hash (pHash) fingerprint** service for community
safety tools. Multiple clients contribute fingerprints of known-bad images and
sync each other's, so an image identified by one community can protect others.

The hub only stores and serves `phash_hex` text. It never decodes images or
computes hashes — clients compute pHashes and match them **locally**
(in-memory Hamming distance). The hub is a sync source + contribution target,
never on a consumer's moderation hot path.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data model, the sync
  protocol (`sync_seq`, tombstones), trust model.
- [docs/API.md](docs/API.md) — full endpoint reference with request/response
  examples.
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — deployment, consumer management,
  backups, and troubleshooting.
- [docs/CLIENT_INTEGRATION.md](docs/CLIENT_INTEGRATION.md) — how a client becomes a
  consumer (the cache model, the four flows, backfill).
- `AGENTS.md` — guidance for coding agents working in this repository.

## Design notes

- **Multi-tenant auth**: each consumer holds one API key (`fph_…`) sent
  in `X-API-Key`. Only its SHA-256 hash is stored. Scopes: `read`, `write`,
  `admin`.
- **Incremental sync** on a monotonic `sync_seq` cursor. `sync_seq` advances on
  content changes (insert/resurrect, hide, delete) but **not** on stats-only
  writes (hits, sub-threshold flags), so popular fingerprints don't churn sync.
- **Tombstones**: deletes are soft (`status='deleted'`); the sync feed emits
  hidden/deleted rows so clients can remove them locally. The feed excludes the
  requesting consumer's own contributions (they already have them).
- **Compatibility triple**: clients may filter sync by
  `(algorithm, algorithm_version, normalization_version)` so they only ingest
  fingerprints they can actually compare against.
- **Trust model**: a consumer may soft-delete only its own rows; it can `flag`
  others'. A row auto-hides once `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD`
  distinct consumers flag it. (Finer trust tuning is deferred until a second
  consumer exists.)
- **Encryption**: `reason` / `source_url` are encrypted at rest (AES-GCM,
  envelope `enc:v1:…`) and redacted from non-owners.

## API (`/v1`)

| Method | Path | Scope | Notes |
|---|---|---|---|
| GET | `/v1/health` | none | liveness + DB ping |
| GET | `/v1/fingerprints/sync?since=&limit=&algorithm=&algorithm_version=&normalization_version=` | read | incremental pull; returns `{fingerprints, next_since, has_more}` |
| POST | `/v1/fingerprints` | write | contribute; `409 {existing_id}` on duplicate |
| POST | `/v1/fingerprints/{id}/hit` | write | bump hit stats |
| POST | `/v1/fingerprints/{id}/flag` | write | idempotent per consumer; auto-hide at threshold |
| DELETE | `/v1/fingerprints/{id}` | write | owner/admin only (soft delete) |
| GET | `/v1/fingerprints/{id}` | read | detail; secrets gated; hidden→404 for non-owner |
| GET | `/v1/fingerprints` | read | browse |
| GET | `/v1/fingerprints/stats` | read | aggregates |

## Local development

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python tools/generate_field_key.py --key-id v1   # put output in .env
docker compose up -d                                       # dev Postgres on :54330

export FINGERPRINTHUB_DATABASE_URL=postgresql://fingerprinthub:fingerprinthub_dev@localhost:54330/fingerprinthub_dev
venv/bin/alembic upgrade head

# tests (destructive — point ONLY at a disposable DB)
FINGERPRINTHUB_TEST_DATABASE_URL=$FINGERPRINTHUB_DATABASE_URL venv/bin/python -m pytest

venv/bin/python main.py                                    # serve on 127.0.0.1:58751
```

## Deployment

Keep the service bound to `127.0.0.1` and expose it through a TLS-terminating
reverse proxy. Use a dedicated Postgres role and database, keep `.env`
permissions restrictive, and apply migrations before starting the service. See
[docs/OPERATIONS.md](docs/OPERATIONS.md) for a deployment checklist. Mint a
consumer key with:

```bash
venv/bin/python tools/create_consumer.py --name community-client --scopes read,write
```
