"""Ticketed external execution (steppewise-consul M3 + M4 fence).

An external worker asks HEARTH for a ticket (requirements, never a seat name), calls the
ticketed seat itself, and commits output + usage back. HEARTH stays the owner of admission,
seat choice, leases and the canonical record:

  acquire_or_get(key, family, messages, max_tokens) -> ticket   (same key -> same job, current ticket)
  renew(ticket)                                     -> new expiry or refusal
  commit(ticket, output_text, output_sha256, usage, route_used) -> accepted or refusal
  fail(ticket, reason)                              -> terminal failure

Jobs are ``execution_mode: external`` so the executor and boot recovery never touch them
(service.py _run_job / recover_pending). Leases have no holder pid, so they survive a gateway
restart and expire only by time or terminal job (recovery policy: lease rebuilt from durable
state; holder renews within expiry or the attempt expires and the kernel asks for a new one).

Fence (M4): every new attempt on a job increments ``fence_epoch``; the ticket carries it and the
seat ``generation`` bound at admission (discovery). A commit is accepted only if the signature,
epoch, attempt, lease, route, output hash and (for discovery-bound seats) the seat's current
Consul generation all still match. Refusal codes: bad_signature, stale_epoch, lease_expired,
route_mismatch, hash_mismatch, generation_changed, not_active.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import replace
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from hearth.execution.coordination import CapacityUnavailable
from hearth.execution.ids import new_invocation_id, new_job_id, new_request_id
from hearth.scheduler.discovery import DiscoveryRefusal, shared_adapter
from hearth.toolsurface.backends import load_pool

LEASE_S = float(os.environ.get("HEARTH_TICKET_LEASE_S", "120"))
SIGNED = ("job_id", "attempt_id", "lease_id", "fence_epoch", "backend", "route", "model",
          "generation", "max_tokens")


class TicketRefusal(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _key() -> bytes:
    path = Path(os.environ.get("HEARTH_TICKET_KEY_FILE",
                               str(Path(os.environ.get("HEARTH_EXECUTION_DIR", "var/execution")) / "ticket.key")))
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    return path.read_text(encoding="utf-8").strip().encode()


def _sign(fields: Mapping[str, Any]) -> str:
    signed = {k: fields.get(k) for k in SIGNED}
    if "request_sha256" in fields:
        signed["request_sha256"] = fields["request_sha256"]
    body = json.dumps(signed, sort_keys=True).encode()
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


class TicketService:
    def __init__(self, service: Any) -> None:
        self.svc = service

    # ---- helpers ----
    @contextmanager
    def _fence(self):
        """Durable cross-process job fence: the ledger's own thread + interprocess append lock
        (the pattern import_receipt uses; append() is reentrant under it). Every ticket operation
        verifies and publishes inside it, so replacement, renew, commit and fail never interleave
        across threads or gateway processes (Codex M5 blocker 1)."""
        ledger = self.svc.ledger
        with ledger._lock, ledger._interprocess_append_lock():
            if ledger._projection_is_stale():
                ledger.rebuild()
            yield
    def _attempts(self, state: Mapping[str, Any]) -> list[dict]:
        return list(state.get("invocations") or [])

    def _ticket(self, state: Mapping[str, Any], inv: Mapping[str, Any]) -> dict[str, Any]:
        t = dict(state["desired"]["ticket"])
        obs = inv.get("observed") or inv
        t.update(job_id=state["job_id"], attempt_id=inv["invocation_id"], lease_id=obs.get("lease_id"), fence_epoch=obs.get("fence_epoch"))
        t["expires_at"] = self.svc.leases.expires_at(t["lease_id"]) if t.get("lease_id") else None
        t["signature"] = _sign(t)
        return t

    def _admit_ticket(self, pool, route, payload_bytes, max_tokens):
        """Balance enforced discovery tickets without weakening ordinary admission.

        Called under the route and ledger fences. Singleton pools retain all family,
        discovery, occupancy and sizing checks; route.backend is never synthesized.
        """
        if (route.recommendation or {}).get("discovery") != "enforce":
            return self.svc._admit_select(pool, route, payload_bytes,
                                         max_tokens=max_tokens, enforce_bytes=payload_bytes)
        from hearth.execution.service import ExecutionServiceError
        from hearth.toolsurface.occupancy import check_occupancy, routing_reservation_context
        ranked = sorted(pool.backends,
                        key=lambda b: (self.svc.leases.active_count("provider:" + b.name), b.name))
        candidates, refusals = [], []
        for backend in ranked:
            if route.backend is not None and backend.name != route.backend:
                continue
            if self.svc.leases.active_count("provider:" + backend.name) >= self.svc._parallel_slots(backend):
                refusals.append(backend.name + ":capacity_unavailable")
                continue
            candidates.append(backend)
        # Never turn a busy/unknown tag candidate into a synthetic default while
        # another available seat can pass admission. Probe with the same lease
        # reservation context normal selection uses, then rank available seats.
        with routing_reservation_context(leases=self.svc.leases, ledger=self.svc.ledger):
            for backend in candidates:
                occupancy = check_occupancy(backend.name)
                if route.backend is None and (not isinstance(occupancy, dict)
                                               or occupancy.get("occupancy") != "available"):
                    continue
                candidate_pool = replace(pool, backends=(backend,), default=backend.name)
                try:
                    return self.svc._admit_select(candidate_pool, route, payload_bytes,
                                                 max_tokens=max_tokens, enforce_bytes=payload_bytes)
                except ExecutionServiceError as exc:
                    refusals.append(backend.name + ":" + str(exc))
        # Preserve the authored default fallback when no available candidate can
        # pass. Keep capacity-full seats out before any canonical acceptance.
        if candidates:
            names = {b.name for b in candidates}
            fallback_pool = replace(pool, backends=tuple(b for b in pool.backends if b.name in names),
                                    default=pool.default if pool.default in names else sorted(names)[0])
            try:
                return self.svc._admit_select(fallback_pool, route, payload_bytes,
                                             max_tokens=max_tokens, enforce_bytes=payload_bytes)
            except ExecutionServiceError as exc:
                refusals.append(str(exc))
        raise TicketRefusal("no_eligible_seat", "; ".join(refusals)[:1200])

    def _new_attempt(self, state: Mapping[str, Any], epoch: int) -> dict[str, Any]:
        t = state["desired"]["ticket"]
        provider = load_pool().by_name(t["backend"])
        inv_id = new_invocation_id()
        try:
            lease_id = self.svc.leases.acquire(scope=f"provider:{t['backend']}", job_id=state["job_id"],
                                               invocation_id=inv_id, limit=self.svc._parallel_slots(provider),
                                               ttl_seconds=LEASE_S, holder_pid=None)
        except CapacityUnavailable as exc:
            raise TicketRefusal("capacity_unavailable", str(exc)) from exc
        observed = {"provider": t["backend"], "model": t["model"], "routed_by": t["routed_by"], "lease_id": lease_id,
                    "fence_epoch": epoch, "generation": t.get("generation"), "execution": "ticket", "occupancy": "leased"}
        try:
            if epoch == 1:
                self.svc._append("job.dispatched", state, observed=observed)
            self.svc._append("invocation.started", state, invocation_id=inv_id, observed=observed)
        except Exception:
            self.svc.leases.release(lease_id)   # never leak a lease on a failed append
            raise
        return self.svc.ledger.get_job(state["job_id"])

    def _finish(self, state: Mapping[str, Any], cur: Mapping[str, Any]) -> dict[str, Any]:
        """Resume a half-published commit: the attempt succeeded but the job isn't terminal yet."""
        obs = cur.get("observed") or cur
        art_id = obs.get("result_artifact_id")
        self.svc._append("job.succeeded", state, observed={"summary": "resumed terminal publication",
                                                           "result_artifact_id": art_id})
        if obs.get("lease_id"):
            self.svc.leases.release(obs["lease_id"])
        return {"accepted": True, "job_id": state["job_id"], "result_artifact_id": art_id, "resumed": True}

    # ---- operations ----
    def acquire_or_get(self, *, principal: Mapping[str, Any], caller_id: str, idempotency_key: str,
                       task_family: str, messages: list, max_tokens: int, response_schema: Optional[dict] = None,
                       provenance: Optional[dict] = None, experiment_pin: Optional[str] = None) -> dict[str, Any]:
        with self.svc._route_lock, self._fence():
            return self._acquire_or_get(principal=principal, caller_id=caller_id, idempotency_key=idempotency_key,
                                        task_family=task_family, messages=messages, max_tokens=max_tokens, response_schema=response_schema,
                                        provenance=provenance, experiment_pin=experiment_pin)

    def _acquire_or_get(self, *, principal, caller_id, idempotency_key, task_family, messages, max_tokens,
                        response_schema=None, provenance=None, experiment_pin=None):
        svc = self.svc
        if not isinstance(messages, list) or not messages or not isinstance(max_tokens, int) or max_tokens < 1:
            raise TicketRefusal("invalid_request")
        if task_family == "consul_verify" and max_tokens > 2048:
            raise TicketRefusal("invalid_budget")
        if response_schema is not None and not isinstance(response_schema, dict):
            raise TicketRefusal("invalid_schema")
        if provenance is not None and not isinstance(provenance, dict):
            raise TicketRefusal("invalid_provenance")
        if experiment_pin is not None and (not isinstance(experiment_pin, str) or not experiment_pin or not provenance or not provenance.get("experiment_id")):
            raise TicketRefusal("pin_requires_experiment")
        request = {"task_family": task_family, "messages": messages, "max_tokens": max_tokens,
                   "response_schema": response_schema, "provenance": provenance, "experiment_pin": experiment_pin}
        request_sha = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        prior = svc.ledger.find_by_idempotency(idempotency_key)
        if prior is not None:
            state = svc.ledger.get_job(prior["job_id"])
            # Only a ticket job owned by this caller may be retrieved; anything else is refused
            # BEFORE any event is appended (Codex M5 blocker 3).
            if not (state.get("desired") or {}).get("ticket") or (state.get("source") or {}).get("transport") != "ticket" \
                    or (state.get("principal") or {}).get("id") != principal.get("id"):
                raise TicketRefusal("idempotency_conflict", "key belongs to a job this caller can't retrieve as a ticket")
            old_sha = (state.get("desired") or {}).get("ticket", {}).get("request_sha256")
            if old_sha is None or old_sha != request_sha:
                raise TicketRefusal("idempotency_conflict", "request differs from admitted request")
            if state["status"] in ("succeeded", "failed", "cancelled", "expired", "rejected"):
                return {"job_id": state["job_id"], "status": state["status"], "ticket": None}
            attempts = self._attempts(state)
            cur = attempts[-1] if attempts else None
            if cur and cur.get("status") == "failed" and (cur.get("observed") or cur).get("terminal_intent") == "explicit_fail":
                # half-published explicit fail: finish job.failed, never a replacement attempt (blocker 2)
                svc._append("job.failed", state, reason=cur.get("reason") or "failed")
                svc.leases.release((cur.get("observed") or cur).get("lease_id"))
                return {"job_id": state["job_id"], "status": "failed", "ticket": None, "resumed": True}
            if cur and cur.get("status") == "succeeded":   # half-published commit: finish it, never a new attempt
                done = self._finish(state, cur)
                return {"job_id": state["job_id"], "status": "succeeded", "ticket": None, "resumed": done.get("resumed")}
            lease_ok = cur and cur.get("status") == "running" and (svc.leases.expires_at((cur.get("observed") or cur).get("lease_id")) or 0) > time.time()
            if not lease_ok:   # previous attempt expired: fence it and open a new one (epoch + 1)
                if cur and cur.get("status") == "running":
                    svc._append("invocation.failed", state, invocation_id=cur["invocation_id"], reason="lease_expired", observed={"terminal_intent": "lease_expired"})
                    state = svc.ledger.get_job(state["job_id"])
                state = self._new_attempt(state, len(attempts) + 1)
                cur = self._attempts(state)[-1]
            return {"job_id": state["job_id"], "status": state["status"], "ticket": self._ticket(state, cur)}

        operation = svc.operations.get("inference.deliberate")
        body = json.dumps({"messages": messages, "response_schema": response_schema}, allow_nan=False).encode("utf-8")
        route = svc._family_route(operation, {"task_family": task_family}, len(body))
        if route.recommendation and route.recommendation.get("refused"):
            raise TicketRefusal("policy_refusal", route.recommendation.get("refusal") or "")
        if experiment_pin is not None:
            pool = load_pool()
            pinned = next((b for b in pool.backends if b.name == experiment_pin), None)
            rec = route.recommendation or {}
            models = rec.get("discovery_models") or [rec.get("model_id")]
            if rec.get("discovery") != "enforce" or pinned is None or not set(models).intersection(pinned.models) \
                    or route.backend not in (None, experiment_pin) or route.tags and not set(route.tags).intersection(pinned.tags or ()):
                raise TicketRefusal("pin_not_eligible")
            route = route._replace(backend=experiment_pin)
        with svc._route_lock:
            provider, routed_by, binding = self._admit_ticket(load_pool(), route, len(body), max_tokens)
        model = route.resolve_model(provider)
        ticket = {"backend": provider.name, "route": (binding or {}).get("route") or provider.endpoint.rstrip("/"),
                  "model": model, "generation": (binding or {}).get("generation"),
                  "instance_id": (binding or {}).get("instance_id"), "max_tokens": int(max_tokens),
                  "routed_by": route.label(routed_by), "task_family": task_family, "request_sha256": request_sha}
        initial = {"request_id": new_request_id(), "job_id": new_job_id()}
        artifact = svc.artifacts.put(json.dumps(messages).encode("utf-8"), media_type="application/json; charset=utf-8",
                                     filename=f"{initial['job_id']}-messages.json")
        desired = {"operation": "inference.deliberate", "arguments": {"task_family": task_family, "thinking": False},
                   "input_artifact": artifact, "policy": {"max_tokens": int(max_tokens), "deadline_s": int(LEASE_S), "priority": 0},
                   "idempotency_key": idempotency_key, "ticket": ticket, "external_request": request, **({"discovery": binding} if binding else {})}
        source = {"transport": "ticket", "adapter": caller_id, "execution_mode": "external"}
        svc._append("request.accepted", initial, principal=dict(principal), source=source,
                    operation="inference.deliberate", desired=desired, observed={"provider": provider.name})
        svc._append("artifact.recorded", initial, artifacts=[{**artifact, "role": "input"}])
        svc._append("job.queued", initial)
        state = svc.ledger.get_job(initial["job_id"])
        try:
            state = self._new_attempt(state, 1)
        except TicketRefusal as exc:
            # A lease acquired outside this route fence can win after preflight.
            # Acceptance is durable: publish a terminal result rather than orphan it.
            svc._append("job.failed", state, reason=exc.code)
            raise
        return {"job_id": state["job_id"], "status": state["status"], "ticket": self._ticket(state, self._attempts(state)[-1])}

    def _verify(self, ticket: Mapping[str, Any], allow_succeeded: bool = False) -> tuple[dict, dict]:
        if not hmac.compare_digest(str(ticket.get("signature", "")), _sign(ticket)):
            raise TicketRefusal("bad_signature")
        state = self.svc.ledger.get_job(ticket["job_id"])
        if state is None or state["status"] in ("succeeded", "failed", "cancelled", "expired", "rejected"):
            raise TicketRefusal("not_active", f"job status {state and state['status']}")
        attempts = self._attempts(state)
        cur = attempts[-1]
        ok_status = ("running", "succeeded") if allow_succeeded else ("running",)
        if int(ticket.get("fence_epoch") or 0) != len(attempts) or ticket.get("attempt_id") != cur["invocation_id"] \
                or cur.get("status") not in ok_status:
            raise TicketRefusal("stale_epoch", f"ticket epoch {ticket.get('fence_epoch')} != current {len(attempts)}")
        exp = self.svc.leases.expires_at(ticket.get("lease_id"))
        if cur.get("status") == "running" and (exp is None or exp <= time.time()):
            raise TicketRefusal("lease_expired")
        return state, cur

    def renew(self, ticket: Mapping[str, Any]) -> dict[str, Any]:
        with self.svc._route_lock, self._fence():
            return self._renew(ticket)

    def _renew(self, ticket: Mapping[str, Any]) -> dict[str, Any]:
        self._verify(ticket)
        if not self.svc.leases.renew(ticket["lease_id"], ttl_seconds=LEASE_S):
            raise TicketRefusal("lease_expired")
        return {"lease_id": ticket["lease_id"], "expires_at": self.svc.leases.expires_at(ticket["lease_id"])}

    def commit(self, ticket: Mapping[str, Any], *, output_text: str, output_sha256: str,
               usage: Mapping[str, Any], route_used: str) -> dict[str, Any]:
        with self.svc._route_lock, self._fence():   # verify and publish atomically w.r.t. replacement/renew/fail (blocker 1)
            return self._commit(ticket, output_text=output_text, output_sha256=output_sha256, usage=usage, route_used=route_used)

    def _commit(self, ticket, *, output_text, output_sha256, usage, route_used):
        state, cur = self._verify(ticket, allow_succeeded=True)
        if cur.get("status") == "succeeded":   # resume after an interrupted publication (blocker 2)
            return self._finish(state, cur)
        if (route_used or "").rstrip("/") != ticket["route"]:
            raise TicketRefusal("route_mismatch", f"{route_used} != {ticket['route']}")
        data = output_text.encode("utf-8")
        if hashlib.sha256(data).hexdigest() != output_sha256:
            raise TicketRefusal("hash_mismatch")
        binding = state["desired"].get("discovery")
        if binding and ticket.get("generation"):   # strict generation fence (M4 default)
            adapter = shared_adapter()
            try:
                if adapter is None:
                    raise DiscoveryRefusal("discovery_unavailable", "not configured")
                adapter.recheck(binding["instance_id"], ticket["generation"], binding.get("route"))
            except DiscoveryRefusal as exc:
                raise TicketRefusal("generation_changed", str(exc)) from exc
        art = self.svc.artifacts.put(data, filename=f"{state['job_id']}-result.txt")
        # Durable order (blocker 2): the result artifact is recorded first, then the invocation closes
        # carrying result_artifact_id, then the job; any prefix can be resumed by a retry.
        self.svc._append("artifact.recorded", state, invocation_id=cur["invocation_id"], artifacts=[{**art, "role": "result"}])
        observed = {"result_artifact_id": art["artifact_id"], "backend": ticket["backend"], "model": ticket["model"], "routed_by": ticket["routed_by"],
                    "max_tokens": ticket["max_tokens"], "fence_epoch": ticket["fence_epoch"], "execution": "ticket",
                    "thinking": usage.get("thinking") if type(usage.get("thinking")) is bool else None,
                    "thinking_source": "caller-wire-setting",
                    **{k: usage.get(k) for k in ("tokens_in", "tokens_out", "finish_reason", "duration_ms",
                                                  "first_content_ms", "t_send", "t_first_byte", "t_done")}}
        self.svc._append("invocation.succeeded", state, invocation_id=cur["invocation_id"], observed=observed)
        self.svc._append("job.succeeded", state, observed={"summary": output_text[:280], "result_artifact_id": art["artifact_id"]})
        self.svc.leases.release(ticket["lease_id"])
        return {"accepted": True, "job_id": state["job_id"], "result_artifact_id": art["artifact_id"]}

    def fail(self, ticket: Mapping[str, Any], reason: str) -> dict[str, Any]:
        with self.svc._route_lock, self._fence():
            return self._fail(ticket, reason)

    def _fail(self, ticket: Mapping[str, Any], reason: str) -> dict[str, Any]:
        if not hmac.compare_digest(str(ticket.get("signature", "")), _sign(ticket)):
            raise TicketRefusal("bad_signature")
        state = self.svc.ledger.get_job(ticket["job_id"])
        cur = (self._attempts(state) or [{}])[-1] if state else {}
        if state and state["status"] not in ("succeeded", "failed", "cancelled", "expired", "rejected") \
                and cur.get("invocation_id") == ticket.get("attempt_id") and cur.get("status") == "failed" \
                and (cur.get("observed") or cur).get("terminal_intent") == "explicit_fail":   # resume an interrupted explicit fail (blocker 2)
            self.svc._append("job.failed", state, reason=cur.get("reason") or reason[:500])
            self.svc.leases.release(ticket["lease_id"])
            return {"failed": True, "job_id": state["job_id"], "resumed": True}
        state, cur = self._verify(ticket)
        self.svc._append("invocation.failed", state, invocation_id=cur["invocation_id"], reason=reason[:500], observed={"terminal_intent": "explicit_fail"})
        self.svc._append("job.failed", state, reason=reason[:500])
        self.svc.leases.release(ticket["lease_id"])
        return {"failed": True, "job_id": state["job_id"]}
