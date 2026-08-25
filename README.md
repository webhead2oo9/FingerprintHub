# FingerprintHub

Standalone shared perceptual-hash (pHash) fingerprint service for community
safety tools. Multiple clients contribute fingerprints of known-bad images and
sync each other's, so an image identified by one community can protect others.

The hub only stores and serves `phash_hex` text. It never decodes images or
computes hashes; clients compute pHashes and match them locally, in memory, by
Hamming distance. The hub is a sync source and a contribution target, never
part of a consumer's moderation hot path.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data model, the
  sync protocol (`sync_seq`, tombstones), trust model.
- [docs/API.md](docs/API.md): full endpoint reference with request/response
  examples.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): deployment, consumer management,
  backups, and troubleshooting.
- [docs/CLIENT_INTEGRATION.md](docs/CLIENT_INTEGRATION.md): how a client
  becomes a consumer (the cache model, the four flows, backfill).
- [PRIVACY.md](PRIVACY.md): field-level sharing, encryption, retention, and
  participant responsibilities.
- `AGENTS.md`: guidance for coding agents working in this repository.
- `.env.example`: the runtime configuration reference (environment variables
  and their defaults).

## Design notes

Auth is multi-tenant. Each consumer holds one API key (`fph_...`) sent in
`X-API-Key`, and only its SHA-256 hash is stored. Scopes are `read`, `write`,
and `admin`.

Sync is incremental over a monotonic `sync_seq` cursor. `sync_seq` advances on
content changes (insert/resurrect, hide, delete) but not on stats-only writes
such as hits and sub-threshold flags, so popular fingerprints don't churn sync.
Deletes are soft (`status='deleted'`), and the feed emits hidden and deleted
rows as tombstones so clients can remove them locally. A consumer's own
contributions are left out of its feed, since it already has them. Clients may
also filter sync by the compatibility triple
`(algorithm, algorithm_version, normalization_version)` and ingest only
fingerprints they can actually compare against.

A consumer may soft-delete only its own rows. Against anyone else's, the lever
is `flag`: a row auto-hides once `FINGERPRINTHUB_AUTO_HIDE_FLAG_THRESHOLD`
distinct consumers have flagged it. Deployments should set that threshold for
their participant count and trust model.

Peer sync is deliberately minimal: fingerprint identity, compatibility,
category/action, and status only. Tenant attribution, community identifiers,
timestamps, provenance, automation flags, free text, and activity statistics
remain owner/admin-only. Community identifiers, reasons, source URLs, hit
locations, and flag reasons are encrypted at rest with AES-GCM.

## API (`/v1`)

Request fields, query parameters, and response shapes are in
[docs/API.md](docs/API.md).

- `GET /v1/health` (no auth): liveness plus a DB ping.
- `GET /v1/fingerprints/sync` (`read`): incremental pull from a `since` cursor.
- `POST /v1/fingerprints` (`write`): contribute; `409` with `existing_id` on
  duplicate.
- `POST /v1/fingerprints/{id}/hit` (`write`): bump hit stats.
- `POST /v1/fingerprints/{id}/flag` (`write`): idempotent per consumer;
  auto-hide at the threshold.
- `DELETE /v1/fingerprints/{id}` (`write`): soft delete, owner or admin only.
- `GET /v1/fingerprints/{id}` (`read`): owner/admin detail; peers receive the
  minimal shared record. A hidden row is a 404 for non-owners.
- `GET /v1/fingerprints` (`read`): browse your own rows; admins may browse all.
- `GET /v1/fingerprints/stats` (`admin`): deployment-wide aggregates.

## Security reporting

Please report suspected vulnerabilities privately through
[GitHub Security Advisories](https://github.com/webhead2oo9/FingerprintHub/security/advisories/new).
Do not put credentials, private community data, or exploit details in a public
issue. The latest `master` is the supported version; no response-time SLA is
promised.

## Local development

Python 3.12 through 3.14 is supported. Linux is the recommended deployment
platform; 64-bit Windows and macOS are suitable for local development. The
pinned `cryptography` release no longer publishes Intel macOS wheels, so x86-64
macOS requires the Rust compiler and OpenSSL development files needed to build
it from source.

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python tools/generate_field_key.py --key-id v1   # put output in .env
docker compose up -d                                       # dev Postgres on :54330

export FINGERPRINTHUB_DATABASE_URL=postgresql://fingerprinthub:fingerprinthub_dev@127.0.0.1:54330/fingerprinthub_dev
venv/bin/alembic upgrade head

# tests (destructive: point only at a disposable DB)
FINGERPRINTHUB_TEST_DATABASE_URL=$FINGERPRINTHUB_DATABASE_URL venv/bin/python -m pytest

venv/bin/python main.py                                    # serve on 127.0.0.1:58751
```

## Deployment

Keep the service bound to `127.0.0.1` and expose it through a TLS-terminating
reverse proxy. Use a dedicated Postgres role and database, keep `.env`
permissions restrictive, and apply migrations before starting the service.
[docs/OPERATIONS.md](docs/OPERATIONS.md) has a deployment checklist. Mint a
consumer key with:

```bash
venv/bin/python tools/create_consumer.py --name community-client --scopes read,write
```
