#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_PORT="${PG_PORT:-5434}"
REDIS_PORT="${REDIS_PORT:-6381}"
DB_NAME="${POSTGRES_DB:-oqim_business}"
DB_USER="${POSTGRES_USER:-postgres}"

PGDATA="${PGDATA:-$ROOT/.local/infra/postgres}"
REDIS_DIR="${REDIS_DIR:-$ROOT/.local/infra/redis}"
LOG_DIR="$ROOT/.dev-logs"

mkdir -p "$PGDATA" "$REDIS_DIR" "$LOG_DIR"

export PATH="/opt/homebrew/opt/postgresql@15/bin:/opt/homebrew/opt/redis/bin:/opt/homebrew/bin:$PATH"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing $1. Install with: brew install postgresql@15 redis"
    exit 1
  fi
}

need initdb
need pg_ctl
need pg_isready
need createdb
need psql
need redis-server

if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
  initdb -D "$PGDATA" -U "$DB_USER" --auth=trust --encoding=UTF8 --locale=C
fi

pg_ctl -D "$PGDATA" -o "-p $PG_PORT -k /tmp" -l "$LOG_DIR/postgres.log" start >/dev/null 2>&1 || true

for _ in $(seq 1 30); do
  if pg_isready -h localhost -p "$PG_PORT" -U "$DB_USER" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! pg_isready -h localhost -p "$PG_PORT" -U "$DB_USER" >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready on port $PG_PORT"
  tail -80 "$LOG_DIR/postgres.log" || true
  exit 1
fi

createdb -h localhost -p "$PG_PORT" -U "$DB_USER" "$DB_NAME" >/dev/null 2>&1 || true
psql -h localhost -p "$PG_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

stop_postgres() {
  pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
}
trap stop_postgres EXIT INT TERM

echo "Local PostgreSQL ready on $PG_PORT ($DB_NAME)"
echo "Local Redis starting on $REDIS_PORT"

redis-server \
  --port "$REDIS_PORT" \
  --dir "$REDIS_DIR" \
  --dbfilename dump.rdb \
  --appendonly no \
  --save "" \
  --protected-mode no &

wait "$!"
