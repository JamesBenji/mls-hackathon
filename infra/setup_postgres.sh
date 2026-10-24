#!/usr/bin/env bash
# Local Postgres on the droplet for Guardian (idempotent). Listens on
# localhost only (Ubuntu default). Run as root:
#   DB_PASSWORD=<hex> bash infra/setup_postgres.sh
# DATABASE_URL to use: postgresql+psycopg://guardian:<DB_PASSWORD>@127.0.0.1:5432/guardian
set -euo pipefail

: "${DB_PASSWORD:?DB_PASSWORD is required (letters/digits only)}"
[[ "$DB_PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || { echo "DB_PASSWORD must be alphanumeric" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

if ! command -v psql >/dev/null 2>&1; then
  echo "==> installing postgresql"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq postgresql >/dev/null
fi
systemctl enable --now postgresql >/dev/null

as_pg() { (cd /tmp && sudo -u postgres "$@"); }

if as_pg psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='guardian'" | grep -q 1; then
  as_pg psql -q -c "ALTER ROLE guardian WITH LOGIN PASSWORD '${DB_PASSWORD}'"
else
  as_pg psql -q -c "CREATE ROLE guardian WITH LOGIN PASSWORD '${DB_PASSWORD}'"
fi
if ! as_pg psql -tAc "SELECT 1 FROM pg_database WHERE datname='guardian'" | grep -q 1; then
  as_pg createdb -O guardian guardian
fi
echo "==> postgres ready (db=guardian user=guardian, localhost only)"
