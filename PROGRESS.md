# HydraDataHive — Progress Log

**Last updated:** 2026-06-21
**Repo:** `/mnt/c/Users/herman.ras/GoogleDrive/DEV/WEB_DEV/DevTest/Hydra`
**Branch:** `main`
**Latest tag:** [`ALPHA1-unbranded`](https://github.com/HermanRas/HydraDataHive/releases/tag/ALPHA1-unbranded)

> Pre-alpha development history (commits `34558da` through `9c54cb8`) lives in
> [PRE_ALPHA_PROGRESS.md](./PRE_ALPHA_PROGRESS.md). This file is the working
> progress log from the ALPHA1-unbranded release onwards.

---

## 1. Overview

HydraDataHive is a lightweight, containerized data-replication service. Every
node runs the same Flask image; behavior is driven entirely by environment
variables. Files dropped into a node's `data/input/` are SHA-256'd, signed with
the node's Ed25519 key, split into 64 MB base64 chunks, and replicated to
approved peers via a mesh-pull on a 5-minute scheduler tick. All audit events
are hash-chained so tampering is detectable.

**Project status:** ALPHA1-unbranded released on 2026-06-21. Internal testing
in progress. v1 release prep underway after the alpha-tester bug-report phase.

---

## 2. Roadmap phases

| Phase                       | Status             | Notes                                          |
| --------------------------- | ------------------ | ---------------------------------------------- |
| Pre-alpha development        | ✅ Complete        | See [PRE_ALPHA_PROGRESS.md](./PRE_ALPHA_PROGRESS.md) |
| ALPHA1-unbranded release    | ✅ Released        | Tag `ALPHA1-unbranded`, commit `95cb499`        |
| Alpha testing + bug reports | 🔄 In progress    | See [§ 4 Bugs reported by testers](#4-bugs-reported-by-testers) |
| Branding & colour phase      | ⏳ Not started    | See [§ 5 Branding & colour phase](#5-branding--colour-phase) |
| v1 release                  | ⏳ Not started    | See [§ 6 v1 release prep](#6-v1-release-prep)              |
| v2 features                 | ⏳ Future          | See [§ 7 v2 features (deferred from v1)](#7-v2-features-deferred-from-v1) |

---

## 3. Open items carried forward from pre-alpha

These came out of the pre-alpha dev cycle and were not closed before the alpha
release:

| # | Item                                                              | Status       | Notes |
| - | ----------------------------------------------------------------- | ------------ | ----- |
| 1 | Document screenshots for README                                   | ⏳ Open     | Waiting on branding pass to use final assets |
| 2 | Self-IP rows in `/neighbors` (visual noise)                       | ⏳ Open     | Harmless — each node shows its own hostname/IP as `approved=0` |
| 3 | Cascade convergence via real tombstones (reject re-pulls of tombstoned SHA) | ⏳ Open | Deferred — current 1–2-tick convergence acceptable for v1 |
| 4 | `out/` backup feature                                             | ⏳ Open     | Deferred to v2 — folder reserved, no behaviour |
| 5 | TLS / mTLS                                                        | ⏳ Open     | Deferred to v2 — v1 trusts the LAN |
| 6 | `hydra-cli` upload subcommand                                     | ⏳ Open     | Intentionally omitted — input folder is canonical ingest |

---

## 4. Bugs reported by testers

> Add a new row for each bug you find. Bug IDs continue from pre-alpha (#12+).

| # | Reported by | Date       | Severity | Summary                                                                 | Reproduction                                                                                                              | Status   | Notes |
| - | ----------- | ---------- | -------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------- | ----- |
| - | -           | -          | -        | *(no tester-reported bugs yet)*                                          | -                                                                                                                          | -        |       |

**Severity legend:** 🟥 blocker · 🟧 high · 🟨 medium · 🟩 low · ⬜ cosmetic

---

## 5. Branding & colour phase

**Goal:** Apply the existing HydraDataHive banner / logo assets consistently
across the web UI, README, and CLI so the visual identity is coherent before
v1 ships.

**Assets already in the repo** (commit `cb492bb`):

- `HydraDataHiveLogo_square_366x366.png` (366×366, colour)
- `HydraDataHiveLogo_square_bw_366x366.png` (366×366, B&W)
- `HydraDataHiveLogo_wide_685x380.png` (685×380, colour)
- `HydraDataHiveLogo_wide_bw_685x380.png` (685×380, B&W)
- `HydraDataHive_banner_1376x378.png` (1376×378, colour)

**Current usage:**
- ✅ README header uses the wide banner
- ⏳ Web UI navbar uses a plain text "HydraDataHive" — no logo
- ⏳ Web UI login page — no branding
- ⏳ Dark theme uses ad-hoc colours (`#0f1115`, `#1a1d24`, `#6cc4ff` accent) — no defined palette

**Tasks for this phase:**

| # | Task                                                                            | Status       |
| - | ------------------------------------------------------------------------------- | ------------ |
| B1 | Extract brand colours from the banner / logo into a documented palette        | ⏳ Not started |
| B2 | Define semantic colour tokens (primary, accent, surface, border, text, muted)  | ⏳ Not started |
| B3 | Replace ad-hoc hex values in `hydra.css` with CSS custom properties            | ⏳ Not started |
| B4 | Add logo to web UI navbar (favicon + brand mark)                              | ⏳ Not started |
| B5 | Add brand logo to login page                                                   | ⏳ Not started |
| B6 | Replace README screenshots placeholder with actual captures from the branded UI | ⏳ Not started |
| B7 | Add favicon (`.ico` + apple-touch-icon) served by the Flask app              | ⏳ Not started |

---

## 6. v1 release prep

**Goal:** Ship a polished v1 release suitable for broader community use once
alpha-tester bugs are closed and the branding pass is complete.

**Pre-v1 checklist:**

| #  | Task                                                                            | Status       |
| -- | ------------------------------------------------------------------------------- | ------------ |
| V1 | Close all 🟥/🟧/🟨 bugs from § 4 (blockers and highs must be zero)              | ⏳ Not started |
| V2 | Complete branding phase (§ 5)                                                   | ⏳ Not started |
| V3 | Capture and embed UI screenshots in README                                     | ⏳ Not started |
| V4 | Decide v1 feature scope (carry forward list from [PRE_ALPHA_PROGRESS.md §6](./PRE_ALPHA_PROGRESS.md))  | ⏳ Not started |
| V5 | Update PLAN.md to mark v1-scope items, leave v2 items annotated                | ⏳ Not started |
| V6 | Write v1 release notes (`RELEASE_NOTES_V1.md`)                                 | ⏳ Not started |
| V7 | Tag `v1.0.0` and publish GitHub release                                        | ⏳ Not started |
| V8 | Remove "unbranded" from tag name                                              | ⏳ Not started |

---

## 7. v2 features (deferred from v1)

These items were intentionally deferred to v2 and are not part of the v1 scope:

| # | Feature                                                                  | Source                |
| - | ------------------------------------------------------------------------ | --------------------- |
| 1 | Full Merkle / cross-node consensus for the audit chain                  | PLAN.md §11           |
| 2 | TLS / mTLS between nodes                                                 | PLAN.md §11           |
| 3 | Multi-user accounts in the web UI                                        | PLAN.md §11           |
| 4 | Garbage collection for chunks orphaned by file deletions                 | PLAN.md §11           |
| 5 | Per-neighbor rate limiting and latency-based routing                    | PLAN.md §11           |
| 6 | `out/` backup feature                                                    | PLAN.md §11           |
| 7 | Real tombstone mechanism for cascade convergence                        | Pre-alpha TODO #3     |
| 8 | (TBD based on v1-tester feedback)                                        | Future                |