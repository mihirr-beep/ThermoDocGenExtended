#!/usr/bin/env bash
# One-shot local setup (macOS / Linux): build, start, and seed.
# Usage:  ./setup.sh
set -euo pipefail

echo "==> Building and starting containers..."
docker compose up -d --build

echo "==> Seeding database (retries until the DB is ready)..."
for i in $(seq 1 20); do
  if docker compose exec -T web python seed.py; then
    echo ""
    echo "==> Done. Open http://localhost:5000  (admin@local.test / Password@123)"
    exit 0
  fi
  sleep 3
done

echo "Seed failed after 20 attempts." >&2
exit 1
