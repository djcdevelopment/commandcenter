"""Gate arithmetic for offload observations, in executable form.

`hearth/observation/emit.py` now writes a `capacity-observation.v1` per dispatch, but an
observation is not a capability: `project_associations` gates it four ways, and on the
current backend pool only ONE rung can pass. That is a structural fact about
`hearth/etc/backends.toml`, not a bug, and rediscovering it from the projector source is
expensive — so it is pinned here.

The shapes below mirror what the emitter actually produces: `task_kind="offload-generate"`,
`workflow_id="wf-hearth-offload-<caller>"`, `builder_id` = the EXECUTING node (never the
caller), `backend` = the rung name.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase

from tools.workflow.project_associations import materialize_associations

TASK_KIND = "offload-generate"


def observation(*, index: int, caller: str, builder: str, model: str, backend: str,
                outcome: str = "success", failure_class: str | None = None,
                timestamp: str = "2026-07-30T08:00:00+00:00") -> dict:
    return {
        "contract_version": "capacity-observation.v1",
        "observation_id": f"obs-offload-{index}",
        "decision_id": None,
        "workflow_id": f"wf-hearth-offload-{caller}",
        "run_id": f"hearth-offload-{caller}",
        "timestamp": timestamp,
        "builder_id": builder,
        "model_id": model,
        "backend": backend,
        "hardware_profile_id": None,
        "workload_shape": {"task_kind": TASK_KIND, "estimated_context_tokens": 1200,
                           "requires_gpu": None, "notes": ""},
        "observed": {"runtime_s": 2.0, "ttft_s": None, "tokens_per_s": 200.0,
                     "ram_gb_peak": None, "vram_gb_peak": None, "context_tokens": 1200,
                     "physical": None},
        "outcome": outcome,
        "failure_class": failure_class,
        "promotion_status": None,
    }


class OffloadCapabilityFormationTests(TestCase):
    def setUp(self) -> None:
        self.root = Path(mkdtemp()).resolve()
        self.runs = self.root / "runs"
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _pour(self, observations: list[dict]) -> dict:
        """Write each observation into its own run stream with an event that refs it, then
        project. Mirrors the emitter's on-disk layout, including date sharding."""
        event_files = []
        by_run: dict[str, list[dict]] = {}
        for obs in observations:
            by_run.setdefault(obs["run_id"], []).append(obs)

        for run_id, group in by_run.items():
            run_dir = self.runs / run_id
            events = []
            for obs in group:
                day = obs["timestamp"][:10]
                relative = f"artifacts/{day}/{obs['observation_id']}.json"
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(obs), encoding="utf-8")
                events.append({
                    "event_id": f"evt_{obs['observation_id']}",
                    "event_type": "work.accepted",
                    "timestamp": obs["timestamp"],
                    "workflow_id": obs["workflow_id"],
                    "run_id": obs["run_id"],
                    "actor": {"type": "builder", "id": "claude-frontier"},
                    "status": "completed",
                    "payload": {"source": "hearth-offload-dispatch"},
                    "artifact_refs": [{
                        "artifact_id": obs["observation_id"],
                        "artifact_type": "capacity_observation",
                        "path": f"{run_id}/{relative}",
                    }],
                })
            events_path = run_dir / "events.jsonl"
            with events_path.open("w", encoding="utf-8", newline="\n") as handle:
                for event in events:
                    handle.write(json.dumps(event) + "\n")
            event_files.append(events_path)

        materialize_associations(event_files, self.knowledge)
        return {
            "associations": json.loads(
                (self.knowledge / "associations.json").read_text(encoding="utf-8-sig")),
            "capabilities": json.loads(
                (self.knowledge / "capabilities.json").read_text(encoding="utf-8-sig")),
        }

    def test_two_models_two_callers_all_success_forms_a_capability(self) -> None:
        """The omen-ollama shape: two callers give gate 1 its second workflow, and two
        models give gate 2 its variation. This is the one rung that passes today."""
        out = self._pour([
            observation(index=1, caller="claude-frontier", builder="omen",
                        model="qwen3-coder:30b", backend="omen-ollama"),
            observation(index=2, caller="gcp-adk-test", builder="omen",
                        model="qwen2.5:7b-instruct", backend="omen-ollama"),
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 1)
        capability = out["capabilities"]["capabilities"][0]
        self.assertEqual(capability["capability_id"],
                         f"capability:task_kind={TASK_KIND}|backend=omen-ollama")
        self.assertEqual(capability["invariant"]["task_kind"], TASK_KIND)

    def test_single_model_bucket_stays_gated_on_nothing_varied(self) -> None:
        """The am4-moe shape: three callers, 85 real calls, ONE declared model -- so
        builder_id and model_id are both functionally determined by backend and gate 2 can
        never be satisfied. Volume does not help. This documents why the high-traffic rungs
        earn no capability on the current pool config."""
        out = self._pour([
            observation(index=i, caller=caller, builder="am4",
                        model="gpt-oss-120b", backend="am4-moe")
            for i, caller in enumerate(
                ["claude-frontier", "docker-open-notebook-facade", "gcp-adk-test"], start=10)
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 0)
        reasons = " ".join(
            " ".join(bucket["reasons"]) for bucket in out["associations"]["gated_buckets"])
        self.assertIn("nothing varied", reasons)

    def test_single_caller_bucket_stays_gated_on_one_workflow(self) -> None:
        """The gcp-gemini-pro shape: 86 calls, all from claude-frontier. One workflow is
        one occasion, however many samples it produced."""
        out = self._pour([
            observation(index=20, caller="claude-frontier", builder="gcp-vertex",
                        model="gemini-3.1-pro-preview", backend="gcp-gemini-pro"),
            observation(index=21, caller="claude-frontier", builder="gcp-vertex",
                        model="gemini-3.5-flash", backend="gcp-gemini-pro"),
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 0)
        reasons = " ".join(
            " ".join(bucket["reasons"]) for bucket in out["associations"]["gated_buckets"])
        self.assertIn("one workflow", reasons)

    def test_one_non_success_poisons_the_bucket(self) -> None:
        """Gate 3 binarizes outcomes and the corpus is append-only, so a single
        un-excluded failure kills a (task_kind, backend) bucket PERMANENTLY. That is why
        the emitter's ADR-0002 exclusion table is the mechanism keeping gate 3 satisfiable,
        not an optimization."""
        out = self._pour([
            observation(index=30, caller="claude-frontier", builder="omen",
                        model="qwen3-coder:30b", backend="omen-ollama"),
            observation(index=31, caller="gcp-adk-test", builder="omen",
                        model="qwen2.5:7b-instruct", backend="omen-ollama"),
            observation(index=32, caller="dev-local", builder="omen",
                        model="qwen3-coder:30b", backend="omen-ollama",
                        outcome="timeout", failure_class="timeout"),
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 0)
        reasons = " ".join(
            " ".join(bucket["reasons"]) for bucket in out["associations"]["gated_buckets"])
        self.assertIn("mixed", reasons)

    def test_null_backend_observations_drop_out_of_the_bucket(self) -> None:
        """A pinned-endpoint dispatch has no backend. An observation with a null invariant
        value is skipped rather than bucketed under 'unknown'."""
        out = self._pour([
            observation(index=40, caller="claude-frontier", builder="omen",
                        model="qwen3-coder:30b", backend="omen-ollama"),
            observation(index=41, caller="gcp-adk-test", builder="omen",
                        model="qwen2.5:7b-instruct", backend=None),
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 0)

    def test_capability_requires_task_kind_so_failure_invariants_never_yield_one(self) -> None:
        """All-failure buckets that agree on a failure_class form a failure_invariant, and
        synthesize_capabilities skips any invariant without task_kind."""
        out = self._pour([
            observation(index=50, caller="claude-frontier", builder="omen",
                        model="qwen3-coder:30b", backend="omen-ollama",
                        outcome="timeout", failure_class="timeout"),
            observation(index=51, caller="gcp-adk-test", builder="omen",
                        model="qwen2.5:7b-instruct", backend="omen-ollama",
                        outcome="timeout", failure_class="timeout"),
        ])

        self.assertEqual(out["capabilities"]["capability_count"], 0)
        kinds = {association["association_type"]
                 for association in out["associations"]["associations"]}
        self.assertIn("failure_invariant", kinds)
