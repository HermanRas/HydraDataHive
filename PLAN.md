# HydraDataHive — Implementation Plan

**Stack:** Python 3.11 · Flask · SQLite · APScheduler · Bootstrap 5 · SweetAlert2 · DataTables · `cryptography` (Ed25519) · Docker

---

## 1. High-Level Architecture

Hydra runs as a single containerized Flask app. Every node is the same image; behavior is driven by env vars:

| Env var          | Default     | Purpose                                                   |
| ---------------- | ----------- | --------------------------------------------------------- |
| `MASTER`         | `TRUE`      | Seed `welcome.md` on boot; respond to neighbor hellos.    |
| `NEIGHBORS`      | `MANUAL`    | `MANUAL` → admin approves in UI. `AUTO` → approve on hello. |
| `ADMIN_PASSWORD` | (required)  | Single password for the web UI (Flask session).           |
| `NODE_NAME`      | hostname    | Friendly identifier broadcast in hellos.                  |
| `HTTP_PORT`      | `8080`      | Port for web UI + neighbor API.                           |
| `DATA_DIR`       | `/data`     | Root for `input/`, `datastore/`, `out/`.                  |
| `KEYS_DIR`       | `/keys`     | Where the Ed25519 keypair lives.                          |
| `SEED_PEERS`     | (optional)  | Comma-separated `host:port` list to bootstrap hellos.     |
| `MASTER_PEERS`   | (empty)     | Comma-separated upstream `host:port` list this node reports to. |
| `DELETE_LOCAL`   | `FALSE`     | Propagate file deletions to/from neighbors.               |
| `MAX_FILE_SIZE_MB` | `2048`   | Max file size; chunk size fixed at 64 MB, count adjusts.  |

A node is always the same binary — `MASTER=TRUE` only flips two behaviors (seed welcome file on boot, accept inbound hellos).

---

## 2. Directory Layout

````
hydra/
├── app/
│   ├── __init__.py            # Flask app factory
│   ├── config.py              # Env-var loader
│   ├── crypto.py              # Ed25519 keypair gen / sign / verify
│   ├── db.py                  # SQLite schema + helpers (WAL mode)
│   ├── auth.py                # Web UI login (env password → session)
│   ├── routes/
│   │   ├── web.py             # Jinja2 pages
│   │   └── api.py             # /api/v1/* JSON endpoints
│   ├── services/
│   │   ├── neighbor.py        # Hello handshake, approval, discovery
│   │   ├── data.py            # Ingest, chunk, checksum, assemble
│   │   ├── seeding.py         # Seed welcome.md on master boot
│   │   ├── sync.py            # Mesh pull + delta logic
│   │   └── audit.py           # Append-only log (hash-chained)
│   ├── scheduler.py           # APScheduler jobs (5-min ticks)
│   ├── templates/             # Bootstrap 5 layout
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── data.html
│   │   ├── neighbors.html
│   │   ├── audit.html
│   │   └── login.html
│   └── static/                # bootstrap, sweetalert2, datatables
├── data/
│   ├── input/                 # Drop files here → ingested on tick
│   ├── datastore/<file_id>/   # base64 64MB chunks: 000.b64, 001.b64, …
│   ├── out/                   # Assembled output (on-demand exports)
│   └── hydra.db               # SQLite (single file)
├── keys/
│   ├── node.key               # Ed25519 private (0600)
│   └── node.pub               # Ed25519 public
├── cli.py                     # `hydra-cli` command entrypoint
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── PLAN.md
````

---

## 3. SQLite Schema

````sql
CREATE TABLE files (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  name            TEXT NOT NULL,
  extension       TEXT,
  mime_type       TEXT,
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL UNIQUE,
  chunk_count     INTEGER NOT NULL,
  uploaded_by     TEXT NOT NULL,        -- NODE_NAME of origin
  signer_pubkey   TEXT NOT NULL,        -- hex Ed25519 pubkey
  signature       TEXT NOT NULL,        -- hex sig over sha256
  created_at      TEXT NOT NULL,        -- ISO8601
  updated_at      TEXT NOT NULL
);
CREATE INDEX idx_files_updated ON files(updated_at);

CREATE TABLE chunks (
  file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  chunk_index     INTEGER NOT NULL,
  path            TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  PRIMARY KEY (file_id, chunk_index)
);

CREATE TABLE neighbors (
  ip              TEXT PRIMARY KEY,
  port            INTEGER NOT NULL DEFAULT 8080,
  node_name       TEXT,
  public_key      TEXT,                 -- hex Ed25519
  approved        INTEGER NOT NULL DEFAULT 0,
  first_seen      TEXT NOT NULL,
  last_online     TEXT,
  latency_ms      INTEGER,              -- last measured RTT (info only)
  hello_sent      INTEGER NOT NULL DEFAULT 0, -- 0=pending, 1=done, -1=auto-paused after 3 failures
  hello_failures  INTEGER NOT NULL DEFAULT 0  -- consecutive hello failure counter
);
CREATE INDEX idx_neighbors_approved ON neighbors(approved);
CREATE INDEX idx_neighbors_hello_pending ON neighbors(hello_sent) WHERE hello_sent = 0;

CREATE TABLE audit_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              TEXT NOT NULL,
  actor           TEXT NOT NULL,        -- 'admin', NODE_NAME, or 'system'
  action          TEXT NOT NULL,        -- 'file.add', 'neighbor.approve', 'sync.pull', …
  target          TEXT,                 -- file id, neighbor ip, etc.
  details         TEXT,                 -- JSON blob
  prev_hash       TEXT,                 -- hash-chain link
  entry_hash      TEXT NOT NULL         -- sha256(prev_hash || row)
);
CREATE INDEX idx_audit_ts ON audit_log(ts);

CREATE TABLE sync_state (
  neighbor_ip     TEXT NOT NULL,
  last_pulled_at  TEXT NOT NULL,
  last_index_ts   TEXT NOT NULL,
  PRIMARY KEY (neighbor_ip)
);
````

> Hash-chained audit log gives v1 the tamper-evidence properties of a blockchain-lite without a real distributed ledger. Full blockchain is deferred.

---

## 4. Component Design

### 4.1 Crypto (`app/crypto.py`)
- Generate Ed25519 keypair on first boot if `keys/node.key` missing.
- `sign_file(path)` → hex signature of `sha256(file)`.
- `verify_file(path, sig, pubkey_hex)` → bool.
- Public key is broadcast in hellos; stored per `files` row.

### 4.2 Data ingest (`app/services/data.py`)
On the 5-min scheduler tick:
1. Scan `data/input/`.
2. For each new file: compute SHA-256, split into 64 MB binary chunks → base64-encode → write to `datastore/<id>/NNN.b64`.
3. Insert `files` + `chunks` rows signed by this node's key.
4. Audit entry `file.add`.
5. Move original out of `input/` (to `out/<ts>_<filename>`).

On pull from neighbor:
1. Download chunk manifest; verify each chunk's SHA-256.
2. Verify file signature against `signer_pubkey`.
3. Persist to local datastore.

### 4.3 Neighbors (`app/services/neighbor.py`)

**Hello message** (POST `/api/v1/hello`):
````json
{
  "node_name": "node-02",
  "public_key": "a3f2…",
  "port": 8080,
  "known_peers": ["10.0.0.5:8080", "10.0.0.6:8080"]
}
````

**Hello response** (master):
````json
{
  "approved": true,
  "neighbors": [
    {"ip": "10.0.0.4", "port": 8080, "approved": true,  "public_key": "…"},
    {"ip": "10.0.0.5", "port": 8080, "approved": false, "public_key": "…"}
  ],
  "files_index": [
    {"id": 1, "sha256": "…", "size": 12345, "updated_at": "…"}
  ],
  "audit_tail": [ … last N entries … ]
}
````

Rules:
- New neighbor → row inserted with `approved=0` (MANUAL) or `approved=1` (AUTO). Audit entry either way.
- Master always returns the full list (approved or not), so newly approved nodes can immediately mesh with existing peers.
- Discovery: a neighbor that learns about a new peer from its master's hello sends its own hello to that peer, attempting a direct connection.

### 4.3.1 Hello-sent tracking (`hello_sent`)

Each `neighbors` row carries a `hello_sent` flag to avoid spamming unreachable peers and to make handshakes retryable.

| State             | `hello_sent` | Meaning                                                                |
| ----------------- | ------------ | ---------------------------------------------------------------------- |
| Newly discovered  | `0`          | Peer IP learned from a hello response or `SEED_PEERS`; no hello sent.  |
| Handshake done    | `1`          | We have successfully POSTed `/hello` and received a response.          |
| Auto-paused       | `-1`         | 3 consecutive failures → scheduler ignores this peer until manual reset. |

Rules:
- When a peer is **discovered** (via master's hello response or `SEED_PEERS`), insert/upsert the row with `hello_sent = 0`.
- The scheduler runs `emit_hello_if_pending` (1 min cadence) which selects all rows where `hello_sent = 0` and POSTs `/hello` to each.
- On a **successful** hello response, set `hello_sent = 1` and `last_online = now()`.
- On **failure** (timeout, connection error, non-2xx): increment a tracked failure count for that peer. After **3 consecutive failures**, set `hello_sent = -1` so the scheduler ignores this peer on subsequent ticks. Manual reset is required to retry.
- A tracked `hello_failures INTEGER NOT NULL DEFAULT 0` counter is incremented on each failure and reset to `0` on success or manual reset.
- If a peer is **manually reset** by an admin (e.g. via UI or `hydra-cli reset-hello <ip>`), set `hello_sent = 0` and `hello_failures = 0` to force a fresh handshake — useful after the peer changed its keypair or IP changed.
- The flag is **not** a measure of approval — a peer can have `hello_sent = 1` and `approved = 0` (handshake done, awaiting approval).

### 4.4 Sync (`app/services/sync.py`)

**Topology:** Master ↔ neighbor is just one relationship. A node pulls from every approved neighbor in the cluster (mesh), and pushes its own uploads to every approved neighbor.

Neighbor-side, every 5 min, per approved peer:
1. `GET /api/v1/index?since=<last_index_ts>` → list of new/updated files.
2. For each file: `GET /api/v1/files/<id>/manifest` → chunks + checksums.
3. `GET /api/v1/files/<id>/chunk/<idx>` → base64 chunk, verify SHA-256.
4. Reassemble locally; verify file signature with signer's pubkey.
5. Insert into `files`/`chunks`; audit entry `sync.pull`.
6. Report any new peers discovered back to master during next hello.

**New-version replacement:** When syncing a file whose name+signer matches an existing local file with an older `updated_at`, delete the old chunks after the new file is fully verified and assembled.

**Deletion cascade (`DELETE_LOCAL`):** A `file.delete` audit event from the owner is treated as authoritative. If `DELETE_LOCAL=TRUE`, remove local copy + chunks on receipt. If `FALSE`, keep the local copy but mark it orphaned in the UI.

**Conflict resolution:** Latest `updated_at` wins. Ties → larger `sha256` wins (deterministic).

### 4.5 Audit log (`app/services/audit.py`)
- Append-only table; each row includes `prev_hash` and `entry_hash = sha256(prev_hash || ts || actor || action || target || details)`.
- Web UI shows the chain with a "Verify chain" button (recomputes hashes).

### 4.6 Scheduler (`app/scheduler.py`)
APScheduler jobs:
| Job                          | Cadence    | Where it runs       |
| ---------------------------- | ---------- | ------------------- |
| `scan_input_folder`          | 5 min      | every node          |
| `pull_from_approved_peers`   | 5 min      | neighbors           |
| `discover_mesh_peers`        | 5 min      | every node          |
| `emit_hello_if_pending`      | 1 min      | every node (resends hello to any `hello_sent = 0` rows)  |
| `prune_audit_log` (optional) | daily      | every node          |

---

## 5. HTTP API

All endpoints under `/api/v1/`. Auth: neighbors authenticate by presenting a valid signed hello (their public key is recorded at hello time, so subsequent requests are just IP-based — open cluster per spec). Admin web UI uses session cookie via env password.

| Method | Path                              | Purpose                                |
| ------ | --------------------------------- | -------------------------------------- |
| POST   | `/hello`                          | Handshake                              |
| GET    | `/index?since=ISO`                | File index since timestamp             |
| GET    | `/files/<id>/manifest`            | Chunk list + checksums                 |
| GET    | `/files/<id>/chunk/<idx>`         | One base64 chunk                       |
| GET    | `/neighbors`                      | Current neighbor list                  |
| GET    | `/audit?since=ISO`                | Audit log tail                         |
| GET    | `/identity`                       | This node's pubkey + name              |

Web (HTML, session-auth):
- `/login`, `/logout`
- `/` dashboard
- `/data` DataTable of files (download, delete, view details)
- `/neighbors` list with Approve / Reject / Remove buttons (SweetAlert confirm)
- `/audit` DataTable of audit log with filter + "Verify chain" button
- `/identity` shows own pubkey, copy button

---

## 6. Web UI

- **Bootstrap 5** (latest) for layout + components.
- **SweetAlert2** for approve/reject/delete confirmations and toasts.
- **DataTables** (server-side pagination optional) on `/data`, `/neighbors`, `/audit`.
- Single `base.html` with sidebar nav; flash messages via SweetAlert toasts.

---

## 7. CLI (`cli.py`)

Thin wrapper calling the same service modules. Subcommands:
- `hydra-cli status`
- `hydra-cli approve <ip>`
- `hydra-cli reject <ip>`
- `hydra-cli reset-hello <ip>` (sets `hello_sent = 0` to force re-handshake)
- `hydra-cli list-files [--since ISO]`
- `hydra-cli show-identity`
- `hydra-cli sync-now [--peer ip:port]`
- `hydra-cli verify-audit`

---

## 8. Docker

`Dockerfile` (multi-stage, slim):
- Base `python:3.11-slim`
- Install `requirements.txt`
- Non-root user, `VOLUME` for `/data` and `/keys`
- `EXPOSE 8080`
- `ENTRYPOINT ["python", "-m", "app"]`

### 8.1 `docker-compose.dev.yml` — 3-node chain for testing

A dev-only compose file with three nodes in a linear chain so you can exercise the full handshake + mesh-discovery flow locally:

```
node1 (MASTER) ──hello──▶ node2 ──hello──▶ node3
  ▲                          │                │
  └────◀──── master response  └──hello───────◀┘ (mesh)
```

Topology rules:
- **node1** is the root master (`MASTER=TRUE`, `NEIGHBORS=MANUAL`, no `MASTER_PEERS`).
- **node2** treats **node1** as its master (`MASTER_PEERS=node1:8080`), and acts as a master for **node3** (`MASTER=TRUE`).
- **node3** treats **node2** as its master (`MASTER_PEERS=node2:8080`).
- All three nodes share the same `hydra_net` bridge network so they can resolve each other by service name.
- Each node exposes port `8080` to the host on a unique host port (`8081`, `8082`, `8083`) so you can hit the web UI directly.
- Each node has its own volume for `/data` and `/keys` so DBs and keypairs stay isolated.

````yaml
# filepath: docker-compose.dev.yml
services:
  node1:
    build: .
    container_name: hydra-node1
    hostname: node1
    environment:
      NODE_NAME: node1
      MASTER: "TRUE"
      NEIGHBORS: MANUAL
      ADMIN_PASSWORD: devpass
      MASTER_PEERS: ""              # root master has no upstream peer
      SEED_PEERS: ""
      DELETE_LOCAL: "FALSE"
      MAX_FILE_SIZE_MB: "2048"
      HTTP_PORT: "8080"
    volumes:
      - node1_data:/data
      - node1_keys:/keys
    ports:
      - "8081:8080"
    networks:
      - hydra_net
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/v1/identity',timeout=2).status==200 else 1)"]
      interval: 10s
      timeout: 3s
      retries: 5

  node2:
    build: .
    container_name: hydra-node2
    hostname: node2
    depends_on:
      node1:
        condition: service_healthy
    environment:
      NODE_NAME: node2
      MASTER: "TRUE"                  # node2 is also a master for node3
      NEIGHBORS: MANUAL
      ADMIN_PASSWORD: devpass
      MASTER_PEERS: "node1:8080"      # node2 treats node1 as ITS master
      SEED_PEERS: "node1:8080"        # bootstrap hello to node1 on boot
      DELETE_LOCAL: "FALSE"
      MAX_FILE_SIZE_MB: "2048"
      HTTP_PORT: "8080"
    volumes:
      - node2_data:/data
      - node2_keys:/keys
    ports:
      - "8082:8080"
    networks:
      - hydra_net

  node3:
    build: .
    container_name: hydra-node3
    hostname: node3
    depends_on:
      node2:
        condition: service_healthy
    environment:
      NODE_NAME: node3
      MASTER: "FALSE"                 # leaf node, no downstream peers
      NEIGHBORS: MANUAL
      ADMIN_PASSWORD: devpass
      MASTER_PEERS: "node2:8080"      # node3 treats node2 as its master
      SEED_PEERS: "node2:8080"
      DELETE_LOCAL: "FALSE"
      MAX_FILE_SIZE_MB: "2048"
      HTTP_PORT: "8080"
    volumes:
      - node3_data:/data
      - node3_keys:/keys
    ports:
      - "8083:8080"
    networks:
      - hydra_net

networks:
  hydra_net:
    driver: bridge

volumes:
  node1_data:
  node1_keys:
  node2_data:
  node2_keys:
  node3_data:
  node3_keys:
````

### 8.2 New env var introduced for compose

| Env var        | Default | Purpose                                                                       |
| -------------- | ------- | ----------------------------------------------------------------------------- |
| `MASTER_PEERS` | (empty) | Comma-separated `host:port` list of upstream master(s) this node reports to.  |

This complements `SEED_PEERS` (used for first hello bootstrap). When `MASTER_PEERS` is non-empty, the node emits periodic hellos back to those masters to report discoveries; `SEED_PEERS` is only consulted until the first hello succeeds.

### 8.3 Dev-test workflow

1. `docker compose -f docker-compose.dev.yml up --build`
2. Open http://localhost:8081 (node1), http://localhost:8082 (node2), http://localhost:8083 (node3). Login with `devpass`.
3. **node1 UI → /neighbors**: node2 appears (pending). Click **Approve** (SweetAlert confirm).
4. **node2 UI → /neighbors**: node1 (now approved upstream), node3 (pending downstream). Approve node3.
5. **node3 UI → /neighbors**: node2 (pending). Approve.
6. After the next 5-min sync tick, node2 should also list node3 as a known peer (mesh discovery via node1's hello response).
7. Drop a file into `node1_data/input/` → within 5 min it appears on node2 and node3 via the mesh pull.
8. Inspect `/audit` on each node — hash chain should verify.

---

## 9. `requirements.txt`

````
Flask==3.0.*
Werkzeug==3.0.*
APScheduler==3.10.*
cryptography==43.*
requests==2.32.*
python-dotenv==1.0.*
````

UI assets served from CDN by default (no npm build step).

---

## 10. Build Order

1. Scaffold repo, `requirements.txt`, `Dockerfile`, `.env.example`.
2. `app/config.py`, `app/db.py`, schema migration runner.
3. `app/crypto.py` + keypair generation on boot.
4. `app/services/data.py` (ingest + chunking) + unit test.
5. Flask app factory + `/api/v1/hello` + `/api/v1/index` + chunk endpoints.
6. `app/services/sync.py` (neighbor pull) + end-to-end test with two containers.
7. `app/services/neighbor.py` (mesh discovery loop).
8. `app/services/audit.py` (hash-chained log).
9. `app/scheduler.py` (5-min ticks).
10. Web UI: login, dashboard, data, neighbors, audit, identity.
11. `cli.py`.
12. `docker-compose.yml` + README + screenshots.

---

## 11. Deferred (Post-v1)

- Full blockchain / Merkle proofs across nodes (v1 ships hash-chained audit locally; replication of the chain piggybacks on the existing file/audit sync endpoints but is not consensus-validated).
- TLS / mTLS between nodes (v1 trusts the LAN; add Caddy/Traefik reverse proxy with self-signed certs later).
- Web UI multi-user accounts.
- Garbage collection for chunks orphaned by file deletions.
- Per-neighbor rate limiting.
- `out/` system backup feature.
- Smart mesh routing based on latency (currently info-only).

---

### Resolved Decisions

- **`out/` folder** — Reserved for system backup. Not used in v1; implementation deferred to v2.
- **File deletion cascade** — Auto-delete local copy when a new version is successfully synced. A new file always replaces an older file of the same name (latest `updated_at` wins); the old copy is removed once the replacement is verified. Additionally, when the owning node deletes a file, that deletion cascades to other nodes via the audit `file.delete` event on the next sync.
- **Max file size cap** — Default 2 GB (32 × 64 MB chunks), configurable via env var `MAX_FILE_SIZE_MB` (chunks adjust automatically: `chunk_count = ceil(file_size / MAX_CHUNK_MB)`).
- **`DELETE_LOCAL`** — New env var (default `FALSE`). When `TRUE`, if the owning node deletes a file, the local node deletes its copy too. Cascades through the same `file.delete` audit event.

### Node Roles (All Nodes Are Identical)

Every node runs the same binary. What changes is config + relationships:

| Role concept         | How it's expressed                                                                 |
| -------------------- | ---------------------------------------------------------------------------------- |
| **Your node as master** | Set `MASTER=TRUE` and `NEIGHBORS=MANUAL`/`AUTO`. Other nodes treat this node as their master (full pull on connect, then delta pull every 5 min). |
| **Your node as neighbor** | Leave `MASTER=FALSE`. Node sends hello to its configured master, awaits approval, then begins bidirectional sync. |
| **Mesh sync** | Any approved neighbor can sync with any other approved neighbor — not just master ↔ node. The full approved-neighbor list is shared in every hello response, so new peers are discovered immediately. |

> **Rule:** Once a neighbor is in your `approved` list, you pull *all* files from them (not only files originating at your master). This makes the network a true mesh, not a star.

A node may also be a master for some peers AND a neighbor for a higher-level master — this is the standard tree-of-meshes topology. The image is unchanged; only env vars and relationships differ.

### New Env Var

| Env var          | Default  | Purpose                                                                        |
| ---------------- | -------- | ------------------------------------------------------------------------------ |
| `DELETE_LOCAL`   | `FALSE`  | If `TRUE`, propagate deletions of files you own to other nodes (and accept them from owners). |
| `MAX_FILE_SIZE_MB` | `2048` | Max upload size; chunk size stays 64 MB, chunk count adjusts automatically. |

### Neighbor Metrics — Latency

Each hello and each pull tick records `latency_ms` (round-trip time of the last successful request). Stored on the `neighbors` table:

````sql
ALTER TABLE neighbors ADD COLUMN latency_ms INTEGER;
````

- Displayed in the web UI `neighbors` table as a column.
- Computed on every hello handshake and every successful `/api/v1/index` call.
- **Info-only in v1** — no routing decisions based on latency (all approved neighbors are equal).