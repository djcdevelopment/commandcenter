from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface import build_requests as br


POOL = textwrap.dedent("""
    default = "omen-ollama"

    [[backend]]
    name = "omen-ollama"
    endpoint = "http://127.0.0.1:11434"
    api = "ollama"
    models = ["qwen3-coder:30b"]
    tags = ["default", "code"]

    [[backend]]
    name = "gcp-gemini"
    endpoint = "https://aiplatform.googleapis.com"
    api = "gemini"
    models = ["gemini-3.5-flash"]
    tags = ["frontier", "cloud-overflow"]
""")


class BuildRequestTests(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.tmp / "repo"
        self.receipts = self.tmp / "receipts"
        self.pool = self.tmp / "backends.toml"
        self.pool.write_text(POOL, encoding="utf-8")
        self.repo.mkdir()
        subprocess.run(["git", "init"], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        self.env = self.enterContext(patch.dict("os.environ", {"HEARTH_BACKENDS": str(self.pool)}))

    def create(self, **kwargs) -> dict:
        args = {
            "title": "Build thing",
            "request": "Implement the thing.",
            "acceptance_criteria": ["criterion one", "criterion two"],
            "repo": str(self.repo),
            "receipt_dir": str(self.receipts),
        }
        args.update(kwargs)
        return br.create_build_request(**args)

    def validation(self) -> list[dict]:
        return [
            {"criterion": "criterion one", "status": "passed", "evidence": "unit test A"},
            {"criterion": "criterion two", "status": "passed", "evidence": "unit test B"},
        ]

    def test_request_creation_returns_receipt_id_and_paths(self) -> None:
        receipt = self.create()
        self.assertRegex(receipt["receipt_id"], r"^br-\d{8}-\d{6}-[a-f0-9]{8}$")
        self.assertEqual(receipt["status"], "open")
        self.assertTrue(Path(receipt["request_path"]).is_file())
        self.assertTrue(Path(receipt["receipt_path"]).is_file())
        self.assertTrue(Path(receipt["ledger_path"]).is_file())

    def test_config_and_argument_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.create(title="")
        with self.assertRaises(ValueError):
            self.create(request=" ")
        with self.assertRaises(ValueError):
            self.create(acceptance_criteria=[])
        with self.assertRaises(ValueError):
            self.create(backend="ghost")

    def test_original_request_is_immutable_across_updates(self) -> None:
        receipt = self.create(request="Original request.")
        request_path = Path(receipt["request_path"])
        before = request_path.read_text(encoding="utf-8")
        br.update_build_request(receipt["receipt_id"], summary="updated",
                                receipt_dir=str(self.receipts))
        after = request_path.read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_backend_routing_capture(self) -> None:
        receipt = self.create(backend="gcp-gemini", task="cloud-overflow")
        self.assertEqual(receipt["backend"], "gcp-gemini")
        self.assertEqual(receipt["routing_reason"], "pinned:gcp-gemini")

    def test_repo_state_capture(self) -> None:
        receipt = self.create()
        self.assertEqual(receipt["repo_before"]["head"], self._head())
        self.assertFalse(receipt["repo_before"]["dirty"])

    def test_pre_existing_dirty_file_handling(self) -> None:
        (self.repo / "dirty.txt").write_text("preexisting\n", encoding="utf-8")
        receipt = self.create()
        self.assertEqual(receipt["pre_existing_dirty_files"], ["dirty.txt"])
        (self.repo / "new.txt").write_text("request\n", encoding="utf-8")
        closed = br.close_build_request(
            receipt["receipt_id"], "done", "closed", self.validation(),
            receipt_dir=str(self.receipts),
        )
        self.assertNotIn("dirty.txt", closed["changed_files"])
        self.assertIn("new.txt", closed["changed_files"])
        self.assertIn("new.txt", closed["request_changed_files"])
        self.assertNotIn("dirty.txt", closed["request_changed_files"])

    def test_execution_failure_can_close_failed(self) -> None:
        receipt = self.create(execute=True)
        br.update_build_request(receipt["receipt_id"], status="running",
                                tool_call={"tool": "fake", "ok": False, "error": "boom"},
                                receipt_dir=str(self.receipts))
        closed = br.close_build_request(
            receipt["receipt_id"], "failed", "tool failed",
            [{"criterion": "criterion one", "status": "failed", "evidence": "boom"},
             {"criterion": "criterion two", "status": "not_run", "evidence": ""}],
            receipt_dir=str(self.receipts),
        )
        self.assertEqual(closed["status"], "failed")

    def test_blocked_result(self) -> None:
        receipt = self.create()
        closed = br.close_build_request(
            receipt["receipt_id"], "blocked", "waiting for external access",
            [{"criterion": "criterion one", "status": "not_run", "evidence": ""},
             {"criterion": "criterion two", "status": "not_run", "evidence": ""}],
            receipt_dir=str(self.receipts),
        )
        self.assertEqual(closed["status"], "blocked")

    def test_done_requires_acceptance_validation(self) -> None:
        receipt = self.create()
        with self.assertRaises(ValueError):
            br.close_build_request(
                receipt["receipt_id"], "done", "not enough",
                [{"criterion": "criterion one", "status": "passed", "evidence": "ok"}],
                receipt_dir=str(self.receipts),
            )

    def test_closure_captures_commit_and_changed_files(self) -> None:
        receipt = self.create()
        (self.repo / "feature.txt").write_text("done\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "feature"], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        closed = br.close_build_request(
            receipt["receipt_id"], "done", "done", self.validation(),
            receipt_dir=str(self.receipts),
        )
        self.assertEqual(closed["status"], "done")
        self.assertEqual(closed["commits"], [self._head()])

    def test_duplicate_closure_prevention(self) -> None:
        receipt = self.create()
        closed = br.close_build_request(
            receipt["receipt_id"], "done", "done", self.validation(),
            receipt_dir=str(self.receipts),
        )
        event_path = Path(closed["events_path"])
        before = event_path.read_text(encoding="utf-8")
        duplicate = br.close_build_request(
            receipt["receipt_id"], "done", "done again", self.validation(),
            receipt_dir=str(self.receipts),
        )
        after = event_path.read_text(encoding="utf-8")
        self.assertTrue(duplicate["duplicate_close"])
        self.assertEqual(before, after)

    def test_secret_redaction(self) -> None:
        receipt = self.create(request="Use token sk-secret123 and password=abc")
        text = Path(receipt["request_path"]).read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", text)
        br.update_build_request(receipt["receipt_id"],
                                tool_call={"api_token": "ya29.secret-token"},
                                receipt_dir=str(self.receipts))
        event_text = Path(receipt["events_path"]).read_text(encoding="utf-8")
        self.assertIn("[REDACTED]", event_text)
        self.assertNotIn("ya29.secret-token", event_text)

    def test_list_and_get_behavior(self) -> None:
        first = self.create(title="first")
        second = self.create(title="second", execute=True)
        fetched = br.get_build_request(first["receipt_id"], receipt_dir=str(self.receipts))
        self.assertEqual(fetched["title"], "first")
        running = br.list_build_requests(status="running", receipt_dir=str(self.receipts))
        self.assertEqual([item["receipt_id"] for item in running["requests"]],
                         [second["receipt_id"]])

    def test_execute_build_request_records_routing(self) -> None:
        receipt = self.create()
        executed = br.execute_build_request(
            receipt["receipt_id"], mode="agent", backend="gcp-gemini",
            evidence="delegated to agent", receipt_dir=str(self.receipts),
        )
        self.assertEqual(executed["status"], "running")
        self.assertEqual(executed["backend"], "gcp-gemini")
        self.assertEqual(executed["routing_reason"], "pinned:gcp-gemini")

    def _head(self) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                              check=True, capture_output=True, text=True).stdout.strip()
