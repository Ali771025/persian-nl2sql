#!/bin/sh
set -eu

attempt=1
until alembic upgrade head; do
  if [ "$attempt" -ge 30 ]; then
    echo "Operational database migration failed after $attempt attempts" >&2
    exit 1
  fi
  echo "Operational database migration failed; retrying ($attempt/30)" >&2
  attempt=$((attempt + 1))
  sleep 2
done

exec "$@"
