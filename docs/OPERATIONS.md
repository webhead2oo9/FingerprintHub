# FingerprintHub — Operations

This host is production. FingerprintHub runs as its own systemd service against
the **native** `postgresql.service` (its own `fingerprinthub` role + database),
listening on `127.0.0.1:58751` only.

## Runtime layout

| Thing | Value |
|-------|-------|
| Unit | `FingerprintHub.service` (User=`webhead`, `Restart=on-failure`, enabled) |
| Working dir | `/home/webhead/VRDiscord/FingerprintHub` |
| Exec | `venv/bin/python main.py` |
| Bind | `127.0.0.1:58751` |
| DB | native Postgres, role+db `fingerprinthub` (`127.0.0.1:5432/fingerprinthub`) |
| Secrets | `.env` (chmod 600, gitignored) |

Startup **requires** the DB to be at Alembic head — it validates, it does not
auto-migrate.

## Common commands

```bash
systemctl is-active FingerprintHub.service          # status (no sudo needed)
systemctl is-enabled FingerprintHub.service
sudo systemctl restart FingerprintHub.service       # after code/.env changes
sudo journalctl -u FingerprintHub.service -n 100 --no-pager
curl -s http://127.0.0.1:58751/v1/health            # {"status":"ok","db":true}
```

Note: on this host, passwordless sudo covers `Nexarion.service`/`Modmail.service`
etc. but NOT `FingerprintHub.service` — restarting the hub prompts for a
password. Status queries (`is-active`) don't need sudo.

## First-time / rebuild deploy

```bash
cd /home/webhead/VRDiscord/FingerprintHub
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# 1. role + database on native Postgres (interactive sudo)
sudo -u postgres psql -c "CREATE ROLE fingerprinthub LOGIN PASSWORD '<strong-pw>';"
sudo -u postgres createdb -O fingerprinthub fingerprinthub

# 2. .env  (generate an INDEPENDENT field key; do not reuse Nexarion's)
venv/bin/python tools/generate_field_key.py --key-id v1     # -> paste into .env
#   FINGERPRINTHUB_DATABASE_URL=postgresql://fingerprinthub:<pw>@127.0.0.1:5432/fingerprinthub
#   FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS=v1:...
#   FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID=v1
#   FINGERPRINTHUB_HOST=127.0.0.1 / FINGERPRINTHUB_PORT=58751
chmod 600 .env

# 3. schema
export FINGERPRINTHUB_DATABASE_URL=...   # same as .env
venv/bin/alembic upgrade head

# 4. systemd unit -> /etc/systemd/system/FingerprintHub.service  (see repo template)
sudo systemctl daemon-reload && sudo systemctl enable --now FingerprintHub.service
```

## Managing consumers

Each client bot is a `consumers` row with one API key (only its hash is stored).

```bash
# mint a consumer + print its key ONCE
venv/bin/python tools/create_consumer.py --name <bot-name> --scopes read,write

# disable a consumer (revoke access) — SQL, no HTTP admin endpoint by design
psql "postgresql://fingerprinthub@127.0.0.1:5432/fingerprinthub" \
  -c "UPDATE consumers SET enabled=FALSE WHERE name='<bot-name>';"

# rotate a key: mint a new consumer (or add a new row) and disable the old one
```

Give the printed `fph_…` key to the client; it goes in the client's own env
(e.g. Nexarion's `NEXARION_FINGERPRINT_HUB_API_KEY`).

## Schema changes

Add a **forward** Alembic revision (`down_revision` = current head); never edit
an applied revision. `venv/bin/alembic upgrade head`, then restart the service.
Migrations use raw SQL (`op.execute`) with `IF NOT EXISTS` / `ON CONFLICT DO
NOTHING`, matching Nexarion's conventions.

## Backups / retention

Deletes and auto-hides are **soft** (`status` = `deleted`/`hidden`) so tombstones
can propagate to clients. Keep tombstones long enough to outlive any client's
sync watermark before any physical purge. Standard Postgres backups of the
`fingerprinthub` database cover everything (encrypted fields stay encrypted at
rest; back up `FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS` separately and durably —
losing it loses `reason`/`source_url`).

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| Boot fails: "schema revision mismatch" | run `alembic upgrade head` against the prod DB |
| Boot fails: DB connect | check `FINGERPRINTHUB_DATABASE_URL`, role password, `postgresql.service` |
| `401` on every request | wrong/absent `X-API-Key`, or consumer `enabled=FALSE` |
| `403` | consumer lacks the required scope for that route |
| `429` | per-consumer rate limit; raise `FINGERPRINTHUB_RATE_LIMIT` if legitimate |
| Client not receiving peer rows | client's compatibility triple must match; check the client's sync watermark and its `image_fingerprint_hub_*` config |

### Client-side gotcha (Nexarion)

Nexarion validates `config.json` against an allowlist. Any new
`moderation_engine.subsystems.content_abuse.*` key (e.g. the
`image_fingerprint_hub_*` keys) MUST also be added to `CONTENT_ABUSE_CONFIG_KEYS`
in `Nexarion/utils/config_validation/moderation_engine.py`, or the bot
crash-loops on "not a supported v1 configuration key". This is a Nexarion
concern, noted here because it bit the initial rollout.
