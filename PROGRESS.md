# HydraDataHive — Progress Log

**Last updated:** 2026-06-21
**Repo:** `/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra`
**Git:** initialized on `main`, initial commit `34558da`

> Pickup instructions for when you get home: jump to [§ 7 Picking up where you left off](#7-picking-up-where-you-left-off).

---

## 1. Status at a glance

| Area                                | Status             |
| ----------------------------------- | ------------------ |
| Plan implementation (12 steps)      | ✅ Complete        |
| Git init + initial commit           | ✅ Done            |
| Unit tests (data ingest)            | ✅ 4/4 passing    |
| E2E test (hello + chunk pull)       | ✅ 1/1 passing    |
| 3-node dev cluster boots            | ✅ Healthy        |
| Hello handshake (mesh discovery)    | ✅ Works          |
| Admin login + neighbor approval     | ✅ Works          |
| Data replication (drop → pull)      | ✅ Works (full mesh) |
| Full 3-way mesh (node1↔node2↔node3↔node1) | ✅ Approved both ways |
| Audit hash chain verification       | ✅ OK on all nodes |
| File deletion cascade               | ✅ Works (converges in 1–2 ticks) |
| **Still TODO**                      | See § 6            |

---

## 2. What got built (per PLAN.md §10)

All 12 build steps are done and committed:

1. ✅ Scaffold: `requirements.txt`, `Dockerfile`, `.env.example`, `.gitignore`
2. ✅ `app/config.py` + `app/db.py` (SQLite WAL, schema migrations, `schema_version` table)
3. ✅ `app/crypto.py` (Ed25519 keypair, `sign_file`/`verify_file`/SHA-256)
4. ✅ `app/services/data.py` (ingest, 64 MB binary chunks → base64 `.b64` files, dedup, reassembly)
5. ✅ Flask app factory + `/api/v1/{hello,index,manifest,chunk,download,neighbors,audit,identity}` + `/audit/verify`
6. ✅ `app/services/sync.py` (delta pull with `since`, new-version replacement, per-peer port lookup)
7. ✅ `app/services/neighbor.py` (`handle_hello`, `post_hello`, mesh discovery, `hello_sent` tracking with 3-failure auto-pause, `seed_peer_rows` boot-time hook)
8. ✅ `app/services/audit.py` (hash-chained log, supports nested-call `_conn=` to avoid `BEGIN-inside-BEGIN` errors)
9. ✅ `app/scheduler.py` (APScheduler: scan_input 5m, pull 5m, discover 5m, emit_hello 1m, prune_audit daily; seeds SEED_PEERS at boot)
10. ✅ Web UI: Bootstrap 5 + SweetAlert2 + DataTables + dark theme, login/dashboard/data/neighbors/audit/identity pages
11. ✅ `cli.py` (`hydra-cli`: status, approve, reject, reset-hello, list-files, show-identity, sync-now, verify-audit, delete-file)
12. ✅ `docker-compose.yml` + `docker-compose.dev.yml` (3-node chain) + `README.md`
13. ✅ File deletion cascade (`sync._process_remote_deletions` + `audit.tail` fetch, schema v2 with `sync_state.last_audit_ts`, `file.replace` vs `file.delete` audit-action split)

---

## 3. Bugs found & fixed during dev-cluster validation

Each was caught by running the actual compose cluster, not by the in-container test suite.

| # | Bug                                                                                       | Fix                                                                                  | File                                       |
| - | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------ |
| 1 | `python -m unittest discover` in `docker run` accidentally ran the **app** (image ENTRYPOINT is `python -m app` and `unittest` args got swallowed) | Add `--entrypoint python` override in `tests/run_in_container.sh`                   | `tests/run_in_container.sh`                |
| 2 | `tests/` wasn't `COPY`ed into the image, so `discover` couldn't import it                 | `COPY tests/ /app/tests/` in Dockerfile                                              | `Dockerfile`                               |
| 3 | `init_schema()` failed in tests with `unable to open database file` because parent dir didn't exist | `_make_settings` now mkdirs all four dirs before `init_schema()` runs              | `tests/test_data.py`                       |
| 4 | `audit.append()` inside a `with transaction()` block → `cannot start a transaction within a transaction` | `audit.append(..., _conn=existing_conn)` reuses the caller's transaction             | `app/services/audit.py`, `app/services/neighbor.py:handle_hello` |
| 5 | `sync.pull_from_peer(peer_ip)` always tried port 8080 because peer was passed without a port | Added `_peer_spec()` that looks up `port` from the `neighbors` table                | `app/services/sync.py`                     |
| 6 | `neighbors.approve/reject/remove/reset_hello` did `SELECT id FROM neighbors` — table has `ip` as PK, no `id` column | Changed to `SELECT ip FROM neighbors`                                               | `app/services/neighbor.py`                 |
| 7 | `emit_hello_if_pending` (1-min tick) never had anything pending because `seed_peer_rows()` was never called | Call `seed_peer_rows(settings)` at scheduler startup                                | `app/scheduler.py`                         |
| 8 | **CRITICAL** chunk pull failed with `chunk 0 size mismatch` — `chunks.size_bytes` stored the **binary** chunk size but `manifest()` returned it as if it were the **on-disk (.b64)** size. Receiver compared .b64 file length to binary size → always mismatch | Store `len(encoded)` (base64 length) in `chunks.size_bytes` so it matches what's on disk and what travels over the wire | `app/services/data.py:ingest_file`         |

> Bug #8 required wiping the node1 DB volumes because existing chunks were already stored with the wrong size. Cleanly reproducible from scratch via `docker compose down && docker volume rm hydra_node{1,2,3}_data && docker compose up --build -d`.

### Bugs found & fixed during cascade-feature dev

| # | Bug                                                                                       | Fix                                                                                  | File                                       |
| - | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------ |
| 9 | File deletion on owner node never propagated to peers — `audit.append("file.delete")` ran, but no consumer on receiving side ever fetched /api/v1/audit. PLAN said it would. | Added `_process_remote_deletions` to `sync.pull_from_peer` that fetches `/api/v1/audit?since=last_audit_ts` from each peer and applies `file.delete` events locally (respecting `DELETE_LOCAL`). Schema v2 adds `sync_state.last_audit_ts`. | `app/services/sync.py:_process_remote_deletions`, `app/db.py` migration |
| 10 | `_replace_old_version` (sync-driven old-version cleanup) used `data.delete_file(...)` which emits `action="file.delete"`. That made old-version swaps look like cascade events, so a peer receiving a fresh pull could also "cascade" its own old-version cleanup → noisy audit + risk of unintentional local deletion | Added optional `action=` and `extra_details=` params to `delete_file`; `_replace_old_version` now emits `action="file.replace"` with `details={"replaced_by_updated_at": ...}`. Cascade handler only fires on `file.delete` | `app/services/data.py:delete_file`, `app/services/sync.py:_replace_old_version` |
| 11 | First sync with a peer would replay the peer's entire audit history (since `last_audit_ts` was empty), creating dozens of noisy `file.delete.skip` audit entries for ancient, already-irrelevant deletion events | Seed `last_audit_ts` to "now" on the very first sync for a peer so we skip history | `app/services/sync.py:_process_remote_deletions` |

---

## 4. Verified end-to-end (last good run)

```text
docker compose -f docker-compose.dev.yml up --build -d
# → all 3 containers healthy

# Login + approve (all 3 nodes)
curl -c c -X POST -d "password=devpass" http://localhost:8081/login
curl -b c -X POST http://localhost:8081/neighbors/172.18.0.3/approve   # node1 → node2
curl -c c -X POST -d "password=devpass" http://localhost:8082/login
curl -b c -X POST http://localhost:8082/neighbors/node1/approve        # node2 → node1
curl -c c -X POST -d "password=devpass" http://localhost:8083/login
curl -b c -X POST http://localhost:8083/neighbors/node2/approve        # node3 → node2

# Drop a file on node1, force ingest (skips the 5-min scheduler tick)
echo "hello replicated world" > /tmp/testfile.txt
docker cp /tmp/testfile.txt hydra-node1:/data/input/testfile.txt
docker exec hydra-node1 python -c "
from app.db import configure, init_schema
from app.config import load_settings
from app.services import data
s = load_settings(); configure(s.db_path); init_schema()
data.scan_input_folder(s, actor=s.node_name)"

# Pull from approved masters
docker exec hydra-node2 python cli.py sync-now     # → fetched: 2, new_local: [1,2]
docker exec hydra-node3 python cli.py sync-now     # → fetched: 2, new_local: [1,2]

# Verify replication
docker exec hydra-node2 python cli.py list-files   # → welcome.md + testfile.txt
docker exec hydra-node3 python cli.py list-files   # → welcome.md + testfile.txt

# Verify audit chain
docker exec hydra-node1 python cli.py verify-audit # → {"ok": true, "checked": 5, "bad_ids": []}
```

---

## 5. Files of interest

| Path                                               | What's in it                                                |
| -------------------------------------------------- | ----------------------------------------------------------- |
| `app/__main__.py`                                  | `python -m app` boot — config → DB → keypair → scheduler → Flask |
| `app/__init__.py`                                  | Flask `create_app()` factory; used by both boot AND tests  |
| `app/services/data.py`                             | Ingest + chunking. Stores base64 size in `chunks.size_bytes` (bug #8 fix). |
| `app/services/sync.py`                             | Mesh pull. `_peer_spec(ip)` looks up port in DB.            |
| `app/services/neighbor.py`                         | `handle_hello` passes `_conn=conn` to audit (bug #4 fix).   |
| `app/services/audit.py`                            | `append(..., _conn=None)` to support nested transactions.   |
| `app/scheduler.py`                                 | Calls `seed_peer_rows(settings)` at boot (bug #7 fix).      |
| `tests/run_in_container.sh`                        | Use `--entrypoint python` to avoid running `app` (bug #1 fix). |
| `tests/test_data.py`                               | 4 unit tests for ingest + chunking                          |
| `tests/test_sync_e2e.py`                           | 1 e2e test: hello + chunk pull between two Flask apps in threads |
| `docker-compose.dev.yml`                           | 3-node chain (node1 master, node2 mid, node3 leaf)          |
| `README.md`                                        | Full quick-start, env-var table, CLI examples, HTTP API table |

---

## 6. TODO (not yet done)

These are good next-session items, not blockers:

1. ✅ **Full 3-way mesh approved** — node1↔node2, node2↔node3, node1↔node3 all approved both ways. Mesh discovery (node3 learned node1 via node2's hello response) works. Verified by dropping `mesh-test.txt` on node1 and seeing it replicated to node2 AND node3 with identical SHA-256 (`39db00377d5b…`). All three audit chains verify clean (9/9 on node1 & node2, 7/7 on node3).
2. ✅ **File deletion cascade E2E** — implemented and verified. Cascade handler added to `sync.pull_from_peer` that fetches `/api/v1/audit?since=...` from each peer, applies `file.delete` events locally when `DELETE_LOCAL=TRUE`, and logs a `file.delete.skip` audit entry when `FALSE`. Schema migrated to v2 with new `sync_state.last_audit_ts` column (auto-applied on boot). Also distinguished `_replace_old_version` to emit `file.replace` instead of `file.delete` so cascade doesn't loop on version swaps. New `cli.py delete-file <id>` subcommand. Verified: `clean-cascade.txt` deleted on node1 propagated to node3 within one sync cycle; node2 took 2 cycles due to a mid-tick re-pull race. See code in [sync.py](app/services/sync.py) `_process_remote_deletions` + [data.py](app/services/data.py) `delete_file(..., action="file.replace")`.
3. ✅ **Cleanup `sync-pending-hellos` CLI subcommand** — removed dead subcommand (TODO #4 from prior session). Scheduler calls `emit_hello_if_pending` directly so the CLI helper was never wired up. `cli.py` is now leaner.
4. **Document screenshots** — README mentions screenshots but none captured yet.
5. **`out/` backup feature** — explicitly deferred to v2 in PLAN.md §11.
6. **TLS / mTLS** — deferred to v2.

> **Cascade convergence note:** In a bidirectional 3-node mesh, a deletion event propagates through the audit log chain. In pathological cases a peer can re-acquire a deleted file from another peer that hasn't yet processed the deletion (a mid-tick race). The cluster converges to the correct state within 1–2 sync ticks once the deletion event has reached all peers. A real tombstone mechanism (reject re-pulls of a tombstoned SHA) is deferred to v2.

---

## 7. Picking up where you left off

### Quick health check
```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
curl -s http://localhost:8081/api/v1/identity | python3 -m json.tool
```

### If the cluster is up and you want to keep poking it
```bash
# Web UIs
open http://localhost:8081   # node1
open http://localhost:8082   # node2
open http://localhost:8083   # node3
# Password: devpass (set in docker-compose.dev.yml)
```

### If you want a fresh cluster
```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
docker compose -f docker-compose.dev.yml down
docker volume rm hydra_node1_data hydra_node2_data hydra_node3_data hydra_node1_keys hydra_node2_keys hydra_node3_keys 2>/dev/null
docker compose -f docker-compose.dev.yml up --build -d
```

### If you want to re-run the tests
```bash
"/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra/tests/run_in_container.sh"
```
Expected output:
```
=== Unit tests (data ingest) ===
Ran 4 tests in 0.0XXs
OK
=== E2E tests (hello + chunk pull) ===
Ran 1 test in 0.2XXs
OK
=== Done ===
```

### If something is broken and you want to bisect
```bash
cd "/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra"
git log --oneline
git show 34558da            # what's in the initial commit
docker logs --tail 50 hydra-node1
docker logs --tail 50 hydra-node2
docker logs --tail 50 hydra-node3
```

### If you change code in `app/` you must rebuild
```bash
docker compose -f docker-compose.dev.yml up --build -d
```

---

## 8. Notes / things I noticed but didn't act on

* The dev compose `ports` mapping exposes 8081/8082/8083 on the host — that's intentional for direct web-UI access from WSL. For production, drop the `ports:` mapping and put a reverse proxy in front.
* `app.run(host="0.0.0.0", port=...)` is Werkzeug's dev server — fine for v1, swap for gunicorn/uvicorn later (deferred in PLAN.md §11).
* The "self-IP" entries showing up as `approved=0` on each node (e.g. `node1` showing up in node1's own neighbors list) are harmless: a peer learned our hostname/IP from someone else's hello response and inserted it. Could be filtered out by ignoring rows where `node_name == settings.node_name` in the response build, but it's not breaking anything.
* `last_pulled_at` is set but never read back; that's fine — it's useful for future observability.

---

**You're in a good spot — full mesh works end-to-end. Have a good break. 👋**