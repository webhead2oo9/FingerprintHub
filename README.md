# FingerprintHub

Standalone shared scam-image **perceptual-hash (pHash) fingerprint** service.
Multiple Discord bots/servers contribute fingerprints of known-bad images and
sync each other's, so a scam image caught on one server protects the others.

The hub only stores and serves `phash_hex` text. It never decodes images or
computes hashes — clients compute pHashes and match them **locally**
(in-memory Hamming distance). The hub is a sync source + contribution target,
never on a consumer's moderation hot path.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data model, the sync
  protocol (`sync_seq`, tombstones), trust model.
- [docs/API.md](docs/API.md) — full endpoint reference with request/response
  examples.
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — deploy, systemd, consumer management,
  backups, troubleshooting.
- [docs/CLIENT_INTEGRATION.md](docs/CLIENT_INTEGRATION.md) — how a bot becomes a
  consumer (the cache model, the four flows, backfill).
- `CLAUDE.md` — guidance for AI agents working in this repo.

## Design notes

- **Multi-tenant auth**: each consumer (bot) holds one API key (`fph_…`) sent
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
- **Trust model**: a consumer may hard-delete only its own rows; it can `flag`
  others'. A row auto-hides once `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD`
  distinct consumers flag it. (Finer trust tuning is deferred until a second
  consumer exists.)
- **Encryption**: `reason` / `source_url` are encrypted at rest (AES-GCM,
  envelope `enc:v1:…`) and redacted from non-owners. Keys are independent from
  Nexarion's.

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

## Production

Runs against the host's **native** `postgresql.service` (its own
`fingerprinthub` role + database), as a systemd unit `FingerprintHub.service`
on `127.0.0.1:58751`. See the deployment checklist in the project plan. Mint a
consumer key with:

```bash
venv/bin/python tools/create_consumer.py --name nexarion --scopes read,write
```
