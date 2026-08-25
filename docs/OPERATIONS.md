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
- Expect the per-consumer rate limit to be per process. It lives in memory in
  the serving process, isn't shared across workers or replicas, and resets on
  restart, so N processes let a consumer through roughly N times the limit.
- Keep secrets out of version control. If you use `.env`, restrict it to the
  service account (`chmod 600`).
- Apply migrations before you start a new version of the code. Startup checks
  that the database sits at the repository's Alembic head and refuses to serve
  if it doesn't; it never migrates for you.

## Environment

Startup requires only `FINGERPRINTHUB_DATABASE_URL`. `FINGERPRINTHUB_HOST` and
`FINGERPRINTHUB_PORT` are optional bind overrides, shown below at their
defaults, and `.env.example` lists the remaining knobs.

The field-encryption keys protect community identifiers, source metadata, and
flag reasons. They are read lazily on the first encrypted read or write. A
deployment missing them starts and reports healthy, then fails those requests,
so set them before migrations or traffic rather than relying on startup to
catch it.

```dotenv
FINGERPRINTHUB_DATABASE_URL=postgresql://fingerprinthub:<password>@127.0.0.1:5432/fingerprinthub
FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS=v1:<base64-encoded-32-byte-key>
FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID=v1

# Optional; defaults shown.
FINGERPRINTHUB_HOST=127.0.0.1
FINGERPRINTHUB_PORT=58751
```

Generate a field-encryption key with:

```bash
venv/bin/python tools/generate_field_key.py --key-id v1
```

Keep old field-encryption keys configured for as long as any row still
references them. `FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS` takes a comma-separated
list of `key_id:base64_key` entries, so a rotation reads
`v1:<old-base64>,v2:<new-base64>` with
`FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID=v2`. New writes then use `v2`
while `v1` stays available to decrypt existing rows. If you lose every copy of
a key, you don't get the `reason` and `source_url` values it encrypted back.

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

The privacy-hardening release is a coordinated, breaking pre-1.0 transition:
peer sync no longer includes attribution/activity metadata, normal browse is
owner-only, and deployment-wide stats require `admin`. Stop the old service,
take a verified backup, update consumers that depended on those legacy fields,
and run `alembic upgrade head` while writers are quiesced before starting the
new code. Migration `0002_privacy_hardening` also takes exclusive table locks as
a fail-safe and encrypts legacy community-linked metadata. Its downgrade
restores the prior plaintext representation for rollback compatibility.

## Backups and retention

Back up the Postgres database and field-encryption keys separately, with
independent access controls. Community identifiers, source metadata, hit
locations, and flag reasons remain encrypted in a dump, but hashes, tenant ids,
timestamps, distances, aggregate counters, and statuses do not. Treat every
dump as sensitive. Apply the same deletion schedule to backups or document a
shorter fixed backup lifecycle; restoring an old dump temporarily restores the
data it contains, so run retention immediately after a restore.

The repository policy defaults are:

- delete per-hit audit rows after 90 days;
- clear flag free text after 90 days while retaining the flag for trust counts;
- clear source community, reason, and URL from deleted tombstones after 30 days;
- physically delete tombstones after 180 days.

The 180-day tombstone window must be at least as long as the longest supported
offline-client interval. A client that misses a tombstone can keep enforcing a
fingerprint everyone else retired. Change the window only with participant
agreement.

Preview the exact candidate counts first, then apply:

```bash
venv/bin/python tools/purge_retention.py
venv/bin/python tools/purge_retention.py --apply
```

The command accepts `--hit-days`, `--flag-reason-days`,
`--tombstone-metadata-days`, and `--tombstone-days`. Preview executes the same
transaction and rolls it back; `--apply` commits it. Schedule the apply command
at least daily and monitor its JSON result. Disabled consumer rows are retained
for referential and audit integrity; revoke them with `enabled=FALSE`, then
remove or anonymize them only as part of an explicit participant offboarding
procedure after their fingerprints and audit references have aged out.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Startup reports a schema revision mismatch | Run `alembic upgrade head` against the configured database. |
| Startup cannot connect to Postgres | Check the DSN, network access, credentials, and database availability. |
| Every request returns `401` | The API key is absent, incorrect, or belongs to a disabled consumer. |
| A route returns `403` | The consumer lacks the required scope. |
| Requests return `429` | The consumer exceeded its configured per-minute limit. |
| A client does not receive peer rows | Check its compatibility filters and persisted sync watermark. |
