from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface import build_requests as br
from hearth.toolsurface._scope import scope_root


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


class ReceiptLaneFixture(TestCase):
    """Temp receipt dir + temp git repo + a temp HEARTH_SCOPE that contains both.

    HEARTH_SCOPE is pointed at the temp tree because ``create_build_request`` now
    refuses a repo outside the sandbox roots (B-02): without it every fixture repo
    under %TEMP% would be out of scope. Nothing here touches the real receipt
    directory or the real repo.
    """

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
        self.env = self.enterContext(patch.dict(
            "os.environ", {"HEARTH_BACKENDS": str(self.pool), "HEARTH_SCOPE": str(self.tmp)}))

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

    def _head(self) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                              check=True, capture_output=True, text=True).stdout.strip()


class BuildRequestTests(ReceiptLaneFixture):
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


DRIVE_PATH_RE = r"[A-Za-z]:[\\/]"
PLAN_ID = "hearth-fake-plan-0001"
WINNER = "cc-builder-2"
DELIVERABLE = "proposals/thing.md"
CRITERION_WITH_DELIVERABLE = f"{DELIVERABLE} records the tradeoffs"
CRITERION_WITHOUT = "a human reviewed the proposal"


class _SubmitSpy:
    """Stands in for task_lane.submit_task. Records every call; never touches SSH."""

    def __init__(self, result: dict | None = None, raises: BaseException | None = None):
        self.calls: list[dict] = []
        self._result = result if result is not None else {
            "ok": True, "plan_id": PLAN_ID, "builders": ["cc-builder-2", "cc-builder-3"],
            "inbox_path": f"/home/claude/work/commandcenter/inbox/{PLAN_ID}.md",
        }
        self._raises = raises

    def __call__(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._result


class _StatusSpy:
    """Stands in for task_lane.task_status. The LAST result repeats forever."""

    def __init__(self, *results: dict):
        self.calls: list[str] = []
        self.results = list(results)

    def __call__(self, plan_id):
        self.calls.append(plan_id)
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


class _HarvestSpy:
    """Stands in for fleet_harvest.harvest_fleet_run. NEVER does real git/SSH."""

    def __init__(self, result: dict | None = None, raises: BaseException | None = None):
        self.calls: list[str] = []
        self._result = result
        self._raises = raises

    def __call__(self, plan_id):
        self.calls.append(plan_id)
        if self._raises is not None:
            raise self._raises
        return self._result if self._result is not None else harvest_ok(plan_id)


def harvest_ok(plan_id: str) -> dict:
    return {
        "ok": True, "plan_id": plan_id, "count": 2, "pushed": True,
        "github_prefix": f"fleet/{plan_id}",
        "workers": [
            {"worker_ref": f"{WINNER}/lap1", "sha": "a" * 40,
             "github_branch": f"fleet/{plan_id}/{WINNER}/lap1"},
            {"worker_ref": "cc-builder-3/lap1", "sha": "b" * 40,
             "github_branch": f"fleet/{plan_id}/cc-builder-3/lap1"},
        ],
    }


def status_pending(plan_id: str = PLAN_ID) -> dict:
    return {"ok": True, "done": False, "plan_id": plan_id,
            "result_path": f"runs/{plan_id}/result.json"}


def status_unreachable(plan_id: str = PLAN_ID) -> dict:
    return {"ok": False, "done": False, "plan_id": plan_id,
            "error": "ssh exit 255: connection refused"}


def status_done(winner=WINNER, plan_id: str = PLAN_ID) -> dict:
    return {"ok": True, "done": True, "plan_id": plan_id,
            "result": {"ok": True, "winner": winner, "plan_id": plan_id}}


class DelegatedBuildRequestTests(ReceiptLaneFixture):
    def test_manual_sync_never_harvests_or_pushes_and_survives_reload(self):
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy(), promotion_policy="manual",
                      runner_preset="am4-shared-27b", operator="hermes")
        observed = status_done()
        observed["result"]["promotion"] = {"promoted": False, "status": "awaiting_review",
            "candidates": [{"worker": WINNER, "branch": "local/candidate", "commit": "abc"}]}
        def forbidden(*args):
            self.fail("manual sync must not harvest/push")
        synced = self.sync(receipt, status_fn=_StatusSpy(observed), harvest_fn=forbidden)
        self.assertEqual(synced["execution"]["delegation"]["result"], "awaiting_review")
        self.assertFalse(synced["execution"]["delegation"]["harvested"])
        fresh = importlib.reload(br)
        again = fresh.update_build_request(receipt["receipt_id"], sync_delegation=True,
            receipt_dir=str(self.receipts), status_fn=forbidden, harvest_fn=forbidden)
        self.assertTrue(again["already_synced"])

    """execute(mode='delegate') + update(sync_delegation=True).

    Every collaborator that would do real git/SSH is injected as a spy: no test in
    this class can reach cc-conductor, GitHub, or the real receipt directory.
    """

    def delegable(self, **kwargs) -> dict:
        args = {
            "acceptance_criteria": [CRITERION_WITH_DELIVERABLE, CRITERION_WITHOUT],
            "deliverables": [DELIVERABLE],
        }
        args.update(kwargs)
        return self.create(**args)

    def events(self, receipt: dict) -> list[dict]:
        text = Path(receipt["events_path"]).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def kinds(self, receipt: dict) -> list[str]:
        return [event["event"] for event in self.events(receipt)]

    def delegate(self, receipt: dict, submit: _SubmitSpy, **kwargs) -> dict:
        return br.execute_build_request(
            receipt["receipt_id"], mode="delegate", receipt_dir=str(self.receipts),
            submit_fn=submit, **kwargs)

    def sync(self, receipt: dict, **kwargs) -> dict:
        return br.update_build_request(
            receipt["receipt_id"], sync_delegation=True,
            receipt_dir=str(self.receipts), **kwargs)

    def delegation(self, receipt: dict) -> dict:
        current = br.get_build_request(receipt["receipt_id"], receipt_dir=str(self.receipts))
        return current["execution"].get("delegation")

    # --- allowed roots -----------------------------------------------------

    def test_repo_defaults_to_the_primary_scope_root(self) -> None:
        with patch.dict("os.environ", {"HEARTH_SCOPE": str(self.repo)}):
            receipt = br.create_build_request(
                title="scoped", request="do it",
                acceptance_criteria=["one"], receipt_dir=str(self.receipts))
            self.assertEqual(receipt["repo"], str(scope_root()))
        self.assertEqual(receipt["repo"], str(self.repo.resolve()))

    def test_repo_outside_the_scope_roots_is_refused(self) -> None:
        outside = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with self.assertRaises(ValueError) as caught:
            self.create(repo=str(outside))
        self.assertIn("sandbox", str(caught.exception))

    def test_repo_that_does_not_exist_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.create(repo=str(self.tmp / "no-such-repo"))
        self.assertIn("not an existing directory", str(caught.exception))

    # --- deliverable validation -------------------------------------------

    def test_deliverable_shapes_are_refused_at_create_time(self) -> None:
        for bad in (["../escape.md"], ["/abs/thing.md"], [r"C:\drive\thing.md"],
                    ["docs\\windows.md"], [""], ["   "], [], ["ok.md", None],
                    ["nul\x00.md"], ["line\nbreak.md"],
                    [f"f{i}.md" for i in range(br.MAX_DELIVERABLES + 1)]):
            with self.subTest(deliverables=bad):
                with self.assertRaises(ValueError):
                    self.create(deliverables=bad)

    def test_delegate_without_deliverables_never_submits(self) -> None:
        receipt = self.create()  # no deliverables at all
        submit = _SubmitSpy()
        with self.assertRaises(ValueError):
            self.delegate(receipt, submit)
        self.assertEqual(submit.calls, [])
        self.assertEqual(self.kinds(receipt), ["created"])

    def test_hand_edited_bad_deliverable_is_refused_before_submit(self) -> None:
        receipt = self.delegable()
        path = Path(receipt["receipt_path"])
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["deliverables"] = ["../escape.md"]
        path.write_text(json.dumps(tampered), encoding="utf-8")
        submit = _SubmitSpy()
        with self.assertRaises(ValueError):
            self.delegate(receipt, submit)
        self.assertEqual(submit.calls, [])

    # --- delegate ----------------------------------------------------------

    def test_delegate_submits_the_brief_and_records_one_delegation(self) -> None:
        receipt = self.delegable()
        submit = _SubmitSpy()
        delegated = self.delegate(receipt, submit, builders=["cc-builder-2"],
                                  max_age_s=21600, task_class="build")
        self.assertEqual(len(submit.calls), 1)
        call = submit.calls[0]
        self.assertEqual(call["requires"], [DELIVERABLE])
        self.assertEqual(call["max_age_s"], 21600)
        self.assertEqual(call["task_class"], "build")
        self.assertEqual(call["builders"], ["cc-builder-2"])
        self.assertEqual(call["plan_id_hint"], receipt["receipt_id"])

        prompt = call["prompt"]
        self.assertIn("Build thing", prompt)
        self.assertIn("Implement the thing.", prompt)
        self.assertIn(f"1. {CRITERION_WITH_DELIVERABLE}", prompt)
        self.assertIn(f"2. {CRITERION_WITHOUT}", prompt)
        self.assertIn(f"- {DELIVERABLE}", prompt)
        self.assertIn("~/commandcenter-src", prompt)
        self.assertNotRegex(prompt, DRIVE_PATH_RE)

        self.assertEqual(delegated["status"], "running")
        record = delegated["execution"]["delegation"]
        self.assertEqual(record["plan_id"], PLAN_ID)
        self.assertEqual(record["requires"], [DELIVERABLE])
        self.assertEqual(record["state"], "submitted")
        self.assertFalse(record["harvested"])
        self.assertIsNone(record["completed_at"])
        self.assertIsNone(record["result"])
        self.assertEqual(self.kinds(receipt).count("delegated"), 1)

        event = [e for e in self.events(receipt) if e["event"] == "delegated"][0]
        self.assertNotIn("prompt", event)
        self.assertEqual(event["prompt_sha256"],
                         hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def test_delegate_refuses_a_brief_carrying_a_windows_path(self) -> None:
        receipt = self.delegable(request=r"Read C:\work\commandcenter\hearth and fix it.")
        submit = _SubmitSpy()
        with self.assertRaises(ValueError) as caught:
            self.delegate(receipt, submit)
        self.assertIn("Windows absolute path", str(caught.exception))
        self.assertEqual(submit.calls, [])

    def test_delegate_failure_blocks_the_receipt_and_records_no_delegation(self) -> None:
        receipt = self.delegable()
        submit = _SubmitSpy(result={"ok": False, "error": "ssh exit 255: refused"})
        blocked = self.delegate(receipt, submit)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["execution"].get("delegation"))
        self.assertEqual(self.kinds(receipt).count("delegation_failed"), 1)
        self.assertEqual(self.kinds(receipt).count("delegated"), 0)
        self.assertTrue(any("ssh exit 255" in line
                            for line in blocked["execution"]["evidence"]))

    def test_a_raising_submit_fn_blocks_without_a_partial_projection(self) -> None:
        receipt = self.delegable()
        ledger = Path(receipt["ledger_path"])
        before = len(ledger.read_text(encoding="utf-8").splitlines())
        submit = _SubmitSpy(raises=RuntimeError("inbox write exploded"))
        blocked = self.delegate(receipt, submit)
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["execution"].get("delegation"))
        self.assertEqual(self.kinds(receipt).count("delegation_failed"), 1)
        self.assertTrue(any("RuntimeError: inbox write exploded" in line
                            for line in blocked["execution"]["evidence"]))
        after = len(ledger.read_text(encoding="utf-8").splitlines())
        self.assertEqual(after - before, 1, "exactly one projection write per delegate call")

    def test_a_second_delegate_submits_nothing_and_appends_nothing(self) -> None:
        receipt = self.delegable()
        submit = _SubmitSpy()
        self.delegate(receipt, submit)
        events_before = Path(receipt["events_path"]).read_text(encoding="utf-8")
        receipt_before = Path(receipt["receipt_path"]).read_text(encoding="utf-8")

        duplicate = self.delegate(receipt, submit)
        self.assertTrue(duplicate["duplicate_delegation"])
        self.assertEqual(len(submit.calls), 1)
        self.assertEqual(Path(receipt["events_path"]).read_text(encoding="utf-8"),
                         events_before)
        self.assertEqual(Path(receipt["receipt_path"]).read_text(encoding="utf-8"),
                         receipt_before)

    # --- sync: still running ----------------------------------------------

    def test_sync_requires_a_delegation(self) -> None:
        receipt = self.delegable()
        with self.assertRaises(ValueError):
            self.sync(receipt, status_fn=_StatusSpy(status_pending()))

    def test_three_unchanged_syncs_append_no_event_and_a_change_appends_one(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        status = _StatusSpy(status_pending())
        for _ in range(3):
            self.sync(receipt, status_fn=status)
        self.assertEqual(len(status.calls), 3)
        self.assertEqual(self.kinds(receipt).count("delegation_synced"), 0)
        self.assertEqual(self.delegation(receipt)["last_status"], "pending")
        self.assertTrue(self.delegation(receipt)["last_sync"])

        changed = self.sync(receipt, status_fn=_StatusSpy(status_unreachable()))
        self.assertTrue(changed["delegation_status_changed"])
        self.assertEqual(self.kinds(receipt).count("delegation_synced"), 1)
        event = [e for e in self.events(receipt) if e["event"] == "delegation_synced"][0]
        self.assertEqual(event["status"], "unreachable")
        self.assertEqual(event["previous"], "pending")

    # --- sync: done --------------------------------------------------------

    def test_sync_done_harvests_once_and_passes_only_named_criteria(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        harvest = _HarvestSpy()
        seen: list[str] = []

        def files(branch):
            seen.append(branch)
            return [DELIVERABLE, "README.md"]

        synced = self.sync(receipt, status_fn=_StatusSpy(status_done()),
                           harvest_fn=harvest, list_files_fn=files)
        self.assertEqual(synced["delegation_result"], "accepted")
        self.assertEqual(synced["status"], "running")  # never done, never blocked
        self.assertEqual(harvest.calls, [PLAN_ID])
        self.assertEqual(seen, [f"origin/fleet/{PLAN_ID}/{WINNER}/lap1"])

        record = synced["execution"]["delegation"]
        self.assertTrue(record["harvested"])
        self.assertEqual(record["state"], "completed")
        self.assertTrue(record["completed_at"])
        self.assertEqual(record["branches"],
                         [f"fleet/{PLAN_ID}/{WINNER}/lap1",
                          f"fleet/{PLAN_ID}/cc-builder-3/lap1"])

        rows = {row["criterion"]: row for row in synced["validation"]}
        self.assertEqual(rows[CRITERION_WITH_DELIVERABLE]["status"], "passed")
        self.assertEqual(rows[CRITERION_WITH_DELIVERABLE]["evidence"],
                         f"origin/fleet/{PLAN_ID}/{WINNER}/lap1: {DELIVERABLE} matched")
        self.assertEqual(rows[CRITERION_WITHOUT]["status"], "not_run")
        self.assertEqual(rows[CRITERION_WITHOUT]["evidence"], "")
        self.assertEqual(self.kinds(receipt).count("delegation_completed"), 1)

        # A deliverable-backed pass is NOT a licence to close done.
        with self.assertRaises(ValueError):
            br.close_build_request(
                receipt["receipt_id"], "done", "deliverable present",
                [rows[CRITERION_WITH_DELIVERABLE]], receipt_dir=str(self.receipts))
        closed = br.close_build_request(
            receipt["receipt_id"], "done", "reviewed and accepted",
            [rows[CRITERION_WITH_DELIVERABLE],
             {"criterion": CRITERION_WITHOUT, "status": "passed",
              "evidence": "reviewed by Derek 2026-09-06"}],
            receipt_dir=str(self.receipts))
        self.assertEqual(closed["status"], "done")

    def test_sync_done_with_missing_deliverables_blocks_and_passes_nothing(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        synced = self.sync(receipt, status_fn=_StatusSpy(status_done()),
                           harvest_fn=_HarvestSpy(),
                           list_files_fn=lambda branch: ["README.md"])
        self.assertEqual(synced["delegation_result"], "acceptance_failed")
        self.assertEqual(synced["status"], "blocked")
        self.assertEqual(synced["execution"]["delegation"]["missing_globs"], [DELIVERABLE])
        self.assertTrue(all(row["status"] != "passed" for row in synced["validation"]))
        completed = [e for e in self.events(receipt)
                     if e["event"] == "delegation_completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["missing_globs"], [DELIVERABLE])
        self.assertTrue(completed[0]["winner_present"])

    def test_sync_done_without_a_winner_still_harvests_exactly_once(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        harvest = _HarvestSpy()
        synced = self.sync(receipt, status_fn=_StatusSpy(status_done(winner=None)),
                           harvest_fn=harvest,
                           list_files_fn=lambda branch: self.fail("acceptance must not run"))
        self.assertEqual(synced["delegation_result"], "no_winner")
        self.assertEqual(synced["status"], "blocked")
        self.assertEqual(harvest.calls, [PLAN_ID])
        completed = [e for e in self.events(receipt)
                     if e["event"] == "delegation_completed"][0]
        self.assertFalse(completed["winner_present"])
        self.assertEqual(completed["branches_count"], 2)

    def test_sync_done_with_no_branch_for_the_winner_blocks(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        lonely = harvest_ok(PLAN_ID)
        lonely["workers"] = [w for w in lonely["workers"] if WINNER not in w["worker_ref"]]
        synced = self.sync(receipt, status_fn=_StatusSpy(status_done()),
                           harvest_fn=_HarvestSpy(result=lonely),
                           list_files_fn=lambda branch: self.fail("acceptance must not run"))
        self.assertEqual(synced["delegation_result"], "winner_branch_missing")
        self.assertEqual(synced["status"], "blocked")

    def test_harvest_failure_blocks_and_is_not_retried(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        harvest = _HarvestSpy(result={"ok": False, "plan_id": PLAN_ID, "stage": "push",
                                      "error": "git push exit 128: permission denied"})
        status = _StatusSpy(status_done())
        synced = self.sync(receipt, status_fn=status, harvest_fn=harvest)
        self.assertEqual(synced["delegation_result"], "harvest_failed")
        self.assertEqual(synced["status"], "blocked")
        self.assertTrue(any("permission denied" in line
                            for line in synced["execution"]["evidence"]))

        again = self.sync(receipt, status_fn=status, harvest_fn=harvest)
        self.assertTrue(again["already_synced"])
        self.assertEqual(len(harvest.calls), 1)
        self.assertEqual(len(status.calls), 1)

    def test_repeat_sync_after_completion_calls_nothing_and_writes_nothing(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        self.sync(receipt, status_fn=_StatusSpy(status_done()), harvest_fn=_HarvestSpy(),
                  list_files_fn=lambda branch: [DELIVERABLE])
        events_before = Path(receipt["events_path"]).read_text(encoding="utf-8")
        receipt_before = Path(receipt["receipt_path"]).read_text(encoding="utf-8")
        ledger_before = Path(receipt["ledger_path"]).read_text(encoding="utf-8")

        status, harvest = _StatusSpy(status_done()), _HarvestSpy()
        again = self.sync(receipt, status_fn=status, harvest_fn=harvest)
        self.assertTrue(again["already_synced"])
        self.assertEqual(status.calls, [])
        self.assertEqual(harvest.calls, [])
        self.assertEqual(Path(receipt["events_path"]).read_text(encoding="utf-8"),
                         events_before)
        self.assertEqual(Path(receipt["receipt_path"]).read_text(encoding="utf-8"),
                         receipt_before)
        self.assertEqual(Path(receipt["ledger_path"]).read_text(encoding="utf-8"),
                         ledger_before)

    # --- fault injection ---------------------------------------------------

    def test_a_crash_inside_harvest_never_harvests_twice(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        harvest = _HarvestSpy(raises=RuntimeError("fetch died mid-push"))
        with self.assertRaises(RuntimeError):
            self.sync(receipt, status_fn=_StatusSpy(status_done()), harvest_fn=harvest)
        # persist-first: the guard reached disk BEFORE the side effect.
        self.assertTrue(self.delegation(receipt)["harvested"])
        self.assertEqual(len(harvest.calls), 1)

        recovered = self.sync(receipt, status_fn=_StatusSpy(status_done()),
                              harvest_fn=harvest)
        self.assertEqual(len(harvest.calls), 1, "a crashed harvest is never replayed")
        self.assertEqual(recovered["delegation_result"], "harvest_incomplete")
        self.assertEqual(recovered["status"], "blocked")
        self.assertEqual(self.kinds(receipt).count("delegation_completed"), 1)

    def test_a_corrupt_event_line_does_not_break_the_projection(self) -> None:
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        with Path(receipt["events_path"]).open("a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        # The projection is the read model; events.jsonl is append-only history and
        # is never parsed by this lane, so the corrupt line changes nothing.
        current = br.get_build_request(receipt["receipt_id"], receipt_dir=str(self.receipts))
        self.assertEqual(current["execution"]["delegation"]["plan_id"], PLAN_ID)
        synced = self.sync(receipt, status_fn=_StatusSpy(status_done()),
                           harvest_fn=_HarvestSpy(),
                           list_files_fn=lambda branch: [DELIVERABLE])
        self.assertEqual(synced["delegation_result"], "accepted")

    # --- restart -----------------------------------------------------------

    def test_a_restart_reconstructs_the_delegation_from_disk_alone(self) -> None:
        receipt = self.delegable(execute=True)
        self.delegate(receipt, _SubmitSpy())
        self.sync(receipt, status_fn=_StatusSpy(status_pending()))

        fresh = importlib.reload(br)  # no in-memory state survives this
        reloaded = fresh.get_build_request(receipt["receipt_id"],
                                           receipt_dir=str(self.receipts))
        self.assertEqual(reloaded["execution"]["delegation"]["plan_id"], PLAN_ID)

        synced = fresh.update_build_request(
            receipt["receipt_id"], sync_delegation=True, receipt_dir=str(self.receipts),
            status_fn=_StatusSpy(status_done()), harvest_fn=_HarvestSpy(),
            list_files_fn=lambda branch: [DELIVERABLE])
        self.assertEqual(synced["delegation_result"], "accepted")

        kinds = self.kinds(receipt)
        self.assertEqual(kinds, ["created", "execution_started", "delegated",
                                 "delegation_completed"])
        self.assertEqual(kinds.count("delegated"), 1)
        self.assertEqual(kinds.count("delegation_completed"), 1)

    # --- no litter ---------------------------------------------------------

    def test_a_whole_delegation_cycle_creates_nothing_under_hearth_var(self) -> None:
        var = Path(br.__file__).resolve().parents[1] / "var"
        before = sorted(str(p) for p in var.rglob("*")) if var.is_dir() else None
        receipt = self.delegable()
        self.delegate(receipt, _SubmitSpy())
        self.sync(receipt, status_fn=_StatusSpy(status_pending()))
        self.sync(receipt, status_fn=_StatusSpy(status_done()), harvest_fn=_HarvestSpy(),
                  list_files_fn=lambda branch: [DELIVERABLE])
        after = sorted(str(p) for p in var.rglob("*")) if var.is_dir() else None
        self.assertEqual(before, after)
