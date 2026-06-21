# HydraDataHive — Quick-start UX Testing Guide

## 0. Prerequisites

- Docker with `docker compose` v2
- Ports **8081**, **8082**, **8083** free on the host
- Web browser

## 1. Build & boot the 3-node cluster

```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
docker compose -f docker-compose.dev.yml up --build -d
```

Wait ~15 s and confirm health:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

You should see three containers in `(healthy)` status.

## 2. Open the web UIs in your browser

| Node | URL | Role |
|---|---|---|
| node1 | <http://localhost:8081> | Master (seed peer) |
| node2 | <http://localhost:8082> | Mid-tier |
| node3 | <http://localhost:8083> | Leaf |

Login on each with password **`devpass`**.

## 3. Initial UX walk-through (per node)

**Dashboard** — shows recent files, neighbors, latest audit events, and the hash-chain verification status.

**Identity** — shows this node's Ed25519 public key (hex). Copy button in the UI.

**Audit** — DataTable of every action. The `Verify chain` button at the top recomputes every entry's hash from scratch.

## 4. Approve the mesh (most important UX flow)

The cluster starts with all neighbors unapproved (because `NEIGHBORS=MANUAL` in dev compose). Two paths:

**Path A — via the UI** (this is the UX flow to test):

1. On **node1** (`/neighbors`): you'll see `172.18.0.3 (node2)` and `node3` (after mesh discovery, ~30s). Click **Approve** next to each → SweetAlert confirm dialog.
2. On **node2** (`/neighbors`): approve `node1` and `node3`.
3. On **node3** (`/neighbors`): approve `node1` and `node2`.

**Path B — via the CLI** (faster for re-runs):

```bash
# node1 → node2 (use the IP, not the hostname)
curl -c /tmp/c1 -X POST -d "password=devpass" http://localhost:8081/login -o /dev/null
curl -b /tmp/c1 -X POST http://localhost:8081/neighbors/172.18.0.3/approve -o /dev/null

# node2 → node1 + node3
curl -c /tmp/c2 -X POST -d "password=devpass" http://localhost:8082/login -o /dev/null
curl -b /tmp/c2 -X POST http://localhost:8082/neighbors/node1/approve -o /dev/null
curl -b /tmp/c2 -X POST http://localhost:8082/neighbors/172.18.0.4/approve -o /dev/null

# node3 → node2 + node1
curl -c /tmp/c3 -X POST -d "password=devpass" http://localhost:8083/login -o /dev/null
curl -b /tmp/c3 -X POST http://localhost:8083/neighbors/node2/approve -o /dev/null
curl -b /tmp/c3 -X POST http://localhost:8083/neighbors/node1/approve -o /dev/null
```

## 5. Drop a file & watch it replicate

The scheduler ticks every 5 min, but you can **force an immediate ingest + sync** without waiting.

**From the host** (this is the primary input path — the UI intentionally has no upload widget; the input folder is the canonical ingest channel for automation / backup tools):

```bash
echo "hello replicated world from $(date)" > /tmp/myfile.txt
docker cp /tmp/myfile.txt hydra-node1:/data/input/

# Force-ingest on node1 (bypass 5-min scan)
docker exec hydra-node1 python -c "
from app.config import load_settings
from app.db import configure, init_schema
from app.services import data
s = load_settings(); configure(s.db_path); init_schema()
ids = data.scan_input_folder(s, actor=s.node_name)
print('ingested:', ids)
"

# Force sync on node2 and node3 (bypass 5-min pull tick)
docker exec hydra-node2 python cli.py sync-now
docker exec hydra-node3 python cli.py sync-now
```

Now refresh the `/data` page on node2 and node3 — your file should appear with identical SHA-256 on all 3 nodes. Each row has a **Download** button (reassembles chunks on the fly) and a **Delete** button.

## 6. Test the audit chain

On any node's `/audit` page:
- Click **Verify chain** — should return `OK (n entries verified)`.
- Try the CLI:
  ```bash
  docker exec hydra-node1 python cli.py verify-audit
  ```
  Expected output: `{"ok": true, "checked": N, "bad_ids": []}`.

## 7. Test file deletion cascade (with `DELETE_LOCAL=TRUE`)

The dev compose ships with `DELETE_LOCAL=FALSE` (safe default — peer keeps its copy but logs a `file.delete.skip` audit event).

**To test the cascading-delete UX path:**

1. Edit [docker-compose.dev.yml](docker-compose.dev.yml) and change all three `DELETE_LOCAL: "FALSE"` → `DELETE_LOCAL: "TRUE"`.
2. Restart:
   ```bash
   docker compose -f docker-compose.dev.yml up -d
   ```
3. Drop a new file, let it replicate (steps 5 above).
4. On node1's `/data` page, click the **Delete** button on the file row → SweetAlert confirm → confirm.
5. Wait ~5 min (or force sync on node2 and node3 as in step 5).
6. Refresh `/data` on node2 and node3 — the file should be gone.

**Important convergence note:** In a full 3-way mesh, deletion propagates through the audit-log chain. Within 1–2 sync cycles the cluster converges; with `DELETE_LOCAL=FALSE` peers keep their copies but record `file.delete.skip` audit entries (visible on `/audit`).

## 8. Run the test suite

```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
./tests/run_in_container.sh
```

Expected:
```
=== Unit tests (data ingest) ===
Ran 4 tests in 0.0XXs
OK
=== E2E tests (hello + chunk pull) ===
Ran 1 test in 0.2XXs
OK
```

## 9. Reset to a clean cluster

```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
docker compose -f docker-compose.dev.yml down
docker volume rm hydra_node1_data hydra_node2_data hydra_node3_data \
                 hydra_node1_keys hydra_node2_keys hydra_node3_keys 2>/dev/null
docker compose -f docker-compose.dev.yml up --build -d
```

## 10. UX issues you might notice (worth reporting back)

- **No upload widget** — confirmed-by-design. The `input/` folder is the canonical ingest path. Hydra is a backup / high-availability tool aimed at automation; users who want web uploads can fork and add it to their own build.
- **Dark-mode readability** — Bootstrap's `text-muted` colour (a mid-grey) is hard to read on the dark background. Worth lifting to a lighter shade for the dark theme.
- **Self-IP rows in `/neighbors`** — each node shows its own hostname/IP as `approved=0`. Harmless but visually noisy. (PROGRESS §8 notes this.)
- **Cascade race in full 3-way mesh** — if you delete from node1 and force-sync all peers simultaneously, one peer can briefly re-acquire a copy from a peer that hasn't processed the deletion yet. Converges within 1–2 ticks. (Documented in PROGRESS §6.)
- **No real-time UI updates** — you have to manually refresh. Would benefit from a simple AJAX poll or SSE later.
- **SweetAlert confirmations** for Approve / Reject / Remove / Delete — test these to confirm they fire and look right.

## 11. Useful one-liners

```bash
# Cluster health
docker ps --format "table {{.Names}}\t{{.Status}}" | grep hydra

# Tail logs of a single node
docker logs --tail 50 -f hydra-node2

# List files on a node (CLI)
docker exec hydra-node1 python cli.py list-files

# Show this node's identity
docker exec hydra-node2 python cli.py show-identity

# Force pull from all approved peers (bypass 5-min scheduler tick)
docker exec hydra-node3 python cli.py sync-now

# Approve a discovered neighbor via CLI
docker exec hydra-node1 python cli.py approve 172.18.0.4

# Re-hello a peer after IP/key change
docker exec hydra-node1 python cli.py reset-hello node2
```