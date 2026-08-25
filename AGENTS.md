# Repository guidance

FingerprintHub is a standalone shared image-fingerprint service built with
aiohttp, psycopg, and Alembic on Python 3.12 through 3.14.

## Architecture

- `main.py` validates the database schema, creates the connection pool, and
  starts the HTTP application. It validates rather than migrates, so a database
  behind Alembic head stops startup instead of being upgraded silently.
- `api/app.py` defines middleware and routes. Register static routes such as
  `/sync` and `/stats` before the dynamic `/{id}` route, or the dynamic route
  shadows them.
- `api/auth.py` handles API-key authentication, scope checks, and per-consumer
  rate limiting. Scopes are independent flags rather than levels, so holding
  `admin` doesn't imply `read`.
- `api/fingerprints.py` validates requests and presents/redacts responses.
- `api/fingerprint_store.py` contains the blocking database operations, and the
  sync invariants live here. `sync_seq` advances only for content changes, so
  stats-only writes like hits don't churn every client's sync feed. Deletes
  stay as tombstones so clients can remove the row locally, and re-contributing
  a tombstoned pair resurrects it with its flags cleared.
- `api/consumers_store.py` owns consumer and API-key persistence.
- `utils/` contains shared configuration, encryption, blocking-IO offload,
  time, and Postgres helpers.
- `tools/` contains operator CLIs for creating consumers and encryption keys.
  `tools/purge_retention.py` previews retention by default and commits only with
  `--apply`.

The hub stores and serves `phash_hex` text. It never decodes images, computes
hashes, or matches them by Hamming distance; clients own all of that. Keep that
logic out of this service.

psycopg is synchronous, so handler database work is offloaded to a thread
through `run_blocking_io(...)` and never blocks the aiohttp event loop. Store
functions take the pool and stay plain blocking callables.

## Development

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
docker compose up -d
venv/bin/alembic upgrade head
venv/bin/python main.py
```

The test suite truncates its configured database, and it skips itself entirely
when the DSN is unset. Run it only against a disposable database that's already
at Alembic head:

```bash
FINGERPRINTHUB_TEST_DATABASE_URL=<disposable-dsn> venv/bin/python -m pytest
```

Add forward Alembic revisions for schema changes. Do not edit a migration that
has already been applied to a shared or production database, since rewriting
applied history leaves every database that ran it out of step.

Preserve the `sync_seq` and tombstone behavior unless you're deliberately
revising the sync protocol.

Never commit `.env`, database credentials, API keys, or field-encryption keys.
Never add tenant attribution, community identifiers, timestamps, provenance,
or activity statistics to the peer sync shape. The minimal sharing contract is
documented in `PRIVACY.md` and `docs/API.md`.
