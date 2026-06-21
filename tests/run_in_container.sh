#!/usr/bin/env bash
# Test runner that runs the hydra:dev container tests one at a time.
set -euo pipefail

echo "=== Build image ==="
docker build -t hydra:dev .

echo "=== Unit tests (data ingest) ==="
# Override ENTRYPOINT so we don't accidentally run `python -m app`.
docker run --rm --entrypoint python \
  -e ADMIN_PASSWORD=x \
  hydra:dev \
  -m unittest discover -s /app/tests -p "test_data.py" -v 2>&1

echo "=== E2E tests (hello + chunk pull) ==="
docker run --rm --entrypoint python \
  -e ADMIN_PASSWORD=x \
  hydra:dev \
  -m unittest discover -s /app/tests -p "test_sync_e2e.py" -v 2>&1

echo "=== Done ==="