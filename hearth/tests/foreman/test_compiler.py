from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from hearth.foreman import (
    ForemanProposalError,
    build_source_manifest,
    compile_workboard,
    make_checkpoint,
    write_checkpoint,
)


GUARD_CRITERIA = [
    "close_build_request may record knowledge/*.json in informational evidence and changed_files.",
    "Actual write tools remain blocked from writing the knowledge store.",
    "Fixture-taint protection remains intact.",
    "Regression tests cover allowed metadata and refused writes.",
]


class ForemanCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init")
        self._git("config", "user.email", "foreman@example.test")
        self._git("config", "user.name", "Foreman Test")
        (self.repo / "hearth" / "kernel").mkdir(parents=True)
        (self.repo / "hearth" / "tests" / "kernel").mkdir(parents=True)
        (self.repo / "hearth" / "kernel" / "guards.py").write_text("committed guard\n", encoding="utf-8")
        (self.repo / "hearth" / "tests" / "kernel" / "test_guards.py").write_text("committed test\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "pinned source")
        self.commit = self._git("rev-parse", "HEAD").strip()
        self.files = ["hearth/kernel/guards.py", "hearth/tests/kernel/test_guards.py"]
        self.manifest = build_source_manifest(self.repo, self.commit, self.files)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, text=True).stdout

    def _proposal(self, **overrides: object) -> dict:
        proposal = {
            "schema": "foreman-proposal.v1",
            "session_id": "flash-foreman-test",
            "task_envelopes": [{
                "id": "env-guard-knowledge-path",
                "intent": "Produce a bounded knowledge-path guard candidate.",
                "artifact_kind": "unified_diff",
                "target_path": "hearth/kernel/guards.py",
                "files": self.files,
                "acceptance_criteria": list(GUARD_CRITERIA),
                "requested_lane": "fast",
                "max_input_tokens": 4000,
                "output_reserve_tokens": 1000,
                "reviewer_notes": "Do not claim tests ran; caller validates the patch and focused tests.",
            }],
            "reviewer_notes": "Flash proposes scope only; the compiler owns source identity.",
            "next_decision": "Submit the guard task to a worker candidate lane.",
        }
        proposal.update(overrides)
        return proposal

    def _compile(self, proposal: dict) -> dict:
        return compile_workboard(
            proposal, repo=self.repo, source_manifest=self.manifest,
            goal="Create bounded, independently validated local-work candidates.",
            mandatory_criteria={"env-guard-knowledge-path": GUARD_CRITERIA},
        )

    def test_manifest_reads_pinned_blob_not_dirty_worktree(self) -> None:
        (self.repo / "hearth" / "kernel" / "guards.py").write_text("mutable worktree\n", encoding="utf-8")
        manifest = build_source_manifest(self.repo, self.commit, ["hearth/kernel/guards.py"])
        self.assertEqual(self.commit, manifest["base_commit"])
        self.assertNotEqual(manifest["files"][0]["sha256"],
                            __import__("hashlib").sha256(b"mutable worktree\n").hexdigest())

    def test_rejects_r2_empty_digest_forgery_before_compilation(self) -> None:
        with self.assertRaisesRegex(ForemanProposalError, "control-plane field"):
            self._compile(self._proposal(source_pack={"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}))

    def test_compiles_pinned_workboard_and_persists_json_checkpoint(self) -> None:
        workboard = self._compile(self._proposal())
        self.assertEqual(self.commit, workboard["base_commit"])
        self.assertEqual(self.manifest["sha256"], workboard["source_pack"]["sha256"])
        self.assertNotIn("source_pack", self._proposal())
        checkpoint = make_checkpoint(
            workboard, evidence_pointers=["runs/flash/session.json"],
            kv_state={"status": "saved", "path": "runs/flash/state.bin"},
        )
        self.assertEqual("json_checkpoint", checkpoint["kv_state"]["source_of_truth"])
        self.assertEqual(["env-guard-knowledge-path"], checkpoint["pending_envelopes"])
        output = Path(self.temporary.name) / "checkpoint.json"
        write_checkpoint(output, checkpoint)
        self.assertEqual(checkpoint, json.loads(output.read_text(encoding="utf-8")))

    def test_rejects_file_outside_manifest(self) -> None:
        proposal = self._proposal()
        proposal["task_envelopes"][0]["files"].append("hearth/operator/history.py")
        with self.assertRaisesRegex(ForemanProposalError, "outside the pinned manifest"):
            self._compile(proposal)

    def test_rejects_duplicate_write_target(self) -> None:
        proposal = self._proposal()
        duplicate = dict(proposal["task_envelopes"][0])
        duplicate["id"] = "env-guard-second"
        proposal["task_envelopes"].append(duplicate)
        with self.assertRaisesRegex(ForemanProposalError, "overlapping write targets"):
            self._compile(proposal)

    def test_rejects_missing_mandatory_guard_criterion(self) -> None:
        proposal = self._proposal()
        proposal["task_envelopes"][0]["acceptance_criteria"].pop()
        with self.assertRaisesRegex(ForemanProposalError, "omits mandatory criterion"):
            self._compile(proposal)

    def test_rejects_lane_context_overflow(self) -> None:
        proposal = self._proposal()
        proposal["task_envelopes"][0]["max_input_tokens"] = 65_000
        proposal["task_envelopes"][0]["output_reserve_tokens"] = 1_000
        with self.assertRaisesRegex(ForemanProposalError, "exact lane context"):
            self._compile(proposal)

    def test_rejects_target_path_escape(self) -> None:
        proposal = self._proposal()
        proposal["task_envelopes"][0]["target_path"] = "../outside.py"
        with self.assertRaisesRegex(ForemanProposalError, "escapes the repository"):
            self._compile(proposal)

    def test_rejects_tampered_manifest_before_compilation(self) -> None:
        manifest = dict(self.manifest)
        manifest["sha256"] = "0" * 64
        with self.assertRaisesRegex(ForemanProposalError, "source manifest sha256 mismatch"):
            compile_workboard(
                self._proposal(), repo=self.repo, source_manifest=manifest,
                goal="Create bounded, independently validated local-work candidates.",
            )

    def test_rejects_tampered_workboard_before_checkpoint(self) -> None:
        workboard = self._compile(self._proposal())
        workboard["source_pack"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ForemanProposalError, "source manifest sha256 mismatch"):
            make_checkpoint(workboard)


if __name__ == "__main__":
    unittest.main()
