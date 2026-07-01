# CLAUDE.md — FingerprintHub

Standalone shared scam-image pHash fingerprint service (aiohttp + psycopg +
Alembic, Python 3.13+). Sibling to Nexarion under `/home/webhead/VRDiscord/`.
First consumer is Nexarion; designed to be consumer-agnostic.

## Production discipline (this host is production)

- Runs against the host's **native `postgresql.service`** with its own
  `fingerprinthub` role + database — NOT the `docker-compose.yml` in this repo
  (that's local-dev only, on port 54330). Production DSN points at
  `127.0.0.1:5432/fingerprinthub`.
- Runs as systemd unit `FingerprintHub.service` on `127.0.0.1:58751`. Do not
  create/enable/start/restart the unit or touch the production DB without
  explicit approval; creating the unit, role, and DB needs interactive sudo.
- Startup requires the DB to be at Alembic head (it validates, does not
  auto-migrate). Add forward migrations; never edit `0001_init.py` once applied.
- Secrets live in `.env` only (`FINGERPRINTHUB_DATABASE_URL`,
  `FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS`, …), never committed. Encryption keys
  are independent from Nexarion's.

## Architecture

- `main.py` — entrypoint (validate Postgres at head → pool → `create_app` →
  `web.run_app`), modeled on Nexarion's `api/deals_ingest_server.py`.
- `api/app.py` — app factory, routes, error + auth middleware.
- `api/auth.py` — per-consumer API-key auth (`X-API-Key` → sha256 lookup),
  per-route scope checks (`require_scope`), in-memory rate limiting.
- `api/fingerprints.py` — HTTP handlers; presentation/redaction.
- `api/fingerprint_store.py` — DB CRUD. Invariants: `sync_seq` advances only on
  content changes (NOT stats); deletes are soft tombstones.
- `api/consumers_store.py` — consumer + API-key primitives.
- `utils/` — `postgres_utils.py`, `field_encryption.py`, `async_utils.py`,
  `config_access.py` ported from Nexarion (env-var names re-prefixed
  `FINGERPRINTHUB_`); `time_utils.py` is hub-local.
- `tools/create_consumer.py`, `tools/generate_field_key.py` — operator CLIs.

The hub never decodes images or computes pHashes — clients do that and send
`phash_hex`. Keep matching/Hamming logic out of this service.

## Commands

```bash
venv/bin/python main.py
venv/bin/alembic upgrade head
FINGERPRINTHUB_TEST_DATABASE_URL=<disposable-db> venv/bin/python -m pytest
```

Conventional Commit prefixes, imperative mood.
