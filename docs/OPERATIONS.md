# FingerprintHub — Operations

This guide describes a conventional production deployment. Adapt paths, users,
service managers, and backup tooling to your environment.

## Deployment model

- Run FingerprintHub as an unprivileged service account.
- Give it a dedicated Postgres role and database.
- Bind the application to `127.0.0.1` and expose it through a
  TLS-terminating reverse proxy.
- Store secrets outside version control. If using `.env`, restrict the file to
  the service account.
- Apply migrations before starting a new application version. Startup validates
  the schema revision but never migrates automatically.

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

Keep old field-encryption keys configured while database rows still reference
them. Losing every copy of a key permanently loses access to the fields it
encrypted.

## Initial deployment

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt

export FINGERPRINTHUB_DATABASE_URL='postgresql://fingerprinthub:<password>@127.0.0.1:5432/fingerprinthub'
venv/bin/alembic upgrade head
venv/bin/python main.py
```

For a managed service, set the repository as the working directory, load the
required environment, run `venv/bin/python main.py`, and enable automatic
restart on failure. The exact unit configuration belongs in deployment
infrastructure rather than this application repository.

## Health checks

```bash
curl --fail --silent http://127.0.0.1:58751/v1/health
```

A healthy service returns `{"status":"ok","db":true}`. A database failure
returns HTTP 503 with a degraded status.

## Managing consumers

Each client is represented by a consumer row with one API key. Only the key's
SHA-256 hash is stored.

```bash
# Mint a consumer and print its key once.
venv/bin/python tools/create_consumer.py \
  --name community-client \
  --scopes read,write

# Revoke a consumer.
psql "$FINGERPRINTHUB_DATABASE_URL" \
  -c "UPDATE consumers SET enabled=FALSE WHERE name='community-client';"
```

Deliver API keys through a secret manager or another protected channel. Store
them only in the client's secret configuration.

## Schema changes

Create a forward Alembic revision whose `down_revision` references the current
head. Apply it before deploying code that depends on the new schema:

```bash
venv/bin/alembic upgrade head
```

Never edit a migration that has already been applied to a shared or production
database.

## Backups and retention

Back up the Postgres database and field-encryption keys separately. Database
backups contain encrypted field values; they are not sufficient without the
corresponding keys.

Deletes and automatic hides are soft so tombstones can propagate to clients.
Keep tombstones longer than the maximum time a client might remain offline
before physically purging them.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Startup reports a schema revision mismatch | Run `alembic upgrade head` against the configured database. |
| Startup cannot connect to Postgres | Check the DSN, network access, credentials, and database availability. |
| Every request returns `401` | The API key is absent, incorrect, or belongs to a disabled consumer. |
| A route returns `403` | The consumer lacks the required scope. |
| Requests return `429` | The consumer exceeded its configured per-minute limit. |
| A client does not receive peer rows | Check its compatibility filters and persisted sync watermark. |
