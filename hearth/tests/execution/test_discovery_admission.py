"""steppewise-consul M2: Consul-gated admission for opt-in families (V1/V3/V4 shape, binding, refusals)."""
from __future__ import annotations

import os
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.operations import load_operations
from hearth.execution.service import ExecutionService, ExecutionServiceError
from hearth.scheduler.discovery import DiscoveryAdapter, Snapshot

BACKENDS = """
default = "seat-a"

[[backend]]
name = "seat-a"
endpoint = "http://127.0.0.1:9101"
api = "openai"
models = ["m1"]
tags = ["dense"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
context_bytes = 65536
deliberate_max_tokens = 4096

[[backend]]
name = "seat-b"
endpoint = "http://127.0.0.1:9102"
api = "openai"
models = ["m1"]
tags = ["dense"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
context_bytes = 65536
deliberate_max_tokens = 4096

[[backend]]
name = "seat-other"
endpoint = "http://127.0.0.1:9103"
api = "openai"
models = ["m2"]
tags = ["dense"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
context_bytes = 65536

[[backend]]
name = "seat-twin"
endpoint = "http://127.0.0.1:9101"
api = "openai"
models = ["m-twin"]
[backend.settings]
parallel_slots = 1
max_tokens = 99
context_bytes = 65536

[[backend]]
name = "seat-multi"
endpoint = "http://127.0.0.1:9105"
api = "openai"
models = ["m1", "m2"]
tags = ["dense"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
context_bytes = 65536

[[backend]]
name = "seat-alias"
endpoint = "http://127.0.0.1:9104"
api = "openai"
models = ["m1-facade"]
tags = ["dense"]
[backend.settings]
parallel_slots = 1
max_tokens = 512
context_bytes = 65536
"""

FAMILIES = """
contract = "routing-families.v1"
[family.consul_verify]
model_id = "m1"
tags = ["dense"]
discovery = "enforce"
evidence = "test"
reason = "test"
[family.consul_regime]
model_id = "m1"
tags = ["dense"]
discovery = "enforce"
evidence = "test"
reason = "test"
[family.consul_regime.regime]
procedure = "one_call"
thinking = false
max_tokens = 2048
evidence = "test"
[family.consul_alias]
model_id = "m1"
tags = ["dense"]
discovery = "enforce"
discovery_models = ["m1-facade"]
evidence = "test"
reason = "test"
[family.consul_shadow]
model_id = "m1"
tags = ["dense"]
discovery = "shadow"
evidence = "test"
reason = "test"
[family.plain]
model_id = "m1"
tags = ["dense"]
evidence = "test"
reason = "test"
[family.default]
model_id = "m1"
evidence = "e"
reason = "r"
"""


def _inst(seat: str, port: int, status: str = "passing", gen: str = "g1", model: str = "m1",
          max_model_len: int | None = 65536, node: str = "passing") -> dict:
    checks = ({"name": "engine", "status": status, "output": f"engine {status}", "scope": "service"},
              {"name": "_node_maintenance", "status": node, "output": f"node {node}", "scope": "node"})
    return {"id": f"{seat}@{gen}", "seat_id": seat, "generation": gen, "route": f"http://127.0.0.1:{port}",
            "wire_model_id": model, "alias": None, "max_model_len": max_model_len,
            "passing": all(c["status"] == "passing" for c in checks), "checks": checks}


class DiscoveryAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "backends.toml").write_text(BACKENDS, encoding="utf-8")
        (self.root / "families.toml").write_text(textwrap.dedent(FAMILIES), encoding="utf-8")
        self.env = patch.dict(os.environ, {"HEARTH_BACKENDS": str(self.root / "backends.toml"),
                                           "HEARTH_ROUTING_FAMILIES": str(self.root / "families.toml")})
        self.env.start()
        os.environ.pop("HEARTH_SIZER", None)
        self.adapter = DiscoveryAdapter("http://127.0.0.1:1", str(self.root / "unused.token"))
        self.patch_adapter = patch("hearth.execution.service.shared_adapter", lambda: self.adapter)
        self.patch_adapter.start()
        self.services: list[ExecutionService] = []
        self.principal = {"type": "irc_account", "id": "derek", "authenticated": True}
        self.source = {"transport": "irc", "adapter": "BotHerder"}

    def tearDown(self) -> None:
        for s in self.services:
            s.close()
        self.patch_adapter.stop()
        self.env.stop()
        self.tmp.cleanup()

    def snap(self, *instances, age_s: float = 0.0, leader: bool = True, filtered: bool = False) -> None:
        self.adapter._set(Snapshot(consul_index=42, fetched_at=time.monotonic() - age_s, known_leader=leader,
                                   filtered_by_acl=filtered, instances=tuple(instances)))

    def service(self, generate) -> ExecutionService:
        s = ExecutionService(ledger=ExecutionLedger(self.root / "ledger"), artifacts=ArtifactStore(self.root / "artifacts"),
                             leases=CapacityLeaseStore(self.root / "coordination.sqlite"), operations=load_operations(),
                             generate=generate, workers=2)
        self.services.append(s)
        return s

    def submit(self, service, family: str, **extra):
        return service.submit(operation_name="llm.chat", arguments={"prompt": "hello", "task_family": family, **extra},
                              principal=self.principal, source=self.source)

    def wait_final(self, service, job_id: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = service.get_job(job_id)
            if state and state["status"] in {"succeeded", "failed", "cancelled", "expired"}:
                return state
            time.sleep(0.01)
        self.fail("job did not finish")

    @staticmethod
    def ok(**kw):
        return {"ok": True, "text": "ok", "model": kw["model"], "backend": kw["backend"], "tokens_in": 1, "tokens_out": 1}

    # --- enforce ---
    def test_enforce_binds_at_admission_and_dispatch_uses_the_bound_seat(self) -> None:   # V1 shape
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102))
        calls = []
        svc = self.service(lambda **kw: calls.append(kw["backend"]) or self.ok(**kw))
        job = self.submit(svc, "consul_verify")
        state = self.wait_final(svc, job["job_id"])
        self.assertEqual("succeeded", state["status"])
        self.assertEqual(["seat-b"], calls)                       # bound == actual
        binding = state["desired"]["discovery"]
        self.assertEqual(("enforce", "seat-b", "seat-b@g1", "g1"),
                         (binding["mode"], binding["backend"], binding["instance_id"], binding["generation"]))
        self.assertEqual(["seat-b"], binding["eligible"])
        self.assertIn("engine=critical", binding["refused"]["seat-a"])
        dispatched = [e for e in svc.events(limit=50) if e["event_type"] == "job.dispatched"][0]
        self.assertEqual("seat-b", dispatched["observed"]["provider"])
        self.assertIn("discovery:bound:seat-b", dispatched["observed"]["routed_by"])
        self.assertIn("dispatch_check", dispatched["observed"]["discovery"])

    def test_no_eligible_seat_refuses_before_any_job(self) -> None:   # V3/V4 shape
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102, "critical"))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            self.submit(svc, "consul_verify")
        self.assertEqual([], svc.events(limit=10))

    def test_pin_must_be_eligible_under_enforce(self) -> None:
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^pin_not_eligible: seat-a"):
            self.submit(svc, "consul_verify", backend="seat-a")
        self.assertEqual([], svc.events(limit=10))

    def test_freshness_refusals_fail_closed(self) -> None:
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        for kwargs, code in (({"age_s": 31}, "discovery_stale"), ({"leader": False}, "discovery_no_leader"),
                             ({"filtered": True}, "discovery_unauthorized")):
            self.snap(_inst("seat-a", 9101), **kwargs)
            with self.assertRaisesRegex(ExecutionServiceError, f"^{code}: "):
                self.submit(svc, "consul_verify")
        self.assertEqual([], svc.events(limit=10))

    def test_not_configured_is_unavailable(self) -> None:
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with patch("hearth.execution.service.shared_adapter", lambda: None):
            with self.assertRaisesRegex(ExecutionServiceError, "^discovery_unavailable: "):
                self.submit(svc, "consul_verify")

    def test_seat_lost_while_queued_is_refused_at_dispatch_not_re_picked(self) -> None:
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        release, started, calls = threading.Event(), threading.Event(), []

        def generate(**kw):
            calls.append(kw["backend"])
            started.set()
            release.wait(5)
            return self.ok(**kw)

        svc = self.service(generate)
        first = self.submit(svc, "consul_verify")
        self.assertTrue(started.wait(3))                 # holds seat-a's only slot
        second = self.submit(svc, "consul_verify")       # bound to seat-a at admission, waits for the lease
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102))   # seat-a lost; seat-b now passing
        release.set()
        self.assertEqual("succeeded", self.wait_final(svc, first["job_id"])["status"])
        state = self.wait_final(svc, second["job_id"])
        self.assertEqual("failed", state["status"])
        failed = [e for e in svc.events(limit=50) if e["event_type"] == "job.failed" and e["job_id"] == second["job_id"]][0]
        self.assertIn("seat_lost_before_dispatch", failed["reason"])
        self.assertEqual(["seat-a"], calls)              # never silently moved to seat-b

    # --- Codex M2 review fixes ---
    def test_incompatible_model_seat_is_never_chosen_even_if_only_healthy(self) -> None:   # B2
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102, "critical"), _inst("seat-other", 9103, model="m2"))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            self.submit(svc, "consul_verify")
        with self.assertRaisesRegex(ExecutionServiceError, "^pin_not_eligible: seat-other"):
            self.submit(svc, "consul_verify", backend="seat-other")

    def test_authored_alias_is_accepted_and_resolved_model_bound(self) -> None:   # B2
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102, "critical"), _inst("seat-alias", 9104, model="m1-facade"))
        calls = []
        svc = self.service(lambda **kw: calls.append((kw["backend"], kw["model"])) or self.ok(**kw))
        state = self.wait_final(svc, self.submit(svc, "consul_alias")["job_id"])
        self.assertEqual("succeeded", state["status"])
        self.assertEqual([("seat-alias", "m1-facade")], calls)
        self.assertEqual("m1-facade", state["desired"]["discovery"]["model"])
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            self.submit(svc, "consul_verify")   # m1-only family must not accept the alias seat

    def test_max_model_len_too_small_is_refused(self) -> None:   # B2 capacity
        self.snap(_inst("seat-a", 9101, max_model_len=16), _inst("seat-b", 9102, max_model_len=16))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "max_model_len 16 < need"):
            self.submit(svc, "consul_verify")

    def test_node_maintenance_excludes_a_seat(self) -> None:   # B1
        self.snap(_inst("seat-a", 9101, node="critical"), _inst("seat-b", 9102))
        calls = []
        svc = self.service(lambda **kw: calls.append(kw["backend"]) or self.ok(**kw))
        self.wait_final(svc, self.submit(svc, "consul_verify")["job_id"])
        self.assertEqual(["seat-b"], calls)

    def test_endpoint_drift_between_admission_and_dispatch_is_refused(self) -> None:   # B3
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        release, started, calls = threading.Event(), threading.Event(), []

        def generate(**kw):
            calls.append(kw["backend"])
            started.set()
            release.wait(5)
            return self.ok(**kw)

        svc = self.service(generate)
        first = self.submit(svc, "consul_verify")
        self.assertTrue(started.wait(3))
        second = self.submit(svc, "consul_verify")     # bound to seat-a @ :9101
        path = self.root / "backends.toml"
        path.write_text(path.read_text().replace("http://127.0.0.1:9101", "http://127.0.0.1:9199"), encoding="utf-8")
        release.set()
        self.wait_final(svc, first["job_id"])
        state = self.wait_final(svc, second["job_id"])
        self.assertEqual("failed", state["status"])
        failed = [e for e in svc.events(limit=80) if e["event_type"] == "job.failed" and e["job_id"] == second["job_id"]][0]
        self.assertIn("seat_lost_before_dispatch", failed["reason"])
        self.assertIn("bound route", failed["reason"])

    # --- Codex M2 re-review 2 fixes ---
    def test_instance_serving_other_model_on_multi_model_backend_is_refused(self) -> None:   # R2-B1
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102, "critical"), _inst("seat-multi", 9105, model="m2"))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            self.submit(svc, "consul_verify")

    def test_call_carries_the_bound_endpoint(self) -> None:   # R2-B2
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        seen = []
        svc = self.service(lambda **kw: seen.append(kw.get("bound_endpoint")) or self.ok(**kw))
        self.wait_final(svc, self.submit(svc, "consul_verify")["job_id"])
        self.assertEqual(["http://127.0.0.1:9101"], seen)

    def _queued_then(self, mutate) -> tuple[dict, list]:
        release, started, calls = threading.Event(), threading.Event(), []

        def generate(**kw):
            calls.append(kw["backend"])
            started.set()
            release.wait(5)
            return self.ok(**kw)

        svc = self.service(generate)
        first = self.submit(svc, "consul_verify")
        self.assertTrue(started.wait(3))
        second = self.submit(svc, "consul_verify")
        mutate()
        release.set()
        self.wait_final(svc, first["job_id"])
        state = self.wait_final(svc, second["job_id"])
        failed = [e for e in svc.events(limit=80) if e["event_type"] == "job.failed" and e["job_id"] == second["job_id"]]
        return state, [f["reason"] for f in failed]

    def test_same_generation_model_drift_is_refused_at_dispatch(self) -> None:   # R2-B1
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        state, reasons = self._queued_then(lambda: self.snap(_inst("seat-a", 9101, model="m9"), _inst("seat-b", 9102, "critical")))
        self.assertEqual("failed", state["status"])
        self.assertIn("model identity changed", reasons[0])

    def test_capacity_shrink_while_queued_without_policy_max_tokens(self) -> None:   # R2-M4
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        state, reasons = self._queued_then(lambda: self.snap(_inst("seat-a", 9101, max_model_len=512), _inst("seat-b", 9102, "critical")))
        self.assertEqual("failed", state["status"])
        self.assertIn("cannot hold need", reasons[0])
        self.assertEqual(512, state["desired"]["discovery"]["reserve_tokens"])

    def test_missing_or_invalid_capacity_metadata_is_refused(self) -> None:   # R2-M3
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        for bad in (None, 0, -5, "abc"):
            self.snap(_inst("seat-a", 9101, max_model_len=bad), _inst("seat-b", 9102, max_model_len=bad))
            with self.assertRaisesRegex(ExecutionServiceError, "capability_unknown"):
                self.submit(svc, "consul_verify")

    def test_shadow_freshness_refusal_keeps_snapshot_facts(self) -> None:   # R2-m5
        svc = self.service(lambda **kw: self.ok(**kw))
        self.snap(_inst("seat-a", 9101), age_s=31)
        rec = self.wait_final(svc, self.submit(svc, "consul_shadow")["job_id"])["desired"]["discovery"]
        self.assertIn("discovery_stale", rec["would_refuse"])
        self.assertEqual((42, False), (rec["consul_index"], rec["trusted"]))
        self.assertIn("seat-a@g1", rec["instances_seen"])

    # --- Codex M2 re-review 3 fixes ---
    def test_legacy_endpoint_resolution_is_unchanged(self) -> None:   # R5-m1: legacy path byte-compatible
        from hearth.toolsurface.inference import DEFAULT_ENDPOINT, _resolve_target
        # explicit endpoint still wins outright with first-match identity, even beside another backend name
        self.assertEqual("seat-a", _resolve_target("http://127.0.0.1:9101", None, "seat-twin").backend)
        self.assertEqual("seat-b", _resolve_target("http://127.0.0.1:9102", None, "seat-a").backend)
        self.assertEqual("seat-b", _resolve_target(DEFAULT_ENDPOINT, None, "seat-b").backend)   # name pin as before

    def test_transport_gets_the_validated_budget(self) -> None:   # R3-M2
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        seen = []
        svc = self.service(lambda **kw: seen.append(kw.get("max_tokens")) or self.ok(**kw))
        state = self.wait_final(svc, self.submit(svc, "consul_verify")["job_id"])
        self.assertEqual([512], seen)                                    # rung default, made explicit
        self.assertEqual(512, state["desired"]["discovery"]["reserve_tokens"])

    def test_system_content_counts_toward_capacity(self) -> None:   # R3-M3
        self.snap(_inst("seat-a", 9101, max_model_len=1200), _inst("seat-b", 9102, max_model_len=1200))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            self.submit(svc, "consul_verify", system="x" * 4000)       # 1000 tokens of system + 512 budget > 1200
        self.assertEqual([], svc.events(limit=10))

    def test_shadow_records_model_refusal_enforce_would_make(self) -> None:   # R3-m4
        self.snap(_inst("seat-multi", 9105, model="m2"))
        svc = self.service(lambda **kw: self.ok(**kw))
        rec = self.wait_final(svc, self.submit(svc, "consul_shadow", backend="seat-multi", model="m1")["job_id"])["desired"]["discovery"]
        self.assertIn("instance serves ['m2']", rec["would_refuse"])

    # --- Codex M2 re-review 4 fixes ---
    def test_strict_bound_target_path_in_the_real_primitive(self) -> None:   # R4-M1
        from hearth.toolsurface.backends import BackendConfigError
        from hearth.toolsurface.inference import DEFAULT_ENDPOINT, _resolve_target
        t = _resolve_target(DEFAULT_ENDPOINT, None, "seat-twin", bound_endpoint="http://127.0.0.1:9101")
        self.assertEqual(("seat-twin", "pinned-bound", "http://127.0.0.1:9101"), (t.backend, t.routed_by, t.endpoint))
        for backend, bound in (("seat-a", "http://127.0.0.1:9102"),      # moved / mismatched
                               ("gone", "http://127.0.0.1:9101"),        # disappeared name: no endpoint-only fallback
                               ("seat-a", DEFAULT_ENDPOINT)):            # sentinel gets no special treatment
            with self.assertRaisesRegex(BackendConfigError, "^bound_target_mismatch"):
                _resolve_target(DEFAULT_ENDPOINT, None, backend, bound_endpoint=bound)

    def test_think_then_emit_is_refused_under_enforce(self) -> None:   # R4-M2
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^discovery_unsupported_procedure"):
            svc.submit(operation_name="inference.deliberate",
                       arguments={"messages": [{"role": "user", "content": "hi"}], "task_family": "consul_verify",
                                  "thinking": True, "procedure": "think_then_emit"},
                       policy={"max_tokens": 256, "deadline_s": 60},
                       principal=self.principal, source=self.source)
        self.assertEqual([], svc.events(limit=10))

    def test_shadow_records_think_then_emit_refusal(self) -> None:   # R5-m2
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102))
        svc = self.service(lambda **kw: self.ok(**kw))
        job = svc.submit(operation_name="inference.deliberate",
                         arguments={"messages": [{"role": "user", "content": "hi"}], "task_family": "consul_shadow",
                                    "thinking": True, "procedure": "think_then_emit"},
                         policy={"max_tokens": 256, "deadline_s": 60}, principal=self.principal, source=self.source)
        rec = svc.get_job(job["job_id"])["desired"]["discovery"]
        self.assertIn("discovery_unsupported_procedure", rec["would_refuse"])

    def test_deliberate_enforce_routes_around_a_seat_in_maintenance(self) -> None:   # live V5 finding
        self.snap(_inst("seat-a", 9101, node="critical"), _inst("seat-b", 9102))   # seat-a: maintenance
        calls = []
        svc = self.service(lambda **kw: calls.append(kw["backend"]) or self.ok(**kw))
        job = svc.submit(operation_name="inference.deliberate",
                         arguments={"messages": [{"role": "user", "content": "hi"}], "task_family": "consul_verify",
                                    "thinking": False, "procedure": "one_call"},
                         policy={"max_tokens": 64, "deadline_s": 60}, principal=self.principal, source=self.source)
        state = self.wait_final(svc, job["job_id"])
        self.assertEqual("succeeded", state["status"])
        self.assertEqual(["seat-b"], calls)                                  # not pinned to seat-a and refused
        self.assertNotIn("_deliberate_floor", state["desired"]["arguments"])
        self.assertNotIn("backend", state["desired"]["arguments"])           # discovery chose; no hidden pin

    def test_deliberate_enforce_only_considers_deliberate_capable_seats(self) -> None:
        self.snap(_inst("seat-a", 9101, "critical"), _inst("seat-b", 9102, "critical"),
                  _inst("seat-multi", 9105))          # passing, serves m1, but declares no deliberate_max_tokens
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^no_eligible_seat: "):
            svc.submit(operation_name="inference.deliberate",
                       arguments={"messages": [{"role": "user", "content": "hi"}], "task_family": "consul_verify",
                                  "thinking": False, "procedure": "one_call"},
                       policy={"max_tokens": 64, "deadline_s": 60}, principal=self.principal, source=self.source)

    def test_regime_budget_defaults_without_a_pinned_seat(self) -> None:   # R6-high
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102))
        seen = []
        svc = self.service(lambda **kw: seen.append((kw["backend"], kw.get("max_tokens"))) or self.ok(**kw))
        job = svc.submit(operation_name="inference.deliberate",
                         arguments={"messages": [{"role": "user", "content": "hi"}], "task_family": "consul_regime"},
                         principal=self.principal, source=self.source)              # no backend, no max_tokens
        state = self.wait_final(svc, job["job_id"])
        self.assertEqual("succeeded", state["status"])
        self.assertEqual([("seat-a", 2048)], seen)                                   # regime budget; seat-a holds 4096

    def test_plan_applies_the_deliberate_capability_filter(self) -> None:   # R6-medium
        svc = self.service(lambda **kw: self.fail("plan must not dispatch"))
        planned = svc.plan(operation_name="inference.deliberate", task_family="consul_regime", prompt_bytes=100)
        self.assertIn(planned["provider"], {"seat-a", "seat-b"})                   # never seat-multi/alias (no deliberate cap)
        with self.assertRaises(Exception):
            svc.plan(operation_name="inference.deliberate", task_family="consul_regime", prompt_bytes=100,
                     policy={"max_tokens": 8000, "deadline_s": 60})                 # above every seat's 4096

    def test_missing_leader_header_is_refused(self) -> None:   # M4
        self.adapter._set(Snapshot(consul_index=1, fetched_at=time.monotonic(), known_leader=None,
                                   instances=(_inst("seat-a", 9101),)))
        svc = self.service(lambda **kw: self.fail("must not dispatch"))
        with self.assertRaisesRegex(ExecutionServiceError, "^discovery_no_leader: "):
            self.submit(svc, "consul_verify")

    def test_shadow_record_carries_seat_identity_and_refusals(self) -> None:   # m8
        self.snap(_inst("seat-a", 9101), _inst("seat-b", 9102, "critical"))
        svc = self.service(lambda **kw: self.ok(**kw))
        state = self.wait_final(svc, self.submit(svc, "consul_shadow")["job_id"])   # tag route picks seat-a first
        rec = state["desired"]["discovery"]
        self.assertEqual(("shadow", "seat-a", "seat-a@g1", "g1"), (rec["mode"], rec["seat_id"], rec["instance_id"], rec["generation"]))
        self.assertIn("seat-b", rec["refused"])
        self.assertNotIn("would_refuse", rec)

    # --- shadow and off ---
    def test_shadow_records_without_changing_selection_or_refusing(self) -> None:
        self.snap(_inst("seat-a", 9101, "critical"), age_s=31)   # stale AND seat-a critical
        svc = self.service(lambda **kw: self.ok(**kw))
        job = self.submit(svc, "consul_shadow")
        state = self.wait_final(svc, job["job_id"])
        self.assertEqual("succeeded", state["status"])
        record = state["desired"]["discovery"]
        self.assertEqual("shadow", record["mode"])
        self.assertIn("discovery_stale", record["would_refuse"])

    def test_off_family_record_has_no_discovery_key(self) -> None:   # V10 shape
        self.snap()   # an empty snapshot would refuse an enforce family
        svc = self.service(lambda **kw: self.ok(**kw))
        job = self.submit(svc, "plain")
        state = self.wait_final(svc, job["job_id"])
        self.assertEqual("succeeded", state["status"])
        self.assertNotIn("discovery", state["desired"])
        for e in svc.events(limit=50):
            self.assertNotIn("discovery", e.get("observed") or {})


if __name__ == "__main__":
    unittest.main()
