"""Consul-observed seat health for opt-in family admission (steppewise-consul M2).

HEARTH stays the owner of placement. This module only *observes*: one daemon thread holds
a blocking query on Consul's ``/v1/health/service/hearth-seat`` and keeps an in-memory
snapshot; admission reads the snapshot and never makes an HTTP call per request.

Fail closed, visibly (only for families whose policy is ``enforce``):
  discovery_unavailable   not configured, never fetched, or no usable snapshot
  discovery_stale         snapshot older than the freshness bound
  discovery_no_leader     Consul reports no known leader
  discovery_unauthorized  results were filtered by ACLs (never read as "zero seats")

There is no fallback to authored TOML for an ``enforce`` family; switching a family back
to ``off`` is a recorded operator action. Credentials are read from a file and never
logged or echoed. Redirects are refused.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SERVICE = "hearth-seat"
MODES = ("off", "shadow", "enforce")


class DiscoveryRefusal(Exception):
    """A discovery decision that refuses admission; ``code`` is the stable reason.

    ``facts`` carries whatever the snapshot could still tell (source, index, age, trust
    status), so a shadow record keeps its evidence even when the snapshot is untrusted.
    """

    def __init__(self, code: str, detail: str, facts: Optional[dict] = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.facts = facts or {}


@dataclass(frozen=True)
class Snapshot:
    consul_index: Optional[int] = None
    fetched_at: Optional[float] = None  # time.monotonic() of the last successful read
    known_leader: Optional[bool] = None
    last_contact_ms: Optional[int] = None
    filtered_by_acl: bool = False
    error: Optional[str] = None
    instances: tuple = field(default_factory=tuple)


def _instances(rows: list) -> tuple:
    out = []
    for row in rows:
        svc = row.get("Service") or {}
        meta = svc.get("Meta") or {}
        # Every check Consul returns for this instance counts, node-level ones too
        # (serfHealth, _node_maintenance have an empty ServiceID): a node in
        # maintenance or failing gossip must not admit work (Codex M2 B1).
        checks = tuple(
            {"name": c.get("Name") or c.get("CheckID"), "status": c.get("Status"),
             "output": (c.get("Output") or "")[:300], "scope": "service" if c.get("ServiceID") else "node"}
            for c in row.get("Checks") or []
        )
        try:
            max_model_len = int(meta.get("max_model_len")) if meta.get("max_model_len") else None
        except (TypeError, ValueError):
            max_model_len = None
        out.append({
            "id": svc.get("ID"),
            "seat_id": meta.get("seat_id") or str(svc.get("ID", "")).split("@", 1)[0],
            "generation": meta.get("generation"),
            "route": (meta.get("route") or "").rstrip("/"),
            "wire_model_id": meta.get("wire_model_id"),
            "alias": meta.get("alias"),
            "max_model_len": max_model_len,
            "checks": checks,
            "passing": bool(checks) and all(c["status"] == "passing" for c in checks),
        })
    return tuple(out)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - refuse every redirect
        return None


class DiscoveryAdapter:
    """Snapshot of Consul health for ``hearth-seat``, refreshed by a blocking-query thread."""

    def __init__(self, consul_addr: str, token_file: str, freshness_s: float = 30.0,
                 wait_s: int = 10, min_interval_s: float = 1.0) -> None:
        self.addr = consul_addr.rstrip("/")
        self.token_file = token_file
        self.freshness_s = float(freshness_s)
        if not math.isfinite(self.freshness_s) or self.freshness_s <= 0:
            raise ValueError(f"freshness_s must be finite and > 0, got {freshness_s!r}")
        self.wait_s = int(wait_s)
        self.min_interval_s = float(min_interval_s)
        self._lock = threading.Lock()
        self._snapshot = Snapshot()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._opener = urllib.request.build_opener(_NoRedirect())

    # ---- construction from the environment ----
    @classmethod
    def from_env(cls) -> Optional["DiscoveryAdapter"]:
        addr = os.environ.get("HEARTH_DISCOVERY_CONSUL", "").strip()
        token_file = os.environ.get("HEARTH_DISCOVERY_TOKEN_FILE", "").strip()
        if not addr or not token_file:
            return None
        return cls(addr, token_file, float(os.environ.get("HEARTH_DISCOVERY_FRESHNESS_S", "30")))

    # ---- watcher ----
    def start(self) -> "DiscoveryAdapter":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="hearth-discovery", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def _set(self, snap: Snapshot) -> None:
        with self._lock:
            self._snapshot = snap

    def _record_error(self, error: str) -> None:
        with self._lock:
            old = self._snapshot
            self._snapshot = Snapshot(old.consul_index, old.fetched_at, old.known_leader, old.last_contact_ms,
                                      old.filtered_by_acl, error, old.instances)

    def fetch_once(self, index: int) -> int:
        """One (blocking) read. Returns the index to send next; raises on transport/HTTP error."""
        # Path.read_text closes the file; a bare open() here would leak one fd per blocking
        # query (the M0 failure class, found by this module's own tests).
        token = Path(self.token_file).read_text(encoding="utf-8").strip()
        query = f"?index={index}&wait={self.wait_s}s" if index >= 1 else ""
        req = urllib.request.Request(f"{self.addr}/v1/health/service/{SERVICE}{query}",
                                     headers={"X-Consul-Token": token})
        with self._opener.open(req, timeout=self.wait_s + self.wait_s / 16 + 5) as resp:
            if resp.status != 200:
                raise RuntimeError(f"http {resp.status}")
            rows = json.loads(resp.read())
            headers = resp.headers
        new_index = int(headers.get("X-Consul-Index") or 0)
        leader = headers.get("X-Consul-KnownLeader")
        contact = headers.get("X-Consul-LastContact")
        self._set(Snapshot(
            consul_index=new_index,
            fetched_at=time.monotonic(),
            known_leader=None if leader is None else leader.lower() == "true",
            last_contact_ms=int(contact) if contact and contact.isdigit() else None,
            filtered_by_acl=(headers.get("X-Consul-Results-Filtered-By-ACLs") or "").lower() == "true",
            error=None,
            instances=_instances(rows if isinstance(rows, list) else []),
        ))
        # Blocking-query rules (Consul docs, 2.1.0): reset when the index goes backwards,
        # never send an index below 1 after the first read.
        if new_index < index:
            return 0
        return max(1, new_index)

    def _run(self) -> None:
        index, backoff = 0, 1.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                index = self.fetch_once(index)
                backoff = 1.0
            except urllib.error.HTTPError as exc:
                exc.close()  # release the response socket on 4xx/5xx
                self._record_error(f"http {exc.code}")
                index = 0
                self._stop.wait(backoff)
                backoff = min(10.0, backoff * 2)
                continue
            except Exception as exc:  # noqa: BLE001 - class name only; no payload or token
                self._record_error(type(exc).__name__)
                index = 0
                self._stop.wait(backoff)
                backoff = min(10.0, backoff * 2)
                continue
            elapsed = time.monotonic() - started
            if elapsed < self.min_interval_s:
                self._stop.wait(self.min_interval_s - elapsed)

    # ---- decisions ----
    def freshness(self, snap: Optional[Snapshot] = None) -> dict[str, Any]:
        """Raise DiscoveryRefusal if the snapshot can't be trusted; else return its facts."""
        snap = snap or self.snapshot()
        if snap.fetched_at is None:
            raise DiscoveryRefusal("discovery_unavailable", f"no snapshot yet ({snap.error or 'not fetched'})",
                                   {"discovery_source": "consul", "trusted": False, "last_error": snap.error})
        age_ms = int((time.monotonic() - snap.fetched_at) * 1000)
        facts = {"discovery_source": "consul", "consul_index": snap.consul_index, "snapshot_age_ms": age_ms,
                 "known_leader": snap.known_leader, "filtered_by_acl": snap.filtered_by_acl,
                 "instances_seen": [i["id"] for i in snap.instances]}
        untrusted = {**facts, "trusted": False}
        if snap.filtered_by_acl:
            raise DiscoveryRefusal("discovery_unauthorized", "Consul filtered results by ACL; refusing to read that as zero seats", untrusted)
        if snap.known_leader is not True:   # missing or false header: no affirmative evidence (Codex M2 M4)
            raise DiscoveryRefusal("discovery_no_leader",
                                   f"X-Consul-KnownLeader {snap.known_leader!r}; snapshot age {age_ms} ms", untrusted)
        if age_ms > self.freshness_s * 1000:
            raise DiscoveryRefusal("discovery_stale", f"snapshot age {age_ms} ms > {int(self.freshness_s * 1000)} ms"
                                   + (f"; last error {snap.error}" if snap.error else ""), untrusted)
        if snap.last_contact_ms is None or snap.last_contact_ms < 0 or snap.last_contact_ms > 30000:
            raise DiscoveryRefusal("discovery_stale_contact", "Consul leader contact missing or exceeds 30000 ms", untrusted)
        return {k: facts[k] for k in ("discovery_source", "consul_index", "snapshot_age_ms")}

    @staticmethod
    def backends_for(instance: dict, pool: Any) -> list[str]:
        """Pool backends served by this instance: same endpoint AND a matching model id."""
        names = {instance.get("wire_model_id"), instance.get("alias")} - {None}
        return [b.name for b in pool.backends
                if b.endpoint.rstrip("/") == instance["route"] and names.intersection(b.models)]

    def evaluate(self, pool: Any, candidates: Optional[list[str]] = None,
                 need_tokens: Optional[int] = None) -> dict[str, Any]:
        """Eligible backends among ``candidates`` (default: all pool backends), with reasons.

        Eligible = mapped to an instance whose every check passes and whose advertised
        ``max_model_len`` (when Consul carries one) holds ``need_tokens``.
        """
        snap = self.snapshot()
        facts = self.freshness(snap)
        wanted = list(candidates) if candidates is not None else [b.name for b in pool.backends]
        eligible: dict[str, dict] = {}
        refused: dict[str, str] = {}
        for inst in snap.instances:
            for name in self.backends_for(inst, pool):
                if name not in wanted:
                    continue
                mml = inst.get("max_model_len")
                if inst["passing"] and need_tokens is not None and not (isinstance(mml, int) and mml > 0):
                    # No valid positive capacity in Consul: capability unknown, not "unlimited" (Codex M2 R2-M3).
                    refused.setdefault(name, f"{inst['id']} capability_unknown: max_model_len {mml!r}")
                elif inst["passing"] and need_tokens is not None and need_tokens > mml:
                    refused.setdefault(name, f"{inst['id']} max_model_len {mml} < need {need_tokens} tokens")
                elif inst["passing"]:
                    eligible.setdefault(name, inst)
                else:
                    bad = next((c for c in inst["checks"] if c["status"] != "passing"), None)
                    refused.setdefault(name, f"{inst['id']} check {bad['name'] if bad else '?'}="
                                             f"{bad['status'] if bad else 'none'}: {bad['output'][:160] if bad else 'no checks'}")
        for name in wanted:
            if name not in eligible and name not in refused:
                refused[name] = "no hearth-seat instance in Consul for this backend"
        for name in eligible:
            refused.pop(name, None)
        return {**facts, "eligible": eligible, "refused": refused}

    def recheck(self, instance_id: str, generation: Optional[str], route: Optional[str] = None) -> dict[str, Any]:
        """Dispatch-time check of a bound instance; raises seat_lost_before_dispatch.

        Returns the freshness facts plus the current ``instance`` so the caller can
        verify endpoint and model against the binding (Codex M2 B3).
        """
        snap = self.snapshot()
        try:
            facts = self.freshness(snap)
        except DiscoveryRefusal as exc:
            raise DiscoveryRefusal("seat_lost_before_dispatch", f"{exc.code}: {exc.detail}") from exc
        inst = next((i for i in snap.instances if i["id"] == instance_id), None)
        if inst is None:
            raise DiscoveryRefusal("seat_lost_before_dispatch", f"instance {instance_id} no longer registered")
        if inst.get("generation") != generation:
            raise DiscoveryRefusal("seat_lost_before_dispatch",
                                   f"generation changed bound={generation} now={inst.get('generation')}")
        if route is not None and inst.get("route") != route.rstrip("/"):
            raise DiscoveryRefusal("seat_lost_before_dispatch",
                                   f"instance route changed bound={route} now={inst.get('route')}")
        if not inst["passing"]:
            bad = next((c for c in inst["checks"] if c["status"] != "passing"), None)
            raise DiscoveryRefusal("seat_lost_before_dispatch",
                                   f"{instance_id} check {bad['name'] if bad else '?'} not passing")
        return {**facts, "instance": inst}


_ADAPTER: Optional[DiscoveryAdapter] = None
_ADAPTER_ERROR: Optional[str] = None
_ADAPTER_LOCK = threading.Lock()


def shared_adapter() -> Optional[DiscoveryAdapter]:
    """Process-wide adapter, started on first use when the environment configures one.

    Invalid configuration never raises here (off traffic must keep working): it is
    recorded in ``adapter_error()`` and the caller sees no adapter, so enforce refuses
    with discovery_unavailable naming the error (Codex R3-m5).
    """
    global _ADAPTER, _ADAPTER_ERROR
    with _ADAPTER_LOCK:
        if _ADAPTER is None:
            try:
                _ADAPTER = DiscoveryAdapter.from_env()
                _ADAPTER_ERROR = None
            except (TypeError, ValueError) as exc:
                _ADAPTER, _ADAPTER_ERROR = None, f"invalid discovery configuration: {exc}"
            if _ADAPTER is not None:
                _ADAPTER.start()
        return _ADAPTER


def adapter_error() -> Optional[str]:
    return _ADAPTER_ERROR
