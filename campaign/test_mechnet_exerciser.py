"""Tests for campaign/mechnet_exerciser.py -- M6, dry-run only.

Nothing here reaches the fleet or the door: submit_task / task_status are injected
fakes, the via-door path is proven against a FAKE HearthClient class substituted for
``_hearth_client_class`` (no socket is opened, 127.0.0.1:8710 is never contacted),
and the dry-run default is asserted to call neither. Run from the repo root:

    fleet-worker-node/.venv-omen/Scripts/python.exe -m pytest campaign/test_mechnet_exerciser.py -q -p no:cacheprovider
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from campaign import mechnet_exerciser as ex
from hearth.toolsurface.task_lane import DEFAULT_BUILDERS

REPO = Path(__file__).resolve().parent.parent
_PATH_RE = re.compile(r"\b((?:hearth|docs|campaign|fleet)/[A-Za-z0-9_./-]+\.(?:py|md|toml))\b")

# A value that exists ONLY to be hunted for. Every via-door test asserts this string
# never reaches stdout or the manifest; if the key ever leaks, these tests fail with
# the leak itself in the message.
MARKER_KEY = "MARKER-KEY-DO-NOT-LEAK-8f3a1c"


class _Recorder:
    """A fake submit_task / task_status that records calls and answers like the real one."""

    def __init__(self, ok: bool = True, status: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.ok = ok
        self.status = status

    def submit(self, prompt, builders=None, plan_id_hint=None, task_class=None,
               est_tokens=None, requires=None, max_age_s=None):
        self.calls.append({"prompt": prompt, "builders": builders, "plan_id_hint": plan_id_hint,
                           "task_class": task_class, "est_tokens": est_tokens,
                           "requires": requires, "max_age_s": max_age_s})
        if not self.ok:
            return {"ok": False, "error": "ssh exit 255: denied", "plan_id": f"hearth-{plan_id_hint}-deadbeef",
                    "builders": builders}
        return {"ok": True, "plan_id": f"hearth-{plan_id_hint}-deadbeef", "builders": list(builders),
                "inbox_path": f"/home/claude/work/commandcenter/inbox/hearth-{plan_id_hint}-deadbeef.md"}

    def task_status(self, plan_id):
        self.calls.append({"status_for": plan_id})
        return self.status


class _DoorLog:
    """What the fake HearthClient saw: constructions, calls, and class lookups.

    ``lookups`` counts calls to the patched ``_hearth_client_class``; ``constructions``
    counts actual client objects. Both are asserted to be zero on the failure paths,
    which is what makes "the env var is checked BEFORE any client construction" a
    real claim rather than a hopeful one.
    """

    def __init__(self, answer) -> None:
        self.lookups = 0
        self.constructions: list[dict] = []
        self.calls: list[dict] = []
        self._answer = answer

    def client_class(self):
        log = self

        class _FakeHearthClient:
            def __init__(self, **kwargs):
                log.constructions.append(dict(kwargs))
                self._kwargs = dict(kwargs)

            def call_sync(self, tool, **args):
                log.calls.append({"tool": tool, "args": args, "ctor": dict(self._kwargs)})
                answer = log._answer
                return answer(tool, args) if callable(answer) else answer

        return _FakeHearthClient

    def getter(self):
        """Stands in for ex._hearth_client_class."""
        def _get():
            self.lookups += 1
            return self.client_class()
        return _get


@contextmanager
def _door_env(key: str | None = MARKER_KEY):
    """Set (or remove) the door key env var; patch.dict restores it either way."""
    with patch.dict(os.environ, {}, clear=False):
        if key is None:
            os.environ.pop(ex.DOOR_KEY_ENV, None)
        else:
            os.environ[ex.DOOR_KEY_ENV] = key
        yield


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

    def test_plan_declares_the_deliverable_and_a_lifetime(self) -> None:
        """B-01's two expectation fields ride every brief: `requires` is the ONE
        deliverable the PREAMBLE promises, `max_age_s` the declared lifetime."""
        for item in ex.plan_submissions():
            self.assertEqual(item["requires"], [item["deliverable"]])
            self.assertEqual(item["max_age_s"], ex.RESEARCH_MAX_AGE_S)
            self.assertEqual(item["max_age_s"], 3600)
            # And it is the deliverable the builder is actually told to commit.
            self.assertIn(item["requires"][0], item["prompt"])

    def test_max_age_s_override_and_validation(self) -> None:
        self.assertEqual(ex.plan_submissions(max_age_s=7200)[0]["max_age_s"], 7200)
        self.assertIsNone(ex.plan_submissions(max_age_s=None)[0]["max_age_s"])
        # Validated by the task lane's own validator, before any plan exists.
        for bad in (0, -1, 10 ** 9, 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ex.plan_submissions(max_age_s=bad)  # type: ignore[arg-type]

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
            # The dry-run row shape is unchanged EXCEPT the two expectation keys.
            self.assertEqual(row["requires"], [row["deliverable"]])
            self.assertEqual(row["max_age_s"], ex.RESEARCH_MAX_AGE_S)
        self.assertEqual(report["vm_reachability_followup"], ex.VM_REACHABILITY_FOLLOWUP)
        self.assertFalse(report["via_door"])
        self.assertIsNone(report["door_key_env"])

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
                # B-01's expectation pair reaches submit_task, not just the plan.
                self.assertEqual(call["requires"], [f"proposals/{brief.slug}.md"])
                self.assertEqual(call["max_age_s"], ex.RESEARCH_MAX_AGE_S)
            written = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertFalse(written["dry_run"])
            self.assertTrue(all(r["ok"] and r["plan_id"].startswith("hearth-") for r in written["briefs"]))
            # The manifest records what was asked for, not only what came back.
            self.assertTrue(all(r["max_age_s"] == ex.RESEARCH_MAX_AGE_S for r in written["briefs"]))
            self.assertTrue(all(r["requires"] == [r["deliverable"]] for r in written["briefs"]))
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


class DoorKeyRuleTests(TestCase):
    """The env-var rule: named, never valued; loud, never a fallback."""

    def test_missing_and_empty_and_blank_all_raise_naming_only_the_variable(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value), _door_env(value):
                with self.assertRaises(ValueError) as ctx:
                    ex.require_door_key()
                message = str(ctx.exception)
                self.assertIn(ex.DOOR_KEY_ENV, message)
                self.assertIn(ex.DOOR_CALLER_ID, message)
                self.assertNotIn(MARKER_KEY, message)

    def test_present_key_is_returned_and_never_reformatted(self) -> None:
        with _door_env():
            self.assertEqual(ex.require_door_key(), MARKER_KEY)

    def test_the_variable_name_is_the_one_ops_stages(self) -> None:
        self.assertEqual(ex.DOOR_KEY_ENV, "HEARTH_MECHNET_ORCHESTRATOR_KEY")
        self.assertEqual(ex.DOOR_CALLER_ID, "mechnet-orchestrator")
        self.assertEqual(ex.DOOR_PROFILE, "orchestrator")

    def test_door_task_id_is_a_dated_correlation_id(self) -> None:
        import datetime as _dt
        self.assertEqual(ex.door_task_id(_dt.date(2026, 9, 6)), "mechnet-exerciser-2026-09-06")
        self.assertRegex(ex.door_task_id(), r"^mechnet-exerciser-\d{4}-\d{2}-\d{2}$")


class DoorResultMappingTests(TestCase):
    """{ok, text, structured} -> the dict submit_task returns."""

    def test_structured_payload_is_used_verbatim(self) -> None:
        mapped = ex.map_door_result({"ok": True, "text": "", "structured": {
            "ok": True, "plan_id": "hearth-x-1", "builders": ["cc-builder-2"],
            "inbox_path": "/inbox/hearth-x-1.md"}})
        self.assertTrue(mapped["ok"])
        self.assertEqual(mapped["plan_id"], "hearth-x-1")
        self.assertEqual(mapped["inbox_path"], "/inbox/hearth-x-1.md")

    def test_single_key_result_wrapper_is_unwrapped(self) -> None:
        mapped = ex.map_door_result({"ok": True, "text": "",
                                     "structured": {"result": {"ok": True, "plan_id": "hearth-x-2"}}})
        self.assertEqual(mapped["plan_id"], "hearth-x-2")

    def test_text_json_is_the_fallback_when_structured_is_absent(self) -> None:
        mapped = ex.map_door_result({"ok": True, "structured": None,
                                     "text": json.dumps({"ok": True, "plan_id": "hearth-x-3"})})
        self.assertEqual(mapped["plan_id"], "hearth-x-3")

    def test_tool_error_becomes_a_reported_row_not_an_exception(self) -> None:
        mapped = ex.map_door_result({"ok": False, "structured": None,
                                     "text": "capability 'dispatch' denied for profile probe"})
        self.assertFalse(mapped["ok"])
        self.assertIn("denied", mapped["error"])
        self.assertIsNone(mapped["plan_id"])

    def test_tool_ok_false_inside_a_structured_payload_survives(self) -> None:
        mapped = ex.map_door_result({"ok": True, "structured": {"ok": False, "error": "ssh exit 255"}})
        self.assertFalse(mapped["ok"])
        self.assertEqual(mapped["error"], "ssh exit 255")

    def test_unreadable_answers_are_reported(self) -> None:
        self.assertFalse(ex.map_door_result("nonsense")["ok"])
        self.assertIn("str", ex.map_door_result("nonsense")["error"])
        self.assertFalse(ex.map_door_result({"ok": True, "structured": None, "text": ""})["ok"])
        self.assertFalse(ex.map_door_result({"ok": True, "structured": None, "text": "[]"})["ok"])


class ViaDoorTests(TestCase):
    """--via-door proven against a FAKE client class. No socket is opened."""

    def _answer(self, tool, args):
        if tool == "submit_task":
            return {"ok": True, "text": "", "structured": {
                "ok": True, "plan_id": f"hearth-{args['plan_id_hint']}-door01",
                "builders": list(args["builders"] or []),
                "inbox_path": f"/inbox/hearth-{args['plan_id_hint']}-door01.md"}}
        return {"ok": True, "text": "", "structured": {
            "ok": True, "done": True, "result": {"winner": "cc-builder-2"}}}

    def _run(self, argv, log):
        buf = io.StringIO()
        with patch.object(ex, "_hearth_client_class", log.getter()), redirect_stdout(buf):
            rc = ex.main(argv, submit_fn=_never_called, status_fn=_never_called)
        return rc, buf.getvalue()

    def test_go_via_door_sends_the_seven_arguments_and_maps_the_result(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(), tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            rc, out = self._run(["--go", "--via-door", "--manifest", str(manifest)], log)
            written = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(len(log.calls), 2)
        for call, brief in zip(log.calls, ex.BRIEFS):
            self.assertEqual(call["tool"], "submit_task")
            self.assertEqual(sorted(call["args"]), sorted(ex.DOOR_SUBMIT_ARGS))
            self.assertEqual(call["args"]["prompt"], ex.build_prompt(brief))
            self.assertEqual(call["args"]["builders"], DEFAULT_BUILDERS)
            self.assertEqual(call["args"]["plan_id_hint"], f"exerciser-{brief.slug}")
            self.assertEqual(call["args"]["task_class"], "research")
            self.assertIsInstance(call["args"]["est_tokens"], int)
            self.assertEqual(call["args"]["requires"], [f"proposals/{brief.slug}.md"])
            self.assertEqual(call["args"]["max_age_s"], ex.RESEARCH_MAX_AGE_S)
        # The mapped answer is what the report shows.
        self.assertTrue(written["via_door"])
        self.assertEqual(written["door_caller_id"], "mechnet-orchestrator")
        self.assertEqual(written["door_profile"], "orchestrator")
        self.assertEqual(written["door_key_env"], ex.DOOR_KEY_ENV)
        self.assertTrue(all(r["ok"] and r["plan_id"].endswith("-door01") for r in written["briefs"]))
        self.assertIn("DISPATCHED", out)

    def test_the_client_is_constructed_with_the_key_and_a_dated_task_id(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(), tempfile.TemporaryDirectory() as tmp:
            self._run(["--go", "--via-door", "--only", "critic-loop-advisory-trust",
                       "--manifest", str(Path(tmp) / "m.json")], log)
        self.assertEqual(len(log.constructions), 1)
        ctor = log.constructions[0]
        self.assertEqual(ctor["key"], MARKER_KEY)
        self.assertRegex(ctor["task_id"], r"^mechnet-exerciser-\d{4}-\d{2}-\d{2}$")
        # No endpoint given -> the client's own DEFAULT_ENDPOINT applies; this
        # module does not duplicate the door address.
        self.assertNotIn("endpoint", ctor)

    def test_door_endpoint_override_is_passed_through(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(), tempfile.TemporaryDirectory() as tmp:
            self._run(["--go", "--via-door", "--door-endpoint", "http://127.0.0.1:9999/mcp",
                       "--only", "critic-loop-advisory-trust",
                       "--manifest", str(Path(tmp) / "m.json")], log)
        self.assertEqual(log.constructions[0]["endpoint"], "http://127.0.0.1:9999/mcp")

    def test_missing_key_raises_before_any_client_is_looked_up_or_constructed(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(None):
            with self.assertRaises(ValueError) as ctx:
                self._run(["--go", "--via-door"], log)
        self.assertIn(ex.DOOR_KEY_ENV, str(ctx.exception))
        self.assertEqual(log.lookups, 0, "the client class was looked up despite a missing key")
        self.assertEqual(log.constructions, [], "a client was constructed despite a missing key")
        self.assertEqual(log.calls, [], "a call was made despite a missing key")

    def test_the_key_value_never_reaches_stdout_or_the_manifest(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(), tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            _, out = self._run(["--go", "--via-door", "--json", "--manifest", str(manifest)], log)
            raw = manifest.read_text(encoding="utf-8")
        self.assertNotIn(MARKER_KEY, out)
        self.assertNotIn(MARKER_KEY, raw)
        # The NAME is allowed to appear -- that is how an operator knows what to stage.
        self.assertIn(ex.DOOR_KEY_ENV, raw)
        # And the key really was in play, so this is not a vacuous absence.
        self.assertEqual(log.constructions[0]["key"], MARKER_KEY)

    def test_a_door_failure_is_reported_not_raised(self) -> None:
        log = _DoorLog({"ok": False, "structured": None,
                        "text": "capability 'dispatch' denied for profile probe"})
        with _door_env(), tempfile.TemporaryDirectory() as tmp:
            rc, out = self._run(["--go", "--via-door", "--json",
                                 "--manifest", str(Path(tmp) / "m.json")], log)
        self.assertEqual(rc, 1)
        report = json.loads(out)
        self.assertTrue(all(r["ok"] is False and "denied" in r["error"] for r in report["briefs"]))

    def test_dry_run_via_door_needs_no_key_and_calls_nothing(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(None):
            rc, out = self._run(["--via-door"], log)
        self.assertEqual(rc, 0)
        self.assertEqual(log.lookups, 0)
        self.assertEqual(log.constructions, [])
        self.assertEqual(log.calls, [])
        self.assertIn(ex.DRY_RUN_DOOR_LINE, out)
        self.assertIn("caller id unknown until the door answers", out)
        self.assertIn("DRY RUN", out)

    def test_dry_run_via_door_records_the_intent_in_json(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(None):
            rc, out = self._run(["--via-door", "--json"], log)
        report = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(report["via_door"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["door_key_env"], ex.DOOR_KEY_ENV)
        self.assertTrue(all(r["dry_run"] and r["plan_id"] is None for r in report["briefs"]))

    def test_status_via_door_reads_through_the_door(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env():
            rc, out = self._run(["--status", "hearth-exerciser-x-1", "--via-door"], log)
        self.assertEqual(rc, 0)
        self.assertEqual([c["tool"] for c in log.calls], ["task_status"])
        self.assertEqual(log.calls[0]["args"], {"plan_id": "hearth-exerciser-x-1"})
        self.assertEqual(log.calls[0]["ctor"]["key"], MARKER_KEY)
        self.assertIn("winner=cc-builder-2", out)
        self.assertNotIn(MARKER_KEY, out)

    def test_status_via_door_without_a_key_raises_before_reading(self) -> None:
        log = _DoorLog(self._answer)
        with _door_env(None), self.assertRaises(ValueError):
            self._run(["--status", "hearth-exerciser-x-1", "--via-door"], log)
        self.assertEqual(log.constructions, [])


class MaxAgeCliTests(TestCase):
    def test_override_reaches_the_submit_call(self) -> None:
        rec = _Recorder()
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = _main(["--go", "--max-age-s", "7200", "--manifest", str(Path(tmp) / "m.json")], rec)
        self.assertEqual(rc, 0)
        self.assertTrue(all(c["max_age_s"] == 7200 for c in rec.calls))

    def test_out_of_range_is_refused_before_anything_is_submitted(self) -> None:
        rec = _Recorder()
        for bad in ("0", "-5", "999999999"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _main(["--go", "--max-age-s", bad], rec)
        self.assertEqual(rec.calls, [], "a brief was submitted despite an invalid --max-age-s")

    def test_non_integer_is_refused_by_argparse(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _main(["--go", "--max-age-s", "six-hours"])
        self.assertEqual(ctx.exception.code, 2)


def _never_called(*args, **kwargs):
    raise AssertionError("the in-process lane was used while --via-door was requested")
