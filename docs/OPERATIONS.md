# FingerprintHub Operations

This guide describes a conventional production deployment. Adapt paths, users,
service managers, and backup tooling to your environment.

## Deployment model

- Run FingerprintHub under its own unprivileged service account, so nothing
  else on the host shares its credentials.
- Give it a dedicated Postgres role and database rather than a schema inside
  someone else's, so the hub's blast radius stays its own.
- Bind the application to `127.0.0.1` and put a TLS-terminating reverse proxy
  in front of it. The service speaks plain HTTP and knows nothing about
  certificates.
- Keep secrets out of version control. If you use `.env`, restrict it to the
  service account (`chmod 600`).
- Apply migrations before you start a new version of the code. Startup checks
  that the database sits at the repository's Alembic head and refuses to serve
  if it doesn't; it never migrates for you.

## Required environment

```dotenv
FINGERPRINTHUB_DATABASE_URL=postgresql://fingerprinthub:<password>@127.0.0.1:5432/fingerprinthub
FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS=v1:<base64-encoded-32-byte-key>
FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID=v1
FINGERPRINTHUB_HOST=127.0.0.1
FINGERPRINTHUB_PORT=58751
```

Generate a field-encryption key with:

```bash
venv/bin/python tools/generate_field_key.py --key-id v1
```

Keep old field-encryption keys configured for as long as any row still
references them. If you lose every copy of a key, you don't get the `reason`
and `source_url` values it encrypted back.

## Initial deployment

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt

export FINGERPRINTHUB_DATABASE_URL='postgresql://fingerprinthub:<password>@127.0.0.1:5432/fingerprinthub'
venv/bin/alembic upgrade head
venv/bin/python main.py
```

To run it under a service manager, point the unit at the repository as its
working directory, load the environment above, run `venv/bin/python main.py`,
and restart it on failure. This repository doesn't ship a unit file, because
the exact shape of one belongs with your deployment infrastructure rather than
the application.

## Health checks

```bash
curl --fail --silent http://127.0.0.1:58751/v1/health
```

A healthy service answers `200` with `{"status":"ok","db":true}`. If the
database ping fails you get `503` and `{"status":"degraded","db":false}`, so a
load balancer or monitor can pull the instance out on its own. It's the only
route that doesn't need an API key.

## Managing consumers

Each client gets one consumer row holding one API key. The hub stores only the
key's SHA-256 hash, so `create_consumer.py` prints the raw key once and can't
show it again. There's no HTTP admin endpoint for any of this by design;
minting and revoking happen on the host.

```bash
# Mint a consumer and print its key once.
venv/bin/python tools/create_consumer.py \
  --name community-client \
  --scopes read,write

# Revoke a consumer.
psql "$FINGERPRINTHUB_DATABASE_URL" \
  -c "UPDATE consumers SET enabled=FALSE WHERE name='community-client';"
```

Hand the key over through a secret manager or another protected channel, and
have the client keep it in its own environment rather than in committed
configuration. To rotate one, mint a fresh consumer and disable the old row
once the client has cut over; disabling takes effect on the next request, since
auth looks the consumer up per request.

## Schema changes

Create a forward Alembic revision whose `down_revision` references the current
head. Apply it before deploying code that depends on the new schema:

```bash
venv/bin/alembic upgrade head
```

Never edit a migration that has already been applied to a shared or production
database.

## Backups and retention

Back up the Postgres database and the field-encryption keys separately, and
store them in different places. A database dump carries `reason` and
`source_url` still encrypted, so on its own it doesn't restore them; you need
the matching keys too. Backing both up to the same target defeats the point of
encrypting the fields at all.

Deletes and auto-hides are soft: the row's `status` becomes `deleted` or
`hidden` and it takes a fresh `sync_seq`, so the tombstone reaches every
client's feed and they drop the row locally. Before you physically purge tombstones,
make sure they've outlived the longest a client might stay offline. A client
that misses a tombstone never learns to drop the row and keeps acting on a
fingerprint everyone else has retired.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Startup reports a schema revision mismatch | Run `alembic upgrade head` against the configured database. |
| Startup cannot connect to Postgres | Check the DSN, network access, credentials, and database availability. |
| Every request returns `401` | The API key is absent, incorrect, or belongs to a disabled consumer. |
| A route returns `403` | The consumer lacks the required scope. |
| Requests return `429` | The consumer exceeded its configured per-minute limit. |
| A client does not receive peer rows | Check its compatibility filters and persisted sync watermark. |
