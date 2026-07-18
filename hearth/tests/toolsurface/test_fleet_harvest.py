from __future__ import annotations

import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.fleet_harvest import (
    DEST_PREFIX,
    get_tools,
    harvest_fleet_run,
    list_fleet_runs,
)

PID = "hearth-demo-1234"


def _cp(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=stdout, stderr=stderr)


def _router(spec: dict, capture: dict | None = None):
    """Route subprocess.run by git subcommand (args = ['git','-C',repo,<sub>,...])."""
    def run(args, **kw):
        sub = args[3] if len(args) > 3 else args[-1]
        if capture is not None:
            capture.setdefault("subs", []).append(sub)
            capture.setdefault("calls", []).append(args)
        outcome = spec.get(sub, _cp())
        return outcome(args) if callable(outcome) else outcome
    return run


class HarvestFleetRunTests(TestCase):
    def test_happy_path_fetches_pushes_and_cleans_up(self) -> None:
        cap: dict = {}
        forref = (f"5b11654 refs/fleet-harvest/{PID}/cc-builder-1/lap1\n"
                  f"5823011 refs/fleet-harvest/{PID}/cc-builder-2/lap1\n")
        spec = {
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=forref),
            "push": _cp(),
            "update-ref": _cp(),
        }
        with patch("subprocess.run", side_effect=_router(spec, cap)):
            result = harvest_fleet_run(PID)

        self.assertTrue(result["ok"])
        self.assertTrue(result["pushed"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["github_prefix"], f"{DEST_PREFIX}/{PID}")
        branches = {w["github_branch"] for w in result["workers"]}
        self.assertEqual(branches, {
            f"{DEST_PREFIX}/{PID}/cc-builder-1/lap1",
            f"{DEST_PREFIX}/{PID}/cc-builder-2/lap1",
        })
        # Push used the fleet/ namespace refspec, non-force (no leading '+').
        push_call = next(a for a in cap["calls"] if a[3] == "push")
        refspec = push_call[-1]
        self.assertEqual(refspec, f"refs/fleet-harvest/{PID}/*:refs/heads/{DEST_PREFIX}/{PID}/*")
        self.assertNotIn("+", refspec)
        # Staging refs cleaned up (one update-ref -d per fetched branch).
        self.assertEqual(cap["subs"].count("update-ref"), 2)

    def test_no_branches_is_ok_with_empty_workers_and_no_push(self) -> None:
        cap: dict = {}
        spec = {"fetch": _cp(), "for-each-ref": _cp(stdout="")}
        with patch("subprocess.run", side_effect=_router(spec, cap)):
            result = harvest_fleet_run(PID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["workers"], [])
        self.assertFalse(result["pushed"])
        self.assertIn("note", result)
        self.assertNotIn("push", cap.get("subs", []))

    def test_fetch_failure_is_clean_error_and_no_push(self) -> None:
        cap: dict = {}
        spec = {"fetch": _cp(stderr="Connection refused", rc=128)}
        with patch("subprocess.run", side_effect=_router(spec, cap)):
            result = harvest_fleet_run(PID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "fetch")
        self.assertIn("Connection refused", result["error"])
        self.assertNotIn("push", cap.get("subs", []))

    def test_push_failure_reports_but_still_cleans_up(self) -> None:
        cap: dict = {}
        spec = {
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=f"5b11654 refs/fleet-harvest/{PID}/cc-builder-1/lap1\n"),
            "push": _cp(stderr="! [rejected]", rc=1),
            "update-ref": _cp(),
        }
        with patch("subprocess.run", side_effect=_router(spec, cap)):
            result = harvest_fleet_run(PID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "push")
        # Cleanup must run even when the push fails, so a retry starts clean.
        self.assertEqual(cap["subs"].count("update-ref"), 1)

    def test_plan_id_with_slash_rejected(self) -> None:
        with self.assertRaises(ValueError):
            harvest_fleet_run("has/slash")

    def test_plan_id_with_glob_rejected(self) -> None:
        with self.assertRaises(ValueError):
            harvest_fleet_run("star*")

    def test_empty_plan_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            harvest_fleet_run("")


class ListFleetRunsTests(TestCase):
    def test_groups_distinct_plan_ids_with_counts(self) -> None:
        lsremote = (
            "sha1 refs/heads/ccfarm/run-a/cc-builder-1/lap1\n"
            "sha2 refs/heads/ccfarm/run-a/cc-builder-2/lap1\n"
            "sha3 refs/heads/ccfarm/run-b/am4-worker-1/lap1\n"
        )
        with patch("subprocess.run", side_effect=_router({"ls-remote": _cp(stdout=lsremote)})):
            result = list_fleet_runs()
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_runs_on_farmer"], 2)
        by_id = {r["plan_id"]: r["branch_count"] for r in result["runs"]}
        self.assertEqual(by_id, {"run-a": 2, "run-b": 1})
        # Lexical desc: run-b before run-a.
        self.assertEqual([r["plan_id"] for r in result["runs"]], ["run-b", "run-a"])

    def test_limit_truncates(self) -> None:
        lines = "".join(f"s{i} refs/heads/ccfarm/run-{i}/w/lap1\n" for i in range(5))
        with patch("subprocess.run", side_effect=_router({"ls-remote": _cp(stdout=lines)})):
            result = list_fleet_runs(limit=2)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(result["total_runs_on_farmer"], 5)

    def test_ls_remote_failure_is_clean(self) -> None:
        with patch("subprocess.run", side_effect=_router({"ls-remote": _cp(stderr="no route", rc=128)})):
            result = list_fleet_runs()
        self.assertFalse(result["ok"])
        self.assertIn("no route", result["error"])

    def test_bad_limit_rejected(self) -> None:
        with self.assertRaises(ValueError):
            list_fleet_runs(0)


class GetToolsTests(TestCase):
    def test_exposes_both_harvest_tools(self) -> None:
        names = {fn.__name__ for fn in get_tools()}
        self.assertEqual(names, {"harvest_fleet_run", "list_fleet_runs"})
