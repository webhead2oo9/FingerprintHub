# Repository guidance

FingerprintHub is a standalone shared image-fingerprint service built with
aiohttp, psycopg, and Alembic on Python 3.13 or newer.

## Architecture

- `main.py` validates the database schema, creates the connection pool, and
  starts the HTTP application.
- `api/app.py` defines middleware and routes. Register static routes such as
  `/sync` and `/stats` before the dynamic `/{id}` route.
- `api/auth.py` handles API-key authentication, independent scopes, and
  per-consumer rate limiting.
- `api/fingerprints.py` validates requests and presents/redacts responses.
- `api/fingerprint_store.py` contains blocking database operations. Its main
  invariants are that `sync_seq` advances only for content changes and deletes
  remain as tombstones.
- `api/consumers_store.py` owns consumer and API-key persistence.
- `utils/` contains shared configuration, encryption, asynchronous offloading,
  time, and Postgres helpers.
- `tools/` contains operator CLIs for creating consumers and encryption keys.

The hub stores pHash text but does not decode images, calculate hashes, or
perform Hamming-distance matching. Clients own those responsibilities.

Database access uses synchronous psycopg calls. Handler database work must run
through `run_blocking_io(...)` so it does not block the aiohttp event loop.

## Development

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
docker compose up -d
venv/bin/alembic upgrade head
venv/bin/python main.py
```

The test suite truncates its configured database. Run it only against a
disposable database that has already been migrated to Alembic head:

```bash
FINGERPRINTHUB_TEST_DATABASE_URL=<disposable-dsn> venv/bin/python -m pytest
```

Add forward Alembic revisions for schema changes. Do not edit a migration that
has already been applied to a shared or production database.
