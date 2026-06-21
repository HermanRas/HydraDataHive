# HydraDataHive

A lightweight, containerized **data-replication service** built around three ideas:
**neighbors**, **data**, and **seeding** — packaged as a single Flask app that
runs in Docker.

* **Neighbors** — every node is identical. Each sends a signed `hello` to its
  peers, learns about the rest of the mesh, and decides who to sync with.
* **Data** — files dropped into `data/input/` are SHA-256'd, signed with the
  node's Ed25519 key, split into 64 MB base64 chunks, and stored under
  `data/datastore/<file_id>/`. Metadata lives in a SQLite WAL DB.
* **Seeding** — when a node joins (or is approved) it pulls the file index,
  fetches manifests + chunks, verifies signatures, and replicates them.
  All audit events are chained with SHA-256 so tampering is detectable.

> Full plan, schema, and design rationale live in [PLAN.md](./PLAN.md).

---

## Quick start (single node)

```bash
cp .env.example .env       # set ADMIN_PASSWORD at minimum
docker compose up --build
# Open http://localhost:8080  (login = ADMIN_PASSWORD)
```

## Dev cluster (3 nodes in a chain)

```bash
docker compose -f docker-compose.dev.yml up --build
# Open http://localhost:8081 (node1), :8082 (node2), :8083 (node3)
# Password: devpass
```

The dev compose file gives you:

```
node1 (MASTER) ──hello──▶ node2 ──hello──▶ node3
  ▲                          │                │
  └──── master response ─────┘──── hello ────┘
```

Workflow:
1. Open **node1** → **Neighbors** → Approve node2.
2. Open **node2** → **Neighbors** → Approve node1 (upstream) and node3 (downstream).
3. Open **node3** → **Neighbors** → Approve node2.
4. Drop a file into the `node1_data` input folder (or via `docker exec
   hydra-node1 bash -c 'echo hi > /data/input/hello.txt'`).
5. Within ~5 minutes the file shows up on node2 and node3.
6. Inspect **Audit** on any node — the hash chain should verify.

---

## Environment variables

| Var               | Default     | Purpose                                                              |
| ----------------- | ----------- | -------------------------------------------------------------------- |
| `NODE_NAME`       | hostname    | Friendly name broadcast in hellos.                                   |
| `HTTP_PORT`       | `8080`      | Flask listen port.                                                   |
| `MASTER`          | `TRUE`      | Seed `welcome.md` on boot; accept inbound hellos.                    |
| `NEIGHBORS`       | `MANUAL`    | `MANUAL` = admin approves; `AUTO` = approve on first hello.         |
| `ADMIN_PASSWORD`  | (required)  | Web UI password.                                                     |
| `DATA_DIR`        | `/data`     | Root for `input/`, `datastore/`, `out/`.                             |
| `KEYS_DIR`        | `/keys`     | Where the Ed25519 keypair lives.                                     |
| `SEED_PEERS`      | (empty)     | Comma-separated `host:port` used to bootstrap first hellos.          |
| `MASTER_PEERS`    | (empty)     | Comma-separated upstream master(s) this node reports to.             |
| `DELETE_LOCAL`    | `FALSE`     | Propagate file deletions to/from neighbors.                          |
| `MAX_FILE_SIZE_MB`| `2048`      | Max file size; chunk size stays 64 MB, count adjusts automatically.  |

---

## CLI (`hydra-cli`)

Inside a running container:

```bash
docker exec -it hydra-node1 python cli.py status
docker exec -it hydra-node1 python cli.py show-identity
docker exec -it hydra-node1 python cli.py approve 10.0.0.5
docker exec -it hydra-node1 python cli.py reset-hello 10.0.0.5
docker exec -it hydra-node1 python cli.py list-files
docker exec -it hydra-node1 python cli.py sync-now --peer node1:8080
docker exec -it hydra-node1 python cli.py verify-audit
```

---

## HTTP API (open, LAN-only)

All paths under `/api/v1/`.

| Method | Path                            | Purpose                                  |
| ------ | ------------------------------- | ---------------------------------------- |
| POST   | `/hello`                        | Handshake                                |
| GET    | `/index?since=ISO`              | File index since timestamp               |
| GET    | `/files/<id>/manifest`          | Chunk list + per-chunk SHA-256           |
| GET    | `/files/<id>/chunk/<idx>`       | One base64 chunk                         |
| GET    | `/files/<id>/download`          | Reassembled file download                |
| GET    | `/neighbors`                    | Current neighbor list                    |
| GET    | `/audit?since=ISO`              | Audit log tail                           |
| GET    | `/audit/verify`                 | Recompute + verify the hash chain        |
| GET    | `/identity`                     | This node's pubkey + name                |

---

## Tests

Tests run **inside the container** so we don't pollute the host Python env.

```bash
# Build the image once
docker build -t hydra:dev .

# Unit tests for the data ingest service
docker run --rm hydra:dev python -m unittest tests.test_data -v

# End-to-end hello + chunk pull across two in-process nodes
docker run --rm hydra:dev python -m unittest tests.test_sync_e2e -v
```

---

## File layout

```
hydra/
├── app/
│   ├── __init__.py            # Flask app factory
│   ├── __main__.py            # `python -m app` entrypoint
│   ├── config.py              # Env-var loader
│   ├── crypto.py              # Ed25519 keypair gen / sign / verify
│   ├── db.py                  # SQLite schema + helpers (WAL mode)
│   ├── auth.py                # Web UI login (env password → session)
│   ├── scheduler.py           # APScheduler jobs (5-min ticks)
│   ├── routes/
│   │   ├── api.py             # /api/v1/* JSON endpoints
│   │   └── web.py             # Jinja2 pages
│   ├── services/
│   │   ├── neighbor.py        # Hello handshake, approval, mesh discovery
│   │   ├── data.py            # Ingest, chunk, checksum, assemble
│   │   ├── seeding.py         # Seed welcome.md on master boot
│   │   ├── sync.py            # Mesh pull + delta logic
│   │   └── audit.py           # Hash-chained append-only log
│   ├── templates/             # Bootstrap 5 layout
│   └── static/                # hydra.css / hydra.js (UI tweaks)
├── data/
│   ├── input/                 # Drop files here → ingested on tick
│   ├── datastore/<file_id>/   # base64 64MB chunks: 000.b64, 001.b64, …
│   ├── out/                   # Assembled outputs + archived originals
│   └── hydra.db               # SQLite (single file)
├── keys/
│   ├── node.key               # Ed25519 private (0600)
│   └── node.pub               # Ed25519 public (hex)
├── tests/                     # unittest suite, run inside the container
├── cli.py                     # `hydra-cli` command entrypoint
├── Dockerfile
├── docker-compose.yml
├── docker-compose.dev.yml     # 3-node chain for local testing
├── requirements.txt
└── PLAN.md
```

---

## Security notes

* The HTTP API is **open by spec** (LAN cluster trust model). Do not expose
  port 8080 to the public internet without putting a reverse proxy with TLS
  + auth in front.
* `keys/node.key` is created with mode 0600 and stays inside the container;
  back the `keys` volume up if you need disaster recovery.
* `ADMIN_PASSWORD` is required and is the only web-UI credential.

---

## Deferred (post-v1)

* Full Merkle / cross-node consensus for the audit chain.
* TLS / mTLS between nodes.
* Multi-user accounts in the web UI.
* Garbage collection for orphaned chunks.
* Per-neighbor rate limiting and latency-based routing.