"""APScheduler jobs for Hydra.

| Job                          | Cadence    |
| ---------------------------- | ---------- |
| ``scan_input_folder``        | 5 min      |
| ``pull_from_approved_peers`` | 5 min      |
| ``discover_mesh_peers``      | 5 min      |
| ``emit_hello_if_pending``    | 1 min      |
| ``prune_audit_log``          | daily      |
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings
from app.services import audit, data, neighbor, sync

log = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_scan_input(settings: Settings) -> None:
    try:
        ids = data.scan_input_folder(settings, actor=settings.node_name)
        if ids:
            log.info("scan_input_folder: ingested %d files", len(ids))
    except Exception as exc:  # noqa: BLE001
        log.exception("scan_input_folder failed: %s", exc)


def _job_pull_peers(settings: Settings) -> None:
    try:
        results = sync.pull_from_approved_peers(settings)
        if results:
            log.info("pull_from_approved_peers: %s", results)
    except Exception as exc:  # noqa: BLE001
        log.exception("pull_from_approved_peers failed: %s", exc)


def _job_discover(settings: Settings) -> None:
    try:
        n = neighbor.discover_mesh_peers(settings)
        if n:
            log.info("discover_mesh_peers: refreshed %d approved peers", n)
    except Exception as exc:  # noqa: BLE001
        log.exception("discover_mesh_peers failed: %s", exc)


def _job_emit_hello(settings: Settings) -> None:
    try:
        n = neighbor.emit_hello_if_pending(settings)
        if n:
            log.info("emit_hello_if_pending: sent %d hellos", n)
    except Exception as exc:  # noqa: BLE001
        log.exception("emit_hello_if_pending failed: %s", exc)


def _job_prune_audit() -> None:
    # v1: keep everything; placeholder for retention policy.
    try:
        chain = audit.verify_chain()
        if not chain.get("ok"):
            log.warning("audit chain verification failed: %s", chain)
    except Exception as exc:  # noqa: BLE001
        log.exception("audit prune failed: %s", exc)


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = BackgroundScheduler(timezone="UTC")
    # Seed SEED_PEERS / MASTER_PEERS into the neighbors table on boot so the
    # hello scheduler will pick them up immediately.
    try:
        from app.services import neighbor as _nbr
        _nbr.seed_peer_rows(settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("seed_peer_rows failed: %s", exc)

    sched.add_job(
        _job_scan_input, "interval", minutes=5, args=[settings], id="scan_input_folder",
        next_run_time=datetime.now(timezone.utc),
    )
    sched.add_job(
        _job_pull_peers, "interval", minutes=5, args=[settings], id="pull_from_approved_peers",
        next_run_time=datetime.now(timezone.utc),
    )
    sched.add_job(
        _job_discover, "interval", minutes=5, args=[settings], id="discover_mesh_peers",
        next_run_time=datetime.now(timezone.utc),
    )
    sched.add_job(
        _job_emit_hello, "interval", minutes=1, args=[settings], id="emit_hello_if_pending",
        next_run_time=datetime.now(timezone.utc),
    )
    sched.add_job(
        _job_prune_audit, "interval", hours=24, id="prune_audit",
        next_run_time=datetime.now(timezone.utc),
    )
    sched.start()
    _scheduler = sched
    log.info("scheduler started")
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None