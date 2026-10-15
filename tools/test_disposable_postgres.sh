#!/usr/bin/env bash
# Never reuse an operator's DSN. Requires an explicit private temp root.
set -euo pipefail
: "${TMPDIR:?Set TMPDIR to a private test directory}"
pg_bin="${PG_TEST_BINDIR:-$(pg_config --bindir)}"
pg_test_root=$(mktemp -d "${TMPDIR}/fingerprinthub-pg.XXXXXX")
cleanup() {
    "$pg_bin/pg_ctl" -D "$pg_test_root/data" -m immediate stop >/dev/null 2>&1 || true
    rm -rf -- "$pg_test_root"
}
trap cleanup EXIT
mkdir "$pg_test_root/socket"
"$pg_bin/initdb" -D "$pg_test_root/data" -A trust --no-locale -E UTF8 > "$pg_test_root/init.log"
"$pg_bin/pg_ctl" -D "$pg_test_root/data" -l "$pg_test_root/server.log" \
    -o "-c listen_addresses='' -c unix_socket_directories='$pg_test_root/socket' -p 55432 -c max_connections=12" start
"$pg_bin/createdb" -h "$pg_test_root/socket" -p 55432 fingerprinthub_test
unset DATABASE_URL
export FINGERPRINTHUB_TEST_DATABASE_URL="postgresql:///fingerprinthub_test?host=$pg_test_root/socket&port=55432"
export FINGERPRINTHUB_DATABASE_URL="$FINGERPRINTHUB_TEST_DATABASE_URL"
export FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS="v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
export FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID=v1
python -m alembic upgrade head
python -m pytest "$@"
