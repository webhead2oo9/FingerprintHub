# Incremental sync write ordering

Every fingerprint store writer takes the same transaction-scoped PostgreSQL
advisory lock before taking row locks or allocating `fingerprints_sync_seq`.
This includes contribution/resurrection, auto-hide, deletion, hit accounting and
retention, so their row-lock order remains consistent. Readers do not take the
lock. An allocated sequence cannot become visible after a higher committed
sequence. Rollback and lost connections release the lock; sequence gaps are safe.
The sequence must retain its default `CACHE 1` setting.

New writers must use the same lock before any row locks or sequence allocation.
Direct SQL writers that bypass the store must follow the same protocol. The
migration tool runs with writers stopped and takes exclusive table locks.

## Verification

The race tests pause real PostgreSQL transactions between mutation and commit.
They cover every pair of sync-changing writers, rollback, connection loss,
tombstone delivery and subsequent writes. Run the full suite using a fresh,
socket-only database (PostgreSQL binaries and Python dependencies required):

```bash
TMPDIR=/your/private/test-directory tools/test_disposable_postgres.sh -q
```

The script uses a new cluster, an explicit test DSN, TCP disabled, and removes the
cluster on exit. It does not use an inherited `DATABASE_URL`.

## Rollout and recovery

No new schema migration is required for this ordering change. Drain all old
writers before starting the updated release: mixed versions do not share the
invariant. Verify that the sequence still uses `CACHE 1` during the separately
authorized rollout. Consumers that may already have skipped changes need a full
resynchronization; this fix cannot reconstruct changes purged by retention.

The current canonical baseline also includes privacy migration
`0002_privacy_hardening`, unlike the reviewed deployed source. Review that
existing migration and key configuration before any rollout from that older
release. Do not downgrade encryption or discard keys as part of a code rollback.
Rolling back the ordering change reintroduces the race.
