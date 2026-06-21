"""`hydra-cli` — operator CLI.

Subcommands mirror the plan:

- status
- approve <ip>
- reject <ip>
- reset-hello <ip>
- list-files [--since ISO]
- show-identity
- sync-now [--peer ip:port]
- verify-audit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running as `python cli.py ...` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import load_settings
from app.crypto import load_or_generate
from app.db import configure as db_configure, init_schema
from app.services import audit, data, neighbor, sync


def _bootstrap():
    s = load_settings()
    db_configure(s.db_path)
    init_schema()
    return s


def cmd_status(args, s):
    rows = data.list_files(5)
    print(f"Node: {s.node_name}  master={s.is_master}  mode={s.neighbors_mode}")
    print(f"Files: {len(rows)} (showing top {len(rows)})")
    for r in rows:
        print(f"  #{r['id']} {r['name']} ({r['size_bytes']} B) by {r['uploaded_by']}")


def cmd_approve(args, s):
    if neighbor.approve(args.ip, actor="cli"):
        print(f"approved {args.ip}")
    else:
        print(f"no such neighbor: {args.ip}", file=sys.stderr); sys.exit(1)


def cmd_reject(args, s):
    if neighbor.reject(args.ip, actor="cli"):
        print(f"rejected {args.ip}")
    else:
        print(f"no such neighbor: {args.ip}", file=sys.stderr); sys.exit(1)


def cmd_reset_hello(args, s):
    if neighbor.reset_hello(args.ip, actor="cli"):
        print(f"reset hello for {args.ip}")
    else:
        print(f"no such neighbor: {args.ip}", file=sys.stderr); sys.exit(1)


def cmd_list_files(args, s):
    rows = data.files_index_since(args.since)
    for r in rows:
        print(f"{r['updated_at']}  {r['sha256'][:12]}…  {r['name']} ({r['size_bytes']} B)")
    print(f"-- {len(rows)} files")


def cmd_show_identity(args, s):
    kp = load_or_generate(s.key_path, s.pub_path)
    print(json.dumps({
        "node_name": s.node_name,
        "is_master": s.is_master,
        "public_key": kp.pub_hex,
        "http_port": s.http_port,
    }, indent=2))


def cmd_sync_now(args, s):
    if args.peer:
        out = sync.pull_from_peer(s, args.peer)
        print(json.dumps(out, indent=2, default=str))
    else:
        out = sync.pull_from_approved_peers(s)
        print(json.dumps(out, indent=2, default=str))


def cmd_verify_audit(args, s):
    res = audit.verify_chain()
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)


def cmd_approve_pending_hellos(args, s):
    """Convenience helper used by e2e: hello all pending neighbors once."""
    sent = neighbor.emit_hello_if_pending(s)
    print(f"sent {sent} hellos")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hydra-cli", description="HydraDataHive operator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("show-identity").set_defaults(fn=cmd_show_identity)
    sub.add_parser("verify-audit").set_defaults(fn=cmd_verify_audit)
    sub.add_parser("sync-pending-hellos").set_defaults(fn=cmd_approve_pending_hellos)

    sp = sub.add_parser("approve"); sp.add_argument("ip"); sp.set_defaults(fn=cmd_approve)
    sp = sub.add_parser("reject"); sp.add_argument("ip"); sp.set_defaults(fn=cmd_reject)
    sp = sub.add_parser("reset-hello"); sp.add_argument("ip"); sp.set_defaults(fn=cmd_reset_hello)

    sp = sub.add_parser("list-files")
    sp.add_argument("--since", default=None)
    sp.set_defaults(fn=cmd_list_files)

    sp = sub.add_parser("sync-now")
    sp.add_argument("--peer", default=None)
    sp.set_defaults(fn=cmd_sync_now)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    s = _bootstrap()
    return args.fn(args, s) or 0


if __name__ == "__main__":
    sys.exit(main())