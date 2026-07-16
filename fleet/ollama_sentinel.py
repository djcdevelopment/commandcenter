#!/usr/bin/env python3
"""ollama-sentinel — mechnet bypass sentinel, slice 0 (visibility first).

The HEARTH door ledgers every inference call that goes THROUGH it — but the
one-boundary rule is convention, not enforcement: a script or CLI agent can
POST straight to Ollama on :11434 and leave no record. Ollama's own server.log
writes no per-request lines (verified 2026-07-16: zero request entries in a
27 MB log), so this sentinel samples network state instead: each tick parses
``netstat -ano`` for connections to the Ollama port, excludes the gateway's
own PID (door traffic), attributes the rest — a same-host client by PID and
process name, a foreign client by source IP prefix — and appends newly-seen
direct connections to ``hearth/var/sentinel/ollama-direct.ndjson``.

Runs as one tick per invocation (``python -m fleet.ollama_sentinel --json``),
fired every 120s by the gateway's in-process timer registry (ADR-0015). A
TTL'd seen-state dedups sustained connections across ticks, and door tuples
observed while ESTABLISHED are remembered so their later TIME_WAIT ghosts
(pid 0, unattributable) don't re-report as direct traffic.

Honest limitation: this is a sampler — short calls that open and close between
ticks can slip through unrecorded. Full per-request interception (a ledgering
proxy owning :11434) is a separate register decision, to be made after this
sentinel's data shows how much direct traffic actually exists.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

DEFAULT_PORT = 11434
SEEN_TTL_S = 600
VAR_DIR = Path(__file__).resolve().parents[1] / "hearth" / "var" / "sentinel"

LABEL_PREFIXES: list[tuple[str, str]] = [
    ("127.", "loopback"),
    ("::1", "loopback"),
    ("192.168.12.", "lan-am4"),
    ("192.168.", "lan"),
    ("172.", "hyperv-nat"),
    ("100.", "tailnet"),
]


def parse_netstat(text: str, port: int) -> dict:
    """Split ``netstat -ano`` output into listener pids, inbound rows (server
    side, local_port == port), and client rows (remote_port == port — the
    attribution gold for same-host callers). Malformed lines are skipped."""
    listener_pids: set[int] = set()
    inbound: list[dict] = []
    client_rows: list[dict] = []

    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP":
            continue

        local_str, remote_str, state, pid_str = parts[1], parts[2], parts[3], parts[4]
        try:
            pid = int(pid_str)
        except ValueError:
            continue

        try:
            l_ip, l_port_str = local_str.rsplit(":", 1)
            r_ip, r_port_str = remote_str.rsplit(":", 1)
            local_ip = l_ip.strip("[]")
            local_port = int(l_port_str)
            remote_ip = r_ip.strip("[]")
            remote_port = int(r_port_str)
        except Exception:
            continue

        row = {
            "local_ip": local_ip, "local_port": local_port,
            "remote_ip": remote_ip, "remote_port": remote_port,
            "state": state, "pid": pid,
        }
        if local_port == port:
            if state == "LISTENING":
                listener_pids.add(pid)
            else:
                inbound.append(row)
        if remote_port == port:
            client_rows.append(row)

    return {
        "listener_pids": listener_pids,
        "inbound": inbound,
        "client_rows": client_rows,
    }


def label_for(ip: str) -> str:
    for prefix, label in LABEL_PREFIXES:
        if ip.startswith(prefix):
            return label
    return "other"


def attribute(inbound: list[dict], client_rows: list[dict], exclude_pids: set[int],
              process_lookup: Callable[[int], Optional[str]]) -> tuple[list[dict], list[str]]:
    """Turn inbound rows into direct-connection records, splitting out door
    traffic (client pid in exclude_pids) as excluded keys so run_once can
    remember those tuples against their future TIME_WAIT ghosts.

    A second sweep covers client-side rows with no matching inbound row: the
    two sides of a closed loopback connection do not linger symmetrically, so
    a bypass whose server-side entry is already gone still shows a client-side
    TIME_WAIT ghost (proven live 2026-07-16 — the first curl bypass slipped
    the inbound-only design entirely)."""
    direct_records = []
    excluded_keys = []
    handled_keys: set[str] = set()

    for in_row in inbound:
        rem_ip = in_row["remote_ip"]
        rem_port = in_row["remote_port"]
        key = f"{rem_ip}:{rem_port}"
        handled_keys.add(key)

        client_pid = None
        for c_row in client_rows:
            if c_row["local_ip"] == rem_ip and c_row["local_port"] == rem_port:
                client_pid = c_row["pid"]
                break

        if client_pid is not None and client_pid in exclude_pids:
            excluded_keys.append(key)
        else:
            process = process_lookup(client_pid) if client_pid is not None else None
            direct_records.append({
                "source_ip": rem_ip,
                "source_port": rem_port,
                "state": in_row["state"],
                "pid": client_pid,
                "process": process,
                "label": label_for(rem_ip),
            })

    # Client-side sweep: rows targeting the port whose server-side twin is
    # absent. The client row's own pid IS the caller when still populated
    # (pid 0 once the socket is in TIME_WAIT).
    for c_row in client_rows:
        key = f"{c_row['local_ip']}:{c_row['local_port']}"
        if key in handled_keys:
            continue
        handled_keys.add(key)
        client_pid = c_row["pid"] if c_row["pid"] != 0 else None
        if client_pid is not None and client_pid in exclude_pids:
            excluded_keys.append(key)
        else:
            process = process_lookup(client_pid) if client_pid is not None else None
            direct_records.append({
                "source_ip": c_row["local_ip"],
                "source_port": c_row["local_port"],
                "state": c_row["state"],
                "pid": client_pid,
                "process": process,
                "label": label_for(c_row["local_ip"]),
            })

    return direct_records, excluded_keys


def load_seen(path: Path, now: float) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if now - v <= SEEN_TTL_S}
    except Exception:
        return {}


def save_seen(path: Path, seen: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f)


def run_once(netstat_text: str, exclude_pids: set[int], var_dir: Path, now: float,
             process_lookup: Callable[[int], Optional[str]],
             port: int = DEFAULT_PORT) -> dict:
    """One sentinel tick over the given netstat text: attribute, dedup against
    the TTL'd seen-state, append new direct records, remember door tuples."""
    parsed = parse_netstat(netstat_text, port)

    combined_excludes = set(exclude_pids)
    combined_excludes.update(parsed["listener_pids"])

    direct_records, excluded_keys = attribute(
        parsed["inbound"], parsed["client_rows"], combined_excludes, process_lookup
    )

    seen_path = var_dir / "ollama-seen.json"
    seen = load_seen(seen_path, now)

    for k in excluded_keys:
        seen[k] = now

    new_direct = []
    ts = datetime.fromtimestamp(now, timezone.utc).isoformat()
    for rec in direct_records:
        k = f"{rec['source_ip']}:{rec['source_port']}"
        if k not in seen:
            rec["ts"] = ts
            new_direct.append(rec)
        seen[k] = now

    if new_direct:
        var_dir.mkdir(parents=True, exist_ok=True)
        with open(var_dir / "ollama-direct.ndjson", "a", encoding="utf-8") as f:
            for rec in new_direct:
                f.write(json.dumps(rec) + "\n")

    save_seen(seen_path, seen)

    return {
        "ok": True,
        "sampled_at": ts,
        "inbound_seen": len(parsed["inbound"]),
        "new_direct": len(new_direct),
        "records": new_direct,
    }


class ProcessLookup:
    """PID -> process name via tasklist, cached per tick; failures resolve to
    None rather than raising (a sentinel never fails on attribution)."""

    def __init__(self) -> None:
        self.cache: dict[int, Optional[str]] = {}

    def __call__(self, pid: int) -> Optional[str]:
        if pid in self.cache:
            return self.cache[pid]
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip() and not line.startswith("INFO:"):
                        name = line.split(",")[0].strip('"')
                        self.cache[pid] = name
                        return name
        except Exception:
            pass
        self.cache[pid] = None
        return None


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="mechnet bypass sentinel: one netstat sample tick")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--exclude-pid", action="append", type=int, default=[])
    parser.add_argument("--netstat-file", type=Path)
    parser.add_argument("--var-dir", type=Path, default=VAR_DIR)
    args = parser.parse_args(argv)

    excludes = set(args.exclude_pid)
    excludes.add(os.getpid())
    excludes.add(os.getppid())

    if args.netstat_file:
        try:
            text = args.netstat_file.read_text(encoding="utf-8")
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
            print(json.dumps(res) if args.json else f"ok=False error={exc}")
            sys.exit(1)
    else:
        try:
            proc = subprocess.run(["netstat", "-ano"], capture_output=True,
                                  text=True, timeout=30)
            text = proc.stdout
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
            print(json.dumps(res) if args.json else f"ok=False error={exc}")
            sys.exit(1)

    now = datetime.now(timezone.utc).timestamp()
    summary = run_once(text, excludes, args.var_dir, now, ProcessLookup(), args.port)

    if args.json:
        print(json.dumps(summary))
    else:
        print(f"ok=True inbound={summary['inbound_seen']} new_direct={summary['new_direct']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
