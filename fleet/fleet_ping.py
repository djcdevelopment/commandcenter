#!/usr/bin/env python3
"""
fleet-ping — reachability sweep over the fleet inventory.

Reads fleet/inventory.toml (the canonical OMEN-side node map) and TCP-connects to
each node's declared service port(s), reporting up/down/latency as a table or JSON.
Runs from OMEN, which can see both the tailnet and its Hyper-V VM siblings.

    python -m fleet.fleet_ping                 # sweep primary reachability of every node
    python fleet/fleet_ping.py                 # same, run directly
    python -m fleet.fleet_ping --all-services  # probe EVERY declared service, not just the primary
    python -m fleet.fleet_ping --node claudefarm1
    python -m fleet.fleet_ping --json          # machine-readable
    python -m fleet.fleet_ping --timeout 2 --no-color

Exit code: 0 if every node with expect="up" is reachable; 1 if any is down (so it
can gate a script). Nodes with expect="optional" never affect the exit code.

Stdlib only (tomllib is stdlib on Python 3.11+); no third-party deps by design.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_INVENTORY = Path(__file__).with_name("inventory.toml")
DEFAULT_TIMEOUT = 3.0

# status → (ascii label, ansi color)
_STYLE = {
    "up":       ("UP     ", "\033[32m"),  # green
    "down":     ("DOWN   ", "\033[31m"),  # red
    "offline":  ("offline", "\033[90m"),  # dim (expected-optional, not reachable)
    "up-opt":   ("up     ", "\033[32m"),  # optional node that IS up
}
_RESET = "\033[0m"


# --- pure helpers (no network; unit-tested) ---------------------------------

def load_inventory(path: Path) -> dict:
    """Parse the TOML inventory into {meta, nodes:[...]}. Raises on malformed TOML."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {"meta": data.get("meta", {}), "nodes": data.get("node", [])}


def resolve_targets(node: dict, all_services: bool) -> list[dict]:
    """Return the list of {service, host, port} to probe for a node.

    Each check may override `host` (e.g. a logical builder whose shell lives on a
    different machine than its model backend); otherwise it falls back to the node
    address. With all_services=False only the FIRST check (primary reachability) is
    returned. A node with no checks yields nothing (reported as 'no-check').
    """
    checks = node.get("checks") or []
    if not all_services:
        checks = checks[:1]
    addr = node.get("address")
    out = []
    for c in checks:
        out.append({
            "service": c.get("service", "tcp"),
            "host": c.get("host") or addr,
            "port": int(c["port"]),
        })
    return out


def classify(expect: str, reachable: bool) -> str:
    """Map (expect, reachable) → a status key in _STYLE."""
    if reachable:
        return "up-opt" if expect == "optional" else "up"
    return "offline" if expect == "optional" else "down"


def is_failure(expect: str, reachable: bool) -> bool:
    """A failure = an expect='up' node that is not reachable. Optional nodes never fail."""
    return expect == "up" and not reachable


# --- the one impure part ----------------------------------------------------

def probe(host: str, port: int, timeout: float) -> tuple[bool, float | None, str | None]:
    """TCP-connect to host:port. Returns (reachable, latency_ms, error)."""
    import time
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.perf_counter() - start) * 1000.0
            return True, ms, None
    except (OSError, socket.timeout) as e:
        return False, None, type(e).__name__


# --- orchestration ----------------------------------------------------------

def sweep(nodes: list[dict], all_services: bool, timeout: float, prober=probe) -> list[dict]:
    """Probe every node's targets in parallel. Returns one result row per node.

    prober is injectable so tests can run without a network.
    """
    def _one(node: dict) -> dict:
        expect = node.get("expect", "up")
        targets = resolve_targets(node, all_services)
        probes = []
        for t in targets:
            ok, ms, err = prober(t["host"], t["port"], timeout)
            probes.append({**t, "reachable": ok, "latency_ms": ms, "error": err})
        # a node is "reachable" if its PRIMARY (first) target answers
        reachable = probes[0]["reachable"] if probes else False
        return {
            "name": node.get("name", "?"),
            "kind": node.get("kind", ""),
            "expect": expect,
            "reachable": reachable,
            "status": classify(expect, reachable) if probes else "down",
            "purpose": node.get("purpose", ""),
            "note": node.get("note"),
            "probes": probes,
        }

    if not nodes:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(nodes))) as ex:
        return list(ex.map(_one, nodes))


# --- rendering --------------------------------------------------------------

def _c(text: str, color: str, use_color: bool) -> str:
    return f"{color}{text}{_RESET}" if use_color else text


def render_table(rows: list[dict], use_color: bool, all_services: bool) -> str:
    name_w = max((len(r["name"]) for r in rows), default=4)
    kind_w = max((len(r["kind"]) for r in rows), default=4)
    lines = []
    for r in rows:
        label, color = _STYLE[r["status"]]
        p = r["probes"][0] if r["probes"] else None
        if p:
            tgt = f"{p['host']}:{p['port']}"
            svc = p["service"]
            lat = f"{p['latency_ms']:.0f}ms" if p["latency_ms"] is not None else "-"
        else:
            tgt, svc, lat = "(no check)", "-", "-"
        line = (f"  {_c(label, color, use_color)}  "
                f"{r['name']:<{name_w}}  {r['kind']:<{kind_w}}  "
                f"{tgt:<28} {svc:<10} {lat:>7}   {r['purpose']}")
        lines.append(line)
        if all_services and len(r["probes"]) > 1:
            for extra in r["probes"][1:]:
                ok = extra["reachable"]
                m = _c("ok  ", _STYLE["up"][1], use_color) if ok else _c("FAIL", _STYLE["down"][1], use_color)
                el = f"{extra['latency_ms']:.0f}ms" if extra["latency_ms"] is not None else extra["error"] or "-"
                lines.append(f"        {m}  {extra['host']}:{extra['port']:<5} {extra['service']:<10} {el}")
        if r["status"] == "down" and r.get("note"):
            lines.append(f"        {_c('note:', _STYLE['down'][1], use_color)} {r['note']}")
    return "\n".join(lines)


def summarize(rows: list[dict]) -> dict:
    up = sum(1 for r in rows if r["status"] in ("up", "up-opt"))
    down = sum(1 for r in rows if r["status"] == "down")
    offline = sum(1 for r in rows if r["status"] == "offline")
    return {"total": len(rows), "up": up, "down": down, "offline": offline}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Fleet reachability sweep.")
    ap.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    ap.add_argument("--node", help="probe only this node (by name)")
    ap.add_argument("--all-services", action="store_true", help="probe every declared service, not just primary")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)

    try:
        inv = load_inventory(a.inventory)
    except FileNotFoundError:
        print(f"inventory not found: {a.inventory}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as e:
        print(f"malformed inventory TOML: {e}", file=sys.stderr)
        return 2

    nodes = inv["nodes"]
    if a.node:
        nodes = [n for n in nodes if n.get("name") == a.node]
        if not nodes:
            print(f"no node named {a.node!r} in inventory", file=sys.stderr)
            return 2

    rows = sweep(nodes, a.all_services, a.timeout)
    summary = summarize(rows)

    if a.json:
        print(json.dumps({"summary": summary, "nodes": rows}, indent=2))
    else:
        use_color = not a.no_color and sys.stdout.isatty()
        print(f"\nfleet-ping  ({inv['meta'].get('tailnet', '?')}, updated {inv['meta'].get('updated', '?')})\n")
        print(render_table(rows, use_color, a.all_services))
        s = summary
        print(f"\n  {s['up']} up | {s['down']} down | {s['offline']} offline  "
              f"({s['total']} nodes)\n")

    # exit 1 if any expect=up node is down (usable as a health gate)
    return 1 if any(is_failure(r["expect"], r["reachable"]) for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
