"""Tests for campaign/mechnet_exerciser.py -- M6, dry-run only.

Nothing here reaches the fleet: submit_task / task_status are injected fakes, and the
dry-run default is asserted to call neither. Run from the repo root:

    fleet-worker-node/.venv-omen/Scripts/python.exe -m pytest campaign/test_mechnet_exerciser.py -q -p no:cacheprovider
"""
from __future__ import annotations

import io
import json
import re
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from campaign import mechnet_exerciser as ex
from hearth.toolsurface.task_lane import DEFAULT_BUILDERS

REPO = Path(__file__).resolve().parent.parent
_PATH_RE = re.compile(r"\b((?:hearth|docs|campaign|fleet)/[A-Za-z0-9_./-]+\.(?:py|md|toml))\b")


class _Recorder:
    """A fake submit_task / task_status that records calls and answers like the real one."""

    def __init__(self, ok: bool = True, status: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.ok = ok
        self.status = status

    def submit(self, prompt, builders=None, plan_id_hint=None, task_class=None, est_tokens=None):
        self.calls.append({"prompt": prompt, "builders": builders, "plan_id_hint": plan_id_hint,
                           "task_class": task_class, "est_tokens": est_tokens})
        if not self.ok:
            return {"ok": False, "error": "ssh exit 255: denied", "plan_id": f"hearth-{plan_id_hint}-deadbeef",
                    "builders": builders}
        return {"ok": True, "plan_id": f"hearth-{plan_id_hint}-deadbeef", "builders": list(builders),
                "inbox_path": f"/home/claude/work/commandcenter/inbox/hearth-{plan_id_hint}-deadbeef.md"}

    def task_status(self, plan_id):
        self.calls.append({"status_for": plan_id})
        return self.status


def _main(argv, rec: _Recorder | None = None) -> tuple[int, str]:
    rec = rec or _Recorder()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ex.main(argv, submit_fn=rec.submit, status_fn=rec.task_status)
    return rc, buf.getvalue()


class BriefShapeTests(TestCase):
    def test_exactly_two_briefs_one_per_loop(self) -> None:
        self.assertEqual(len(ex.BRIEFS), 2)
        self.assertEqual(sorted(b.loop for b in ex.BRIEFS), ["critic", "planning"])
        self.assertEqual(len({b.slug for b in ex.BRIEFS}), 2)

    def test_prompt_has_the_revival_probe_shape(self) -> None:
        for brief in ex.BRIEFS:
            prompt = ex.build_prompt(brief)
            self.assertIn("READ-ONLY source at ~/commandcenter-src", prompt)
            self.assertIn(f"proposals/{brief.slug}.md", prompt)
            self.assertIn("commit only that", prompt)
            self.assertIn("NO production code", prompt)
            self.assertIn("0008-scheduler-advisory-first", prompt)
            # PREAMBLE first, POSTAMBLE last, the question in between.
            self.assertTrue(prompt.startswith("MECHNET EXERCISER BRIEF"))
            self.assertTrue(prompt.rstrip().endswith("harvested by hand."))
            self.assertIn(brief.question, prompt)
            self.assertNotIn("{slug}", prompt)

    def test_every_cited_path_exists_in_the_repo(self) -> None:
        # Grounding: a brief that cites a file that does not exist teaches the builder
        # to invent. Every hearth/ docs/ path in the briefs must be real here.
        for brief in ex.BRIEFS:
            cited = set(_PATH_RE.findall(ex.build_prompt(brief)))
            self.assertTrue(cited, f"{brief.slug} cites no source files")
            for rel in cited:
                self.assertTrue((REPO / rel).exists(), f"{brief.slug} cites missing {rel}")

    def test_planning_brief_asks_for_the_scrum_master_fields(self) -> None:
        planning = next(b for b in ex.BRIEFS if b.loop == "planning")
        for needle in ("epic", "task_class", "est_tokens", "acceptance", "submit_task"):
            self.assertIn(needle, planning.question)

    def test_critic_brief_asks_for_grade_risk_and_rubric(self) -> None:
        critic = next(b for b in ex.BRIEFS if b.loop == "critic")
        for needle in ("rubric", "quality grade A-F", "risk score 0.0-1.0", "objections", "experiment"):
            self.assertIn(needle, critic.question)


class EstimateAndPlanTests(TestCase):
    def test_estimate_tokens_is_prompt_plus_deliverable_reserve(self) -> None:
        self.assertEqual(ex.estimate_tokens("x" * 400), 100 + ex.RESEARCH_OUT_TOKENS)
        self.assertEqual(ex.estimate_tokens("x" * 401), 101 + ex.RESEARCH_OUT_TOKENS)
        self.assertEqual(ex.estimate_tokens("", out_tokens=0), 0)

    def test_plan_matches_the_submit_task_call_shape(self) -> None:
        plan = ex.plan_submissions()
        self.assertEqual(len(plan), 2)
        for item in plan:
            self.assertEqual(item["task_class"], "research")
            self.assertEqual(item["builders"], DEFAULT_BUILDERS)   # imported roster, not a copy
            self.assertTrue(item["plan_id_hint"].startswith("exerciser-"))
            self.assertIsInstance(item["est_tokens"], int)
            self.assertEqual(item["est_tokens"], ex.estimate_tokens(item["prompt"]))
            self.assertEqual(item["prompt_chars"], len(item["prompt"]))
            self.assertEqual(item["deliverable"], f"proposals/{item['slug']}.md")

    def test_default_roster_meets_fanout_minimum(self) -> None:
        self.assertGreaterEqual(len(ex.plan_submissions()[0]["builders"]), 2)

    def test_builders_override_and_unknown_only_slug(self) -> None:
        plan = ex.plan_submissions(builders=["cc-builder-1", "cc-builder-2"])
        self.assertEqual(plan[0]["builders"], ["cc-builder-1", "cc-builder-2"])
        with self.assertRaises(ValueError):
            ex.run(go=False, only="no-such-brief", submit_fn=_Recorder().submit)


class DryRunDefaultTests(TestCase):
    def test_default_invocation_dispatches_nothing(self) -> None:
        rec = _Recorder()
        rc, out = _main([], rec)
        self.assertEqual(rc, 0)
        self.assertEqual(rec.calls, [])
        self.assertIn("DRY RUN", out)
        self.assertIn("--go is required", out)
        self.assertIn("vm-reachability follow-up", out)

    def test_explicit_dry_run_and_json_dispatch_nothing(self) -> None:
        rec = _Recorder()
        rc, out = _main(["--dry-run", "--json"], rec)
        self.assertEqual(rc, 0)
        self.assertEqual(rec.calls, [])
        report = json.loads(out)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["task_class"], "research")
        self.assertEqual(report["builders"], DEFAULT_BUILDERS)
        for row in report["briefs"]:
            self.assertIsNone(row["plan_id"])
            self.assertTrue(row["dry_run"])
            self.assertNotIn("prompt", row)          # the report is a summary, not the brief body
        self.assertEqual(report["vm_reachability_followup"], ex.VM_REACHABILITY_FOLLOWUP)

    def test_dry_run_writes_no_manifest_unless_asked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            default_manifest = Path(tmp) / "default.json"
            target = Path(tmp) / "m.json"
            with patch.object(ex, "DEFAULT_MANIFEST", str(default_manifest)):
                rc, _ = _main([])
                self.assertEqual(rc, 0)
                self.assertFalse(default_manifest.exists(), "a dry run must not write the default manifest")
                rc, _ = _main(["--manifest", str(target)])
            self.assertEqual(rc, 0)
            written = json.loads(target.read_text(encoding="utf-8"))
            self.assertTrue(written["dry_run"])
            self.assertFalse(default_manifest.exists())

    def test_go_and_dry_run_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _main(["--go", "--dry-run"])
        self.assertEqual(ctx.exception.code, 2)


class GoPathTests(TestCase):
    """--go is exercised ONLY against the fake; the real submit_task is never bound here."""

    def test_go_submits_each_brief_in_the_revival_probe_shape(self) -> None:
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "exerciser.json"
            rc, out = _main(["--go", "--manifest", str(manifest)], rec)
            self.assertEqual(rc, 0)
            self.assertEqual(len(rec.calls), 2)
            for call, brief in zip(rec.calls, ex.BRIEFS):
                self.assertEqual(call["builders"], DEFAULT_BUILDERS)
                self.assertEqual(call["task_class"], "research")
                self.assertEqual(call["plan_id_hint"], f"exerciser-{brief.slug}")
                self.assertIsInstance(call["est_tokens"], int)
                self.assertGreater(call["est_tokens"], ex.RESEARCH_OUT_TOKENS)
                self.assertEqual(call["prompt"], ex.build_prompt(brief))
            written = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertFalse(written["dry_run"])
            self.assertTrue(all(r["ok"] and r["plan_id"].startswith("hearth-") for r in written["briefs"]))
            self.assertIn("DISPATCHED", out)

    def test_go_only_one_brief(self) -> None:
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = _main(["--go", "--only", "critic-loop-advisory-trust",
                           "--manifest", str(Path(tmp) / "m.json")], rec)
        self.assertEqual(rc, 0)
        self.assertEqual([c["plan_id_hint"] for c in rec.calls], ["exerciser-critic-loop-advisory-trust"])

    def test_go_submit_failure_is_reported_not_raised(self) -> None:
        rec = _Recorder(ok=False)
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = _main(["--go", "--json", "--manifest", str(Path(tmp) / "m.json")], rec)
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertTrue(all(r["ok"] is False and "denied" in r["error"] for r in report["briefs"]))


class AcceptanceTests(TestCase):
    def _done(self, winner: str, builds: dict | None = None) -> dict:
        result = {"plan_id": "hearth-exerciser-x-1", "winner": winner}
        if builds is not None:
            result["builds"] = builds
        return {"ok": True, "done": True, "result": result}

    def test_done_with_roster_winner_passes_and_lists_branches(self) -> None:
        status = self._done("cc-builder-3", {
            "cc-builder-2": {"branch": "lap/hearth-exerciser-x-1/cc-builder-2", "pushed": True},
            "cc-builder-3": {"branch": "lap/hearth-exerciser-x-1/cc-builder-3", "pushed": False},
        })
        acc = ex.check_acceptance(status)
        self.assertTrue(acc["ok"])
        self.assertTrue(acc["done"])
        self.assertEqual(acc["winner"], "cc-builder-3")
        self.assertEqual(sorted(acc["builders_built"]), ["cc-builder-2", "cc-builder-3"])
        self.assertEqual(len(acc["branches"]), 2)
        self.assertEqual(acc["pushed"], ["lap/hearth-exerciser-x-1/cc-builder-2"])
        self.assertTrue(any("fleet_harvest --sweep" in r for r in acc["reasons"]))

    def test_winner_outside_roster_fails(self) -> None:
        acc = ex.check_acceptance(self._done("am4-worker-1"))
        self.assertFalse(acc["ok"])
        self.assertTrue(acc["done"])
        self.assertFalse(acc["winner_ok"])
        self.assertTrue(any("am4-worker-1" in r for r in acc["reasons"]))

    def test_not_done_and_ssh_failure_and_garbage(self) -> None:
        pending = ex.check_acceptance({"ok": True, "done": False})
        self.assertFalse(pending["ok"])
        self.assertFalse(pending["done"])
        failed = ex.check_acceptance({"ok": False, "done": False, "error": "no route to host"})
        self.assertFalse(failed["ok"])
        self.assertIn("no route to host", failed["reasons"][0])
        self.assertFalse(ex.check_acceptance("nonsense")["ok"])  # type: ignore[arg-type]

    def test_out_file_ack_shape_uses_the_lifted_winner(self) -> None:
        ack = {"ok": True, "done": True, "plan_id": "p", "winner": "cc-builder-2", "result_ok": True}
        acc = ex.check_acceptance(ack)
        self.assertTrue(acc["ok"])
        self.assertEqual(acc["winner"], "cc-builder-2")

    def test_status_mode_reads_only_and_never_submits(self) -> None:
        rec = _Recorder(status=self._done("cc-builder-2"))
        rc, out = _main(["--status", "hearth-exerciser-x-1"], rec)
        self.assertEqual(rc, 0)
        self.assertEqual(rec.calls, [{"status_for": "hearth-exerciser-x-1"}])
        self.assertIn("winner=cc-builder-2", out)
        rec = _Recorder(status={"ok": True, "done": False})
        rc, _ = _main(["--status", "hearth-exerciser-x-1", "--json"], rec)
        self.assertEqual(rc, 1)


class FollowupRecordTests(TestCase):
    def test_vm_reachability_followup_is_recorded_not_attempted(self) -> None:
        vm = ex.VM_REACHABILITY_FOLLOWUP
        for key in ("status", "evidence", "why_not_bind_wide", "design", "out_of_window"):
            self.assertIn(key, vm)
        self.assertIn("not attempted", vm["status"])
        self.assertIn("omen.mshome.net:8081/v1/models", vm["evidence"]["probe"])
        self.assertIn("127.0.0.1", vm["evidence"]["observed"])
        self.assertTrue(any("firewall rule" in d for d in vm["design"]))
        self.assertIn("runner.json cutover", vm["out_of_window"])
        # And the module docstring carries the same design for the reader.
        doc = ex.__doc__
        self.assertIn("omen.mshome.net:8081/v1/models", doc)
        self.assertIn("vEthernet (Default Switch)", doc)
        self.assertIn("NOT built", doc)
        self.assertIn("--go", doc)
