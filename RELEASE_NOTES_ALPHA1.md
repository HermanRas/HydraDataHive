# HydraDataHive — ALPHA1-unbranded

> **Tag:** `ALPHA1-unbranded`
> **Commit:** `66ba4a0` on `main`
> **Date:** 2026-06-21
> **Status:** Alpha — internal testing only. Not production-ready.

This is the **first public alpha** of HydraDataHive — a lightweight, containerized
data-replication service in which every node runs the same Flask image and
behaves as either master, neighbor, or both, driven purely by environment
variables. It is designed as a backup / high-availability tool aimed at
automation: there is no web-upload widget; the canonical ingest path is the
`data/input/` folder.

---

## What works (verified end-to-end)

| Capability                                  | Status |
| ------------------------------------------- | ------ |
| 3-node dev cluster boots cleanly            | ✅      |
| Signed `hello` handshake + mesh discovery   | ✅      |
| Admin login + manual neighbor approval      | ✅      |
| Full 3-way mesh (node1↔node2↔node3↔node1)   | ✅      |
| Data replication via delta pull (every 5 min) | ✅    |
| Ed25519 signing of every uploaded file     | ✅      |
| SHA-256 chunk verification on pull          | ✅      |
| Hash-chained audit log (verify on each node)| ✅      |
| File deletion cascade (`DELETE_LOCAL=TRUE`) | ✅      |
| `hydra-cli` operator CLI (`status`, `approve`, `reject`, `reset-hello`, `list-files`, `show-identity`, `sync-now`, `verify-audit`, `delete-file`) | ✅ |
| Unit tests (data ingest, 4 cases)           | ✅      |
| E2E test (hello + chunk pull, 1 case)       | ✅      |

---

## What's not in this alpha (deferred to v2)

- Full Merkle / cross-node consensus for the audit chain (v1 ships
  hash-chained audit locally; replication piggybacks on file/audit sync).
- TLS / mTLS between nodes (v1 trusts the LAN).
- Multi-user accounts in the web UI.
- Garbage collection for chunks orphaned by file deletions.
- Per-neighbor rate limiting and latency-based routing.
- `out/` backup feature (reserved folder, no behaviour yet).
- Web upload widget (by design — Hydra is for automation; fork to add one).

---

## 🚀 How to test this alpha

**Read [NEW_DEPLOYMENT_TEST_GUIDE.md](./NEW_DEPLOYMENT_TEST_GUIDE.md) first.**
It walks you through every UX flow end-to-end:

1. Build & boot the 3-node dev cluster (`docker compose -f docker-compose.dev.yml up --build`).
2. Open <http://localhost:8081>, <http://localhost:8082>, <http://localhost:8083> (password: `devpass`).
3. Approve the mesh (UI or CLI — both documented).
4. Drop a file into `hydra-node1:/data/input/`, force ingest + sync.
5. Verify the file appears on all 3 nodes with identical SHA-256.
6. Test file deletion cascade (flip `DELETE_LOCAL` to `TRUE` in compose, restart, delete a file, watch it propagate).
7. Verify the audit hash chain on every node.
8. Reset cluster (clean volumes, reboot).
9. One-liners cheat-sheet for daily use.

> Estimated time: ~20 minutes for the full walkthrough including cluster
> warm-up and the first manual sync.

---

## Known limitations / things to watch for

- **Cascade convergence** — in a full 3-way mesh, deletion propagates
  through the audit-log chain. Within 1–2 sync cycles the cluster
  converges to a consistent state. A real tombstone mechanism (reject
  re-pulls of a tombstoned SHA) is deferred to v2.
- **Self-IP rows** — each node shows its own hostname/IP as `approved=0`
  in the neighbors list. Harmless but visually noisy; can be filtered
  in the response builder later.
- **No real-time UI updates** — refresh the page to see the latest
  state. A small AJAX poll or SSE channel is a future improvement.
- **Dashboard `text-muted` readability** — fixed in this alpha via
  `hydra.css` overrides for the dark theme.

---

## File layout (this alpha)

```
hydra/
├── app/
│   ├── __init__.py            # Flask app factory
│   ├── __main__.py            # `python -m app` entrypoint
│   ├── config.py              # Env-var loader
│   ├── crypto.py              # Ed25519 keypair gen / sign / verify
│   ├── db.py                  # SQLite schema + helpers (WAL, v2 migration)
│   ├── auth.py                # Web UI login
│   ├── scheduler.py           # APScheduler jobs (5-min ticks)
│   ├── routes/
│   │   ├── api.py             # /api/v1/* JSON endpoints
│   │   └── web.py             # Jinja2 pages
│   ├── services/
│   │   ├── neighbor.py        # Hello handshake, approval, mesh discovery
│   │   ├── data.py            # Ingest, chunk, checksum, assemble
│   │   ├── seeding.py         # Seed welcome.md on master boot
│   │   ├── sync.py            # Mesh pull + deletion cascade
│   │   └── audit.py           # Hash-chained append-only log
│   ├── templates/             # Bootstrap 5 layout
│   └── static/hydra.css       # Dark-theme UI tweaks
├── data/                      # input/ + datastore/ + out/ + hydra.db
├── keys/                      # Ed25519 keypair (0600)
├── tests/                     # unittest suite, run inside the container
├── cli.py                     # `hydra-cli`
├── Dockerfile
├── docker-compose.yml
├── docker-compose.dev.yml
├── requirements.txt
├── PLAN.md                    # Full design + schema
├── PROGRESS.md                # Dev log (handoff notes)
├── NEW_DEPLOYMENT_TEST_GUIDE.md  # 👈 START HERE for the test walkthrough
└── RELEASE_NOTES_ALPHA1.md   # This file
```

---

## Reporting issues

Open a GitHub issue on <https://github.com/HermanRas/HydraDataHive/issues>
with:

- The node name and commit hash (`hydra-cli show-identity` + `git rev-parse HEAD`).
- The exact command(s) you ran.
- Output of `hydra-cli verify-audit` on the affected node.
- Relevant snippet from `docker logs hydra-nodeN --tail 100`.

---

## What's next (post-ALPHA1)

- Capture UI screenshots for the README.
- Resolve UX issues reported by alpha testers.
- Decide on v1.x feature scope (the deferred list above, prioritised).