from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hearth.execution.artifacts import ArtifactStore
from hearth.execution.coordination import CapacityLeaseStore
from hearth.execution.ledger import ExecutionLedger
from hearth.execution.service import ExecutionService, ExecutionServiceError
from hearth.toolsurface.inference import response_schema_digest
from hearth.localwork.service import LocalWorkError, LocalWorkService, SERVING_PROFILE_KEYS
from hearth.toolsurface.backends import load_pool


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


class LocalWorkServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "a.py").write_text("one\ntwo\n", encoding="utf-8")
        git(self.repo, "add", "a.py")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.outputs: list[str] = [json.dumps({
            "schema": "local-work-candidate.v1",
            "artifact_kind": "unified_diff",
            "summary": "Change the second line.",
            "target_path": None,
            "citations": [{"path": "a.py", "start_line": 1, "end_line": 2}],
            "content": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
        })]

        self.generate_calls: list[dict] = []

        def generate(**kwargs):
            self.generate_calls.append(kwargs)
            text, extra = self.outputs.pop(0), {}
            if isinstance(text, tuple):  # (answer, extra result keys the door would add)
                text, extra = text
            result = {"ok": True, "text": text, "backend": "omen-arc",
                      "model": "qwen3-30b-a3b", "routed_by": "pinned:omen-arc",
                      "tokens_in": 100, "tokens_out": 50, "duration_ms": 5, **extra}
            # what the door's request-body builder stamps when the call carried them (W2)
            if kwargs.get("response_schema") is not None:
                result["response_schema_sha256"] = response_schema_digest(kwargs["response_schema"])
            if kwargs.get("temperature") is not None:
                result["temperature"] = kwargs["temperature"]
            return result

        state = self.root / "execution"
        self.execution = ExecutionService(
            ledger=ExecutionLedger(state), artifacts=ArtifactStore(state / "artifacts"),
            leases=CapacityLeaseStore(state / "leases.sqlite"), generate=generate,
            workers=1, recover_pending=False)
        self.env = mock.patch.dict(os.environ, {"HEARTH_OPERATOR_HOME": str(self.root)})
        self.env.start()
        self.service = LocalWorkService(self.execution, root=self.root,
                                        token_counter=lambda _p, _m, _q: 100)

    def tearDown(self) -> None:
        self.execution.close()
        self.env.stop()
        self.temp.cleanup()

    def submit(self, **overrides):
        args = dict(intent="Fix a.py", acceptance_criteria=["diff applies"],
                    repo=str(self.repo), base_commit=self.base, files=["a.py"],
                    artifact_kind="unified_diff", target_path=None, lane="fast",
                    task_family=None, deadline_s=30, max_tokens=128,
                    receipt_id="br-test", idempotency_key=None, caller_id="caller-a")
        args.update(overrides)
        return self.service.submit(**args)

    def settle(self, work_id: str, wanted: str = "awaiting_review") -> dict:
        for _ in range(100):
            current = self.service.get(work_id)
            if current["status"] == wanted or current["status"] == "failed":
                return current
            time.sleep(.01)
        self.fail("local work did not settle")

    def test_candidate_waits_for_explicit_evidenced_verdict(self) -> None:
        manifest = self.submit()
        final = self.settle(manifest["work_id"])
        self.assertEqual(final["status"], "awaiting_review")
        fetched = self.service.artifact(final["work_id"])
        self.assertEqual(fetched["artifact"]["sha256"], final["artifact"]["sha256"])
        with self.assertRaisesRegex(LocalWorkError, "passed evidenced"):
            self.service.verdict(final["work_id"], decision="accepted", criteria=[],
                                 summary="reviewed", evidence=["pytest"], receipt_id=None,
                                 caller_id="frontier")
        accepted = self.service.verdict(
            final["work_id"], decision="accepted",
            criteria=[{"criterion": "diff applies", "status": "passed", "evidence": "git apply --check"}],
            summary="Independently checked", evidence=["focused tests passed"],
            receipt_id=None, caller_id="frontier")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["caller"]["validated_by"], "frontier")

    def test_only_one_structural_repair_is_attempted(self) -> None:
        self.outputs[:] = ["not json", "still not json"]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertEqual(final["status"], "failed")
        self.assertEqual(len(final["attempts"]), 2)
        self.assertTrue(final["attempts"][1]["repair"])

    def test_bare_path_string_citation_normalizes_to_whole_file(self) -> None:
        cand = json.loads(self.outputs[0])
        cand["citations"] = ["a.py"]
        self.outputs[:] = [json.dumps(cand)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="awaiting_review")
        self.assertEqual(final["status"], "awaiting_review")
        self.assertTrue(final.get("mechanical", {}).get("normalized_bare_citation"))
        artifact = self.service.artifact(final["work_id"])
        self.assertEqual(artifact["candidate"]["citations"], [{"path": "a.py", "start_line": 1, "end_line": 2}])

    def test_invalid_citation_shape_triggers_repair(self) -> None:
        bad = json.loads(self.outputs[0])
        bad["citations"] = [12345]  # invalid citation shape
        good = json.loads(self.outputs[0])
        self.outputs[:] = [json.dumps(bad), json.dumps(good)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="awaiting_review")
        self.assertEqual(final["status"], "awaiting_review")
        self.assertEqual(len(final["attempts"]), 2)
        self.assertTrue(final["attempts"][1]["repair"])

    def test_semantically_invalid_candidate_fails_without_retry(self) -> None:
        bad = json.loads(self.outputs[0])
        bad["citations"][0]["end_line"] = 99
        self.outputs[:] = [json.dumps(bad)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertEqual(len(final["attempts"]), 1)
        self.assertIn("citation exceeds", final["failure"])

    def test_pinned_commit_ignores_mutable_worktree(self) -> None:
        (self.repo / "a.py").write_text("mutated\n", encoding="utf-8")
        manifest = self.submit()
        self.assertEqual(manifest["base_commit"], self.base)
        self.assertEqual(manifest["source"]["files"][0]["lines"], 2)

    def test_context_and_vision_refuse_before_job(self) -> None:
        refusing = LocalWorkService(self.execution, root=self.root,
                                     token_counter=lambda _p, _m, _q: 1_000_000)
        with self.assertRaisesRegex(LocalWorkError, "exact context refusal"):
            refusing.submit(intent="x", acceptance_criteria=["x"], repo=str(self.repo),
                             base_commit=self.base, files=["a.py"], artifact_kind="markdown",
                             target_path=None, lane="fast", task_family=None, deadline_s=30,
                             max_tokens=128, receipt_id=None, idempotency_key=None, caller_id="c")
        with self.assertRaisesRegex(LocalWorkError, "vision"):
            self.submit(task_family="vision")

    def test_diff_may_not_touch_undeclared_path(self) -> None:
        bad = json.loads(self.outputs[0])
        bad["content"] = bad["content"].replace("a.py", "secret.py")
        self.outputs[:] = [json.dumps(bad)]
        manifest = self.submit()
        final = self.settle(manifest["work_id"], wanted="failed")
        self.assertIn("undeclared", final["failure"])

    def test_idempotency_returns_same_work_and_rejects_reuse(self) -> None:
        first = self.submit(idempotency_key="same")
        second = self.submit(idempotency_key="same")
        self.assertEqual(first["work_id"], second["work_id"])
        with self.assertRaisesRegex(LocalWorkError, "different local work"):
            self.submit(idempotency_key="same", intent="different")

    def test_manifest_serving_profile_is_allowlisted_and_digested(self) -> None:
        manifest = self.submit()
        route = manifest["route"]
        profile = route["serving_profile"]
        self.assertTrue(set(profile).issubset(SERVING_PROFILE_KEYS))
        self.assertNotIn("context_bytes", profile)
        self.assertNotIn("timeout_s", profile)
        expected = hashlib.sha256(json.dumps(
            profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(route["serving_profile_sha256"], expected)
        reconciled = self.settle(manifest["work_id"])
        self.assertEqual(reconciled["route"]["serving_profile"], profile)
        self.assertEqual(reconciled["route"]["serving_profile_sha256"], expected)

    def test_serving_profile_excludes_unrelated_settings(self) -> None:
        qualified = {
            "context_tokens": 131072,
            "parallel_slots": 1,
            "hardware_profile_id": "omen-285k-dual-b70-2026H2",
            "engine": "llama.cpp",
        }
        polluted = dict(qualified, auth_env="SECRET_ENV", endpoint="http://private.invalid",
                        node="omen", context_bytes=458752, timeout_s=1000,
                        nested={"must": "not leak"})
        clean_profile, clean_digest = LocalWorkService._serving_profile(qualified)
        polluted_profile, polluted_digest = LocalWorkService._serving_profile(polluted)
        self.assertEqual(polluted_profile, clean_profile)
        self.assertEqual(polluted_digest, clean_digest)

    def test_clean_d1_profile_declares_qualified_shape(self) -> None:
        config = Path(__file__).resolve().parents[2] / "etc" / "backends.toml"
        provider = load_pool(config).by_name("omen-arc-27b")
        self.assertIsNotNone(provider)
        assert provider is not None
        profile, _digest = LocalWorkService._serving_profile(provider.settings)
        self.assertEqual(profile["context_tokens"], 131072)
        self.assertEqual(profile["parallel_slots"], 1)
        self.assertEqual(profile["device_backend"], "SYCL")
        self.assertEqual(profile["kv_cache_key_type"], "f16")
        self.assertFalse(profile["speculative"])

    def test_clean_am4_profile_is_the_qualified_fast_lane(self) -> None:
        config = Path(__file__).resolve().parents[2] / "etc" / "backends.toml"
        provider = load_pool(config).by_name("am4-dense")
        self.assertIsNotNone(provider)
        assert provider is not None
        profile, _digest = LocalWorkService._serving_profile(provider.settings)
        self.assertEqual(profile["context_tokens"], 65536)
        self.assertEqual(profile["max_tokens"], 4096)
        self.assertEqual(profile["parallel_slots"], 1)
        self.assertEqual(profile["device_backend"], "CUDA")
        self.assertEqual(profile["kv_cache_key_type"], "q4_0")
        self.assertEqual(profile["batch_tokens"], 2048)
        routes, _route_digest = LocalWorkService._route_profile()
        self.assertEqual(routes["fast"], "am4-dense")
        self.assertEqual(routes["deep"], "omen-arc-27b")

    def delivery_submit(self, **overrides):
        """A brief.v2 through the door: the model's answer is delivery-output.v1, the door renders the manifest."""
        brief = json.loads((Path(__file__).resolve().parents[1] / "delivery" / "fixtures" / "a59bad05.brief.json")
                           .read_text(encoding="utf-8"))
        self.outputs[:] = [json.dumps({"summary": "The second line.", "sections": [
            {"heading": "Lines", "paragraphs": [{"text": "The file ends with two.", "quotes": ["two"]}]}]})]
        return self.submit(artifact_kind="markdown", brief=brief, max_tokens=None, **overrides)

    def test_delivery_manifest_records_observed_route_and_conditions(self) -> None:
        """docs/rnd-log.md 2026-10-03T05:20Z, laps 1-4 (work_a59bad05 onward): the manifest's observed route fields
        (backend, tokens, duration, response_schema_sha256) were all null and `environment` read unknown. The
        manifest names where the answer was produced, what it cost, which schema constrained it, and the
        conditions it ran under; the door, not the model, says so."""
        with mock.patch.dict(os.environ, {"HEARTH_LAB_CONFIGURATION": "memsplice",
                                          "HEARTH_ENVIRONMENT_FILE": str(self.root / "no-environment")}):
            manifest = self.delivery_submit(temperature=0.2)
            final = self.settle(manifest["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        route = final["route"]
        self.assertEqual((route["backend"], route["model"], route["routed_by"]),
                         ("omen-arc", "qwen3-30b-a3b", "pinned:omen-arc"))
        self.assertEqual((route["tokens_in"], route["tokens_out"], route["duration_ms"]), (100, 50, 5))
        schema = self.generate_calls[0]["response_schema"]
        self.assertEqual(route["response_schema_sha256"], response_schema_digest(schema))
        self.assertEqual(route["temperature"], 0.2)
        self.assertEqual(final["conditions"]["lab_configuration"], "memsplice")
        self.assertEqual(final["conditions"]["lab_configuration_source"], "env")
        self.assertEqual(final["conditions"]["temperature"], 0.2)
        self.assertEqual(final["conditions"]["environment"], "prod")   # ADR-0053: a missing file reads as prod
        self.assertEqual(final["delivery_summary"]["deterministic"], "pass")

    def test_delivery_conditions_say_when_no_temperature_was_sent(self) -> None:
        """docs/rnd-log.md 2026-10-03T05:50Z: every delivery of laps 1-5 was sent without a temperature and the
        8B's two runs differed ("temperature on the tool seat" not sampled); the record must say none was sent
        (None), never a default."""
        with mock.patch.dict(os.environ, {"HEARTH_LAB_CONFIGURATION": "day",
                                          "HEARTH_ENVIRONMENT_FILE": str(self.root / "no-environment")}):
            manifest = self.delivery_submit()
            final = self.settle(manifest["work_id"])
        self.assertIsNone(final["conditions"]["temperature"])
        self.assertNotIn("temperature", final["route"])

    def routes_file(self, *lanes: tuple[str, str]) -> str:
        path = self.root / "routes.toml"
        path.write_text("version = \"local-work-routes.v2\"\n" + "".join(
            f"[lane.{name}]\nbackend = \"{backend}\"\n" for name, backend in lanes), encoding="utf-8")
        return str(path)

    def test_tool_lane_routes_to_the_profiles_tool_backend(self) -> None:
        """Delivery lap 4 (perception brief on am4-tool-4070ti): lane=tool is explicit and lands on the backend the
        route profile names for it."""
        routes = self.routes_file(("fast", "am4-dense"), ("deep", "omen-arc-27b"), ("tool", "am4-read-4070ti"))
        with mock.patch.dict(os.environ, {"HEARTH_LOCAL_WORK_ROUTES": routes}):
            manifest = self.submit(lane="tool")
            self.settle(manifest["work_id"])
        self.assertEqual(manifest["route"]["requested_lane"], "tool")
        self.assertEqual(manifest["route"]["selected_lane"], "tool")
        self.assertEqual(manifest["route"]["provider"], "am4-read-4070ti")
        self.assertEqual(self.generate_calls[0]["backend"], "am4-read-4070ti")

    def test_a_lane_the_profile_lacks_is_refused_by_name(self) -> None:
        """Delivery lap 4 (lane=tool is new, flash 8919a22): a host whose route profile has no tool lane refuses it
        by name before any generate call, instead of falling back to another lane."""
        routes = self.routes_file(("fast", "am4-dense"), ("deep", "omen-arc-27b"))
        with mock.patch.dict(os.environ, {"HEARTH_LOCAL_WORK_ROUTES": routes}):
            with self.assertRaisesRegex(LocalWorkError, "local lane 'tool' is not in this host's route profile"):
                self.submit(lane="tool")
        self.assertEqual(self.generate_calls, [])

    def test_a_kept_revision_does_not_inherit_the_first_answers_repairs(self) -> None:
        """W4 integration (wp/w14 x wp/w15): the route describes the kept answer. A first answer the door repaired
        (control_char_in_string) and a kept revision that needed no repair: the route carries none, and each
        attempt says what its own answer needed."""
        brief = json.loads((Path(__file__).resolve().parents[1] / "delivery" / "fixtures" / "a59bad05.brief.json")
                           .read_text(encoding="utf-8"))
        first = json.dumps({"summary": "The second line.", "sections": [
            {"heading": "Lines", "paragraphs": [{"text": "The file ends with two.", "quotes": ["three"]}]}]})
        second = json.dumps({"summary": "The second line.", "sections": [
            {"heading": "Lines", "paragraphs": [{"text": "The file ends with two.", "quotes": ["two"]}]}]})
        coverage = self.coverage_answer(json.loads(first))
        self.outputs[:] = [(first, {"structured_output_repairs": ["control_char_in_string"]}), second,
                           (json.dumps(coverage), {"backend": "omen-dense-27b", "model": "test-27b"})]
        with mock.patch.dict(os.environ, {"HEARTH_BACKENDS": self.coverage_pool()}):
            manifest = self.submit(artifact_kind="markdown", brief=brief, max_tokens=None, revise=True,
                                   idempotency_key="revise-repairs")
            final = self.settle(manifest["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        self.assertEqual(final["revision"]["kept"], "revised", final["revision"])
        self.assertNotIn("structured_output_repairs", final["route"])
        self.assertEqual(final["attempts"][0]["structured_output_repairs"], ["control_char_in_string"])
        self.assertNotIn("structured_output_repairs", final["attempts"][1])
        self.assertIn("three", self.generate_calls[-1]["prompt"].split("OBJECTIONS:", 1)[1])
        self.assertEqual(final["revision"]["coverage"]["state"], "pass")
        self.assertEqual(final["revision"]["coverage"]["method"], "identical_prose")
        self.assertEqual(len(self.generate_calls), 2)

    def coverage_pool(self):
        config = Path(__file__).resolve().parents[2] / "etc" / "backends.toml"
        target = self.root / "coverage-backends.toml"
        target.write_text(config.read_text() + '''
[[backend]]
name = "omen-dense-27b"
api = "openai"
endpoint = "http://127.0.0.1:1"
models = ["test-27b"]
[backend.settings]
context_tokens = 65536
context_bytes = 229376
max_tokens = 8192
parallel_slots = 2
''')
        return str(target)

    @staticmethod
    def coverage_answer(output):
        from hearth.delivery.revision import prose
        return {"criteria_preserved": True, "coverage": [
            {"claim_id": cid, "status": "retained", "p": 1,
             "revised_evidence": text, "source_quotes": [], "reason": "Every assertion is retained."}
            for cid, text in prose(output).items()]}

    def test_coverage_omission_or_wrong_backend_keeps_original(self):
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the file."}]}
        first = {"summary": "It ends with two.", "sections": [{"heading": "Lines", "paragraphs": [
            {"text": "The first line is one and the last is two.", "quotes": ["three"]}]}]}
        revised = {"summary": "It ends with two.", "sections": [{"heading": "Lines", "paragraphs": [
            {"text": "The last line is two.", "quotes": ["two"]}]}]}
        for variant in ("dropped", "omitted", "wrong_backend", "low_confidence"):
            with self.subTest(variant=variant):
                coverage = self.coverage_answer(first)
                coverage["coverage"][1].update(status="missing", revised_evidence="The last line is two.",
                                               reason="The correct first-line fact was silently deleted.")
                if variant == "omitted":
                    coverage["coverage"].pop()
                if variant == "low_confidence":
                    coverage["coverage"][1].update(status="retained", p=.5)
                backend = "omen-vllm" if variant == "wrong_backend" else "omen-dense-27b"
                self.outputs[:] = [json.dumps(first), json.dumps(revised),
                                   (json.dumps(coverage), {"backend": backend, "model": "test-27b"})]
                with mock.patch.dict(os.environ, {"HEARTH_BACKENDS": self.coverage_pool()}):
                    manifest = self.submit(artifact_kind="markdown", brief=brief, max_tokens=None, revise=True)
                    final = self.settle(manifest["work_id"])
                self.assertEqual(final["status"], "awaiting_review", final)
                self.assertEqual(final["revision"]["kept"], "original", final["revision"])
                self.assertNotEqual(final["revision"]["coverage"]["state"], "pass")

    def test_revision_rejects_new_failure_even_when_kind_already_existed(self):
        before = {"unsupported": 2, "rung0_failures": ["arithmetic_mismatch"], "rung0_findings": ["old"]}
        after = {"unsupported": 1, "rung0_failures": ["arithmetic_mismatch"], "rung0_findings": ["new"]}
        self.assertIsNone(self.service._better(before, after))
        after["rung0_findings"] = []
        self.assertIsNotNone(self.service._better(before, after))
        after["unsupported"] = 2
        self.assertIn("rung 0 findings", self.service._better(before, after))  # equal quotes, fewer findings: adopt
        after["rung0_findings"] = ["old"]
        self.assertIsNone(self.service._better(before, after))  # equal quotes, equal findings: refuse

    def test_delivery_budget_from_brief_and_explicit_override(self):
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the file."}],
                 "generation": {"max_tokens": 6000}}
        answer = {"summary": "The file.", "sections": []}
        for explicit, expected in ((None, 6000), (4096, 4096)):
            self.outputs[:] = [json.dumps(answer)]
            final = self.settle(self.submit(artifact_kind="markdown", brief=brief,
                                           max_tokens=explicit)["work_id"])
            self.assertEqual(final["prompt"]["output_reserve_tokens"], expected)
            self.assertEqual(self.generate_calls[-1]["max_tokens"], expected)

    def test_line_reference_prompt_and_report_use_pinned_source(self):
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the last line."}],
                 "form": {"quote_mode": "line_reference", "citations": "quote"}}
        self.outputs[:] = [json.dumps({"summary": "The last line.", "sections": [{"heading": "Lines",
            "paragraphs": [{"text": "The file ends with two.", "quotes": ["a.py:2"]}]}]})]
        final = self.settle(self.submit(artifact_kind="markdown", brief=brief, max_tokens=None)["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final)
        prompt = self.generate_calls[-1]["prompt"]
        self.assertIn("numbered; cite repository-relative path:N", prompt)
        self.assertNotIn("Do not write line numbers", prompt)
        art = self.service.artifact(final["work_id"])
        self.assertIn("> two", art["candidate"])
        claim = art["delivery"]["manifest"]["claims"][0]
        self.assertEqual((claim["quote"], claim["quote_reference"], claim["resolved"]["start_line"]),
                         ("two", "a.py:2", 2))


    def test_rewritten_prose_still_requires_separate_coverage(self):
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the file."}]}
        first = {"summary": "It ends with two.", "sections": [{"heading": "Lines", "paragraphs": [
            {"text": "The file ends with two.", "quotes": ["three"]}]}]}
        revised = json.loads(json.dumps(first))
        revised["sections"][0]["paragraphs"][0].update(text="The file ends with two. It starts with one.", quotes=["two"])
        coverage = self.coverage_answer(first)
        self.outputs[:] = [json.dumps(first), json.dumps(revised),
                           (json.dumps(coverage), {"backend": "omen-dense-27b", "model": "test-27b"})]
        with mock.patch.dict(os.environ, {"HEARTH_BACKENDS": self.coverage_pool()}):
            final = self.settle(self.submit(artifact_kind="markdown", brief=brief, revise=True)["work_id"])
        self.assertEqual(final["revision"]["kept"], "revised", final)
        self.assertEqual(final["revision"]["coverage"]["state"], "pass")
        self.assertEqual(len(self.generate_calls), 3)
        from hearth.delivery.revision import same_prose
        changed_heading = json.loads(json.dumps(first))
        changed_heading["sections"][0]["heading"] = "Different function"
        self.assertFalse(same_prose(first, changed_heading))


    def test_heading_claim_change_keeps_original_without_judge(self):
        brief = {"schema": "brief.v2", "substance": [{"id": "s1", "statement": "Describe the file."}]}
        first = {"summary": "The file ends with two.", "sections": [{"heading": "The last line is two", "paragraphs": [
            {"text": "The file ends with two.", "quotes": ["three"]}]}]}
        revised = json.loads(json.dumps(first))
        revised["sections"][0]["heading"] = "The last line is one"
        revised["sections"][0]["paragraphs"][0]["quotes"] = ["two"]
        self.outputs[:] = [json.dumps(first), json.dumps(revised)]
        final = self.settle(self.submit(artifact_kind="markdown", brief=brief, revise=True)["work_id"])
        self.assertEqual(final["revision"]["kept"], "original")
        self.assertIn("heading", final["revision"]["reason"])
        self.assertEqual(len(self.generate_calls), 2)
        from hearth.delivery import revision
        with self.assertRaisesRegex(ValueError, "headings"):
            revision.assess(json.dumps(self.coverage_answer(first)), first, revised, None)


class CarryProcedureTests(unittest.TestCase):
    """procedure="carry" (delivery-plan lap 17, B2): a thinking turn writes the draft, thinking-off calls attach quotes,
    code assembles; the work stays queued/running through the stages and a failed stage fails it loudly."""

    DRAFT = "# Lines\n\nThe file ends with two.\n\nThe file starts with one.\n"
    ATTACH = "[block 2]\n> two\n[block 3]\n> one\n"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "a.py").write_text("one\ntwo\n", encoding="utf-8")
        git(self.repo, "add", "a.py")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.root / "backends.toml").write_text(
            'default = "fast-b"\n' + "".join(
                f'[[backend]]\nname = "{n}"\nendpoint = "http://127.0.0.1:{9000 + i}"\napi = "openai"\nmodels = ["m-{n}"]\n'
                f'[backend.settings]\nparallel_slots = 1\ncontext_tokens = 65536\ncontext_bytes = 229376\nmax_tokens = 16384\n'
                + ("deliberate_max_tokens = 24576\n" if n == "deep-b" else "")
                for i, n in enumerate(("fast-b", "deep-b"))), encoding="utf-8")
        (self.root / "routes.toml").write_text(
            'version = "local-work-routes.v2"\n[lane.fast]\nbackend = "fast-b"\n[lane.deep]\nbackend = "deep-b"\n', encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {
            "HEARTH_OPERATOR_HOME": str(self.root), "HEARTH_BACKENDS": str(self.root / "backends.toml"),
            "HEARTH_LOCAL_WORK_ROUTES": str(self.root / "routes.toml"),
            "HEARTH_ENVIRONMENT_FILE": str(self.root / "no-environment")})
        self.env.start()
        self.calls: list[dict] = []
        self.attach: list = []      # one entry per attach call: the answer text, or an Exception-free dict result
        self.work: dict = {}
        self.hold: threading.Event | None = None    # holds the thinking turn until set

        def generate(**kwargs):
            self.calls.append(kwargs)
            common = {"backend": kwargs["backend"], "model": kwargs["model"], "wire_request": {}, "reasoning": "thought",
                      "tokens_in": 10, "tokens_out": 5, "duration_ms": 1, "thinking": kwargs["thinking"]}
            if kwargs["thinking"]:
                if self.hold is not None:
                    self.hold.wait(5)
                return {**common, "ok": True, "text": self.DRAFT, "finish_reason": "stop", **self.work}
            answer = self.attach.pop(0) if self.attach else self.ATTACH
            if isinstance(answer, dict):
                return {**common, **answer}
            return {**common, "ok": True, "text": answer, "finish_reason": "stop"}

        self.state = self.root / "execution"
        self.execution = self.execution_service(generate, recover=False)
        self.service = LocalWorkService(self.execution, root=self.root, token_counter=lambda _p, _m, _q: 100)

    def execution_service(self, generate, recover):
        return ExecutionService(
            ledger=ExecutionLedger(self.state), artifacts=ArtifactStore(self.state / "artifacts"),
            leases=CapacityLeaseStore(self.state / "leases.sqlite"), generate=generate, workers=2, recover_pending=recover)

    def tearDown(self) -> None:
        self.execution.close()
        self.env.stop()
        self.temp.cleanup()

    def submit(self, **overrides):
        brief = json.loads((Path(__file__).resolve().parents[1] / "delivery" / "fixtures" / "a59bad05.brief.json")
                           .read_text(encoding="utf-8"))
        args = dict(intent="Report on a.py", acceptance_criteria=["renders"], repo=str(self.repo), base_commit=self.base,
                    files=["a.py"], artifact_kind="markdown", target_path=None, lane="deep", task_family=None,
                    deadline_s=30, max_tokens=None, receipt_id="br-carry", idempotency_key=None, caller_id="caller-a",
                    brief=brief, procedure="carry")
        args.update(overrides)
        return self.service.submit(**args)

    def settle(self, work_id: str) -> dict:
        for _ in range(300):
            current = self.service.get(work_id)
            if current["status"] not in {"queued", "running"}:
                return current
            time.sleep(.02)
        self.fail("carried work did not settle")

    def test_full_run_stops_at_awaiting_review_with_the_carry_record(self) -> None:
        final = self.settle(self.submit()["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        work, attach = self.calls
        self.assertEqual((work["thinking"], work["max_tokens"], work["temperature"]), (True, 24576, 0))
        self.assertEqual((attach["thinking"], attach["max_tokens"], attach["temperature"]), (False, 1000, 0))
        self.assertEqual([m["role"] for m in work["messages"]], ["system", "user"])
        self.assertIn("STEP: work.", work["messages"][1]["content"])
        self.assertIn("=== CARRIED DRAFT ===", attach["messages"][1]["content"])
        self.assertEqual([(j["stage"], j["batch"]) for j in final["carry"]["jobs"]], [("work", 0), ("attach", 1)])
        self.assertEqual(final["carry"]["stage"], "render")
        run = self.root / "runs" / "operator" / final["work_id"]
        delivery = json.loads((run / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((delivery["procedure"], delivery["aids_used"][-2:]), ("carry", ["thinking", "carried_draft"]))
        self.assertTrue({"candidate", "manifest", "output", "carry_draft", "carry_reasoning", "line_references"}
                        <= set(final["delivery_artifacts"]))
        self.assertEqual((run / "carry-draft.md").read_text(encoding="utf-8"), self.DRAFT)
        self.assertEqual(self.service.artifact(final["work_id"])["delivery"]["files"]["carry_reasoning"]["size"], 7)

    def test_only_the_report_part_is_carried_and_the_notes_guide_the_attacher(self) -> None:
        self.DRAFT = "# Notes\n\nlines 12, 14 matter.\n\n# Report\n\nThe file ends with two.\n\nThe file starts with one.\n"
        self.attach = ["[block 4]\n> two\n[block 5]\n> one\n"]
        final = self.settle(self.submit()["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        attach = self.calls[1]["messages"][1]["content"]
        self.assertIn("never copy a quote from them):\n# Notes\n\nlines 12, 14 matter.\n\nBLOCKS TO CHECK:\n[block 4]", attach)
        run = self.root / "runs" / "operator" / final["work_id"]
        delivery = json.loads((run / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual(delivery["repairs"]["notes_blocks_not_carried"], 2)
        self.assertNotIn("lines 12", (run / "candidate.md").read_text(encoding="utf-8"))

    def test_a_truncated_attach_answer_is_retried_once_as_two_halves(self) -> None:
        self.attach = [{"ok": False, "text": "[block 2]", "finish_reason": "length", "error_code": "output_truncated",
                        "error": "output_truncated: cut"}, "[block 2]\n> two\n", "[block 3]\n> one\n"]
        final = self.settle(self.submit()["work_id"])
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        self.assertEqual(final["carry"]["retried"], [1])
        self.assertEqual([(j["batch"], j["part"]) for j in final["carry"]["jobs"][1:]], [(1, ""), (1, "a"), (1, "b")])

    def test_a_rejected_answer_then_a_second_failure_fails_the_work_and_keeps_the_draft(self) -> None:
        self.attach = ["[block 2]\n> two\n", {"ok": False, "text": "", "finish_reason": "length",
                                              "error_code": "output_truncated", "error": "output_truncated: cut"}]
        final = self.settle(self.submit()["work_id"])
        self.assertEqual(final["status"], "failed")
        self.assertIn("carry attach batch 1a", final["failure"])
        self.assertEqual(final["carry"]["failure"]["stage"], "attach")
        self.assertEqual((self.root / "runs" / "operator" / final["work_id"] / "carry-draft.md").read_text(encoding="utf-8"), self.DRAFT)
        self.assertNotIn("delivery_artifacts", final)

    def test_a_failed_work_stage_fails_the_work_and_keeps_the_cut_draft(self) -> None:
        self.work = {"ok": False, "text": "# Lines\n\ncut", "finish_reason": "length", "error_code": "output_truncated",
                     "error": "output_truncated: cut"}
        final = self.settle(self.submit()["work_id"])
        self.assertEqual((final["status"], len(self.calls)), ("failed", 1))
        self.assertIn("carry work", final["failure"])
        self.assertEqual(final["carry"]["kept"]["output"]["file"], "carry-draft.partial.md")

    def test_submits_the_procedure_cannot_serve_are_refused_by_name(self) -> None:
        for label, overrides, reason in (("fast lane", {"lane": "fast"}, "deep lane on a backend that declares deliberate_max_tokens"),
                                         ("revise", {"revise": True}, "no revision round"),
                                         ("no brief", {"brief": None}, "requires a brief"),
                                         ("max_tokens", {"max_tokens": 4096}, "max_tokens is refused"),
                                         ("unknown", {"procedure": "other"}, "'carry' or absent")):
            with self.subTest(label), self.assertRaisesRegex(LocalWorkError, reason):
                self.submit(**overrides)
        self.assertEqual(self.calls, [])

    def test_a_restart_between_stages_resumes_and_a_restart_during_a_job_fails_the_work(self) -> None:
        self.hold = threading.Event()
        with mock.patch.object(self.service, "_spawn_auto_reconcile"):   # the process dies before its waiter reconciles
            work_id = self.submit()["work_id"]
            self.hold.set()
            for _ in range(100):
                if self.execution.get_job(self.service._read(work_id)["job_id"])["status"] == "succeeded":
                    break
                time.sleep(.02)
        self.assertEqual(self.service._read(work_id)["carry"]["stage"], "work")
        restarted = LocalWorkService(self.execution_service(self.execution._generate, recover=True), root=self.root,
                                     token_counter=lambda _p, _m, _q: 100)
        self.assertEqual(restarted.reconcile_all(), 1)   # the mount-time reconcile dispatches the attach stage
        for _ in range(300):
            final = restarted._read(work_id)
            if final["status"] not in {"queued", "running"}:
                break
            time.sleep(.02)
        restarted.execution.close()
        self.assertEqual(final["status"], "awaiting_review", final.get("failure"))
        gate = self.hold = threading.Event()     # the second work's thinking turn hangs in the seat
        def hanging(**kwargs):
            gate.wait(5)
            return {"ok": True, "text": "late", "finish_reason": "stop", "backend": kwargs["backend"], "model": kwargs["model"]}
        second = self.submit(idempotency_key="restart-2")
        for _ in range(100):
            if self.execution.get_job(self.service._read(second["work_id"])["job_id"])["status"] == "running":
                break
            time.sleep(.02)
        recovered = self.execution_service(hanging, recover=True)   # a gateway start: the running turn is closed, not replayed
        try:
            failed = LocalWorkService(recovered, root=self.root, token_counter=lambda _p, _m, _q: 100).reconcile(second["work_id"])
        finally:
            gate.set()
            recovered.close()
        self.assertEqual((failed["status"], failed["carry"]["failure"]["stage"]), ("failed", "work"))
        self.assertIn("never replayed", failed["failure"])

    def test_a_refused_dispatch_or_a_raising_render_fails_the_work_instead_of_leaving_it_queued(self) -> None:
        """Review of 2d50438: an exception out of a stage left the manifest queued with no waiter (the drain counts it
        busy for ever) and made reconcile_all raise at mount. Each is a failed stage now, named, the draft kept."""
        real = self.execution.submit
        def refusing(**kwargs):
            if kwargs["arguments"]["thinking"] is False:
                raise ExecutionServiceError("HEARTH dispatch is paused; restart the gateway after resume")
            return real(**kwargs)
        with mock.patch.object(self.execution, "submit", side_effect=refusing):
            final = self.settle(self.submit()["work_id"])
            self.assertEqual(self.service.reconcile_all(), 0)
        self.assertEqual((final["status"], final["carry"]["failure"]["stage"]), ("failed", "attach"))
        self.assertIn("carry attach batch 1: ExecutionServiceError: HEARTH dispatch is paused", final["failure"])
        self.assertTrue((self.root / "runs" / "operator" / final["work_id"] / "carry-draft.md").is_file())
        with mock.patch("hearth.delivery.carry.line_reference_agreement", side_effect=KeyError(7)):
            final = self.settle(self.submit()["work_id"])
        self.assertEqual((final["status"], final["carry"]["failure"]["stage"]), ("failed", "render"))
        with mock.patch.object(self.execution, "submit", side_effect=ExecutionServiceError("global execution queue is full")):
            with self.assertRaisesRegex(ExecutionServiceError, "queue is full"):
                self.submit(idempotency_key="refused-at-submit")
        on_disk = [json.loads(p.read_text(encoding="utf-8")) for p in (self.root / "runs" / "operator").glob("*/work-manifest.json")]
        self.assertEqual(sorted(m["status"] for m in on_disk), ["failed"] * 3)

    def test_a_draft_render_would_refuse_fails_at_the_work_stage_before_any_attach(self) -> None:
        self.DRAFT = "".join(f"# H{i}\n\nText {i}.\n\n" for i in range(17))   # 17 sections: assemble refuses
        final = self.settle(self.submit()["work_id"])
        self.assertEqual((final["status"], final["carry"]["failure"]["stage"], len(self.calls)), ("failed", "work", 1))
        self.assertIn("sections", final["failure"])


class DeepLaneFamilyTests(unittest.TestCase):
    """2026-09-27 (docs/sizing-map.md): the drain's default brief is code_fix on lane auto, so
    small code work went to the fast MoE lane although the family's own evidence pins the 27B."""

    def test_code_families_take_the_deep_lane_at_any_size(self) -> None:
        from hearth.localwork.service import LocalWorkService
        self.assertEqual(LocalWorkService._lane("auto", 500, "code_fix"), "deep")
        self.assertEqual(LocalWorkService._lane("auto", 500, "code_review"), "deep")
        self.assertEqual(LocalWorkService._lane("auto", 500, "drafting"), "fast")
        self.assertEqual(LocalWorkService._lane("auto", 9000, "drafting"), "deep")
        self.assertEqual(LocalWorkService._lane("fast", 500, "code_fix"), "fast")   # an explicit lane still wins

    def test_revision_coverage_keyed_schema_cannot_omit_summary(self):
        from hearth.delivery import revision, sourcemap
        original = {"summary": "Source has one constant.", "sections": [
            {"heading": "Details", "paragraphs": [{"text": "The constant is present.", "quotes": []}]}]}
        sch = revision.schema(original)["properties"]["coverage"]
        self.assertEqual(sch["type"], "object")
        self.assertEqual(set(sch["required"]), {"summary", "s0.p0"})
        rows = {cid: {"status": "retained", "p": 1, "revised_evidence": txt,
                      "source_quotes": [], "reason": "Identical assertion."}
                for cid, txt in revision.prose(original).items()}
        good = json.dumps({"coverage": rows, "criteria_preserved": True})
        self.assertEqual(revision.assess(good, original, original, None)["state"], "pass")
        del rows["summary"]
        with self.assertRaisesRegex(ValueError, "omitted"):
            revision.assess(json.dumps({"coverage": rows, "criteria_preserved": True}), original, original, None)


if __name__ == "__main__":
    unittest.main()
