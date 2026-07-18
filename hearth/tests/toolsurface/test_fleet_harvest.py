from __future__ import annotations

import subprocess
from unittest import TestCase
from unittest.mock import patch

from hearth.toolsurface.fleet_harvest import (
    DEST_PREFIX,
    _parse_push_porcelain,
    _sweep,
    get_tools,
    harvest_fleet_run,
    list_fleet_runs,
)

PID = "hearth-demo-1234"


def _cp(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=stdout, stderr=stderr)


def _porcelain(*ref_lines: str) -> str:
    """Wrap per-ref lines in the To/Done frame git push --porcelain emits."""
    return ("To ssh://github.com/example/commandcenter.git\n"
            + "".join(line + "\n" for line in ref_lines)
            + "Done\n")


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


class ParsePushPorcelainTests(TestCase):
    def test_counts_new_up_to_date_and_rejected(self) -> None:
        txt = _porcelain(
            f"*\trefs/fleet-harvest/{PID}/w1/lap1:refs/heads/fleet/{PID}/w1/lap1\t[new branch]",
            f"=\trefs/fleet-harvest/{PID}/w2/lap1:refs/heads/fleet/{PID}/w2/lap1\t[up to date]",
            f"!\trefs/fleet-harvest/{PID}/w3/lap1:refs/heads/fleet/{PID}/w3/lap1\t[rejected] (non-fast-forward)",
        )
        stats = _parse_push_porcelain(txt)
        self.assertEqual(stats["pushed"], 1)
        self.assertEqual(stats["up_to_date"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["raw"], txt)

    def test_new_refs_count_as_pushed(self) -> None:
        # First harvest of a run: every ref is '*' (new branch on origin).
        txt = _porcelain(
            "*\trefs/fleet-harvest/r/w1/lap1:refs/heads/fleet/r/w1/lap1\t[new branch]",
            "*\trefs/fleet-harvest/r/w2/lap1:refs/heads/fleet/r/w2/lap1\t[new branch]",
        )
        stats = _parse_push_porcelain(txt)
        self.assertEqual(stats["pushed"], 2)
        self.assertEqual(stats["up_to_date"], 0)
        self.assertEqual(stats["rejected"], 0)

    def test_fast_forward_space_flag_counts_as_pushed(self) -> None:
        txt = _porcelain(" \trefs/x:refs/heads/fleet/x\t5b11654..5823011")
        self.assertEqual(_parse_push_porcelain(txt)["pushed"], 1)

    def test_all_up_to_date_re_mirror(self) -> None:
        # Re-harvest of an already-mirrored run: every ref is '=' (no-op).
        txt = _porcelain(
            "=\trefs/fleet-harvest/r/w1/lap1:refs/heads/fleet/r/w1/lap1\t[up to date]",
            "=\trefs/fleet-harvest/r/w2/lap1:refs/heads/fleet/r/w2/lap1\t[up to date]",
        )
        stats = _parse_push_porcelain(txt)
        self.assertEqual(stats["pushed"], 0)
        self.assertEqual(stats["up_to_date"], 2)

    def test_malformed_and_frame_lines_leave_counters_alone(self) -> None:
        txt = ("To ssh://github.com/example/commandcenter.git\n"
               "no tabs on this line at all\n"
               "?\tunknown-flag\tline\n"
               "\n"
               "Done\n")
        stats = _parse_push_porcelain(txt)
        self.assertEqual((stats["pushed"], stats["up_to_date"], stats["rejected"]),
                         (0, 0, 0))
        self.assertEqual(stats["raw"], txt)

    def test_empty_text(self) -> None:
        stats = _parse_push_porcelain("")
        self.assertEqual((stats["pushed"], stats["up_to_date"], stats["rejected"]),
                         (0, 0, 0))
        self.assertEqual(stats["raw"], "")


class HarvestFleetRunTests(TestCase):
    def test_happy_path_fetches_pushes_and_cleans_up(self) -> None:
        cap: dict = {}
        forref = (f"5b11654 refs/fleet-harvest/{PID}/cc-builder-1/lap1\n"
                  f"5823011 refs/fleet-harvest/{PID}/cc-builder-2/lap1\n")
        porcelain = _porcelain(
            f"*\trefs/fleet-harvest/{PID}/cc-builder-1/lap1:refs/heads/fleet/{PID}/cc-builder-1/lap1\t[new branch]",
            f"*\trefs/fleet-harvest/{PID}/cc-builder-2/lap1:refs/heads/fleet/{PID}/cc-builder-2/lap1\t[new branch]",
        )
        spec = {
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=forref),
            "push": _cp(stdout=porcelain),
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
        # Push used the fleet/ namespace refspec, non-force (no leading '+'),
        # with --porcelain so the new-vs-noop delta comes back parseable.
        push_call = next(a for a in cap["calls"] if a[3] == "push")
        self.assertIn("--porcelain", push_call)
        refspec = push_call[-1]
        self.assertEqual(refspec, f"refs/fleet-harvest/{PID}/*:refs/heads/{DEST_PREFIX}/{PID}/*")
        self.assertNotIn("+", refspec)
        # The parsed porcelain delta rides the result: both refs were new.
        self.assertEqual(result["push"]["pushed"], 2)
        self.assertEqual(result["push"]["up_to_date"], 0)
        self.assertEqual(result["push"]["rejected"], 0)
        self.assertEqual(result["push"]["raw"], porcelain)
        # Staging refs cleaned up (one update-ref -d per fetched branch).
        self.assertEqual(cap["subs"].count("update-ref"), 2)

    def test_re_mirror_reports_all_up_to_date(self) -> None:
        spec = {
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=f"5b11654 refs/fleet-harvest/{PID}/cc-builder-1/lap1\n"),
            "push": _cp(stdout=_porcelain(
                f"=\trefs/fleet-harvest/{PID}/cc-builder-1/lap1:refs/heads/fleet/{PID}/cc-builder-1/lap1\t[up to date]")),
            "update-ref": _cp(),
        }
        with patch("subprocess.run", side_effect=_router(spec)):
            result = harvest_fleet_run(PID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["push"]["pushed"], 0)
        self.assertEqual(result["push"]["up_to_date"], 1)

    def test_partial_reject_in_porcelain_keeps_ok_true(self) -> None:
        # git push --porcelain can exit 0 while individual refs are rejected —
        # the transport worked, so ok stays True and the reject is visible in
        # the parsed delta instead of an error.
        spec = {
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=(
                f"5b11654 refs/fleet-harvest/{PID}/w1/lap1\n"
                f"5823011 refs/fleet-harvest/{PID}/w2/lap1\n")),
            "push": _cp(stdout=_porcelain(
                f"*\trefs/fleet-harvest/{PID}/w1/lap1:refs/heads/fleet/{PID}/w1/lap1\t[new branch]",
                f"!\trefs/fleet-harvest/{PID}/w2/lap1:refs/heads/fleet/{PID}/w2/lap1\t[rejected] (non-fast-forward)")),
            "update-ref": _cp(),
        }
        with patch("subprocess.run", side_effect=_router(spec)):
            result = harvest_fleet_run(PID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["push"]["pushed"], 1)
        self.assertEqual(result["push"]["rejected"], 1)

    def test_no_branches_is_ok_with_empty_workers_and_no_push(self) -> None:
        cap: dict = {}
        spec = {"fetch": _cp(), "for-each-ref": _cp(stdout="")}
        with patch("subprocess.run", side_effect=_router(spec, cap)):
            result = harvest_fleet_run(PID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["workers"], [])
        self.assertFalse(result["pushed"])
        self.assertIn("note", result)
        self.assertNotIn("push", result)  # no push ran → no porcelain delta
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
        self.assertNotIn("push", result)  # transport failed → no porcelain delta
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


class SweepTests(TestCase):
    def test_sweep_aggregates_branches_new_across_runs(self) -> None:
        # run-a: 1 branch, already mirrored ('='). run-b: 2 branches, one new
        # ('*') one up-to-date ('='). branches_mirrored counts all 3 ensured-
        # present; branches_new counts only the 1 that actually landed.
        lsremote = (
            "sha1 refs/heads/ccfarm/run-a/cc-builder-1/lap1\n"
            "sha2 refs/heads/ccfarm/run-b/cc-builder-1/lap1\n"
            "sha3 refs/heads/ccfarm/run-b/cc-builder-2/lap1\n"
        )

        def forref(args):
            stage_root = args[-1]
            if stage_root.endswith("/run-a"):
                return _cp(stdout="aaa1111 refs/fleet-harvest/run-a/cc-builder-1/lap1\n")
            return _cp(stdout=("bbb1111 refs/fleet-harvest/run-b/cc-builder-1/lap1\n"
                               "bbb2222 refs/fleet-harvest/run-b/cc-builder-2/lap1\n"))

        def push(args):
            refspec = args[-1]
            if "/run-a/" in refspec:
                return _cp(stdout=_porcelain(
                    "=\trefs/fleet-harvest/run-a/cc-builder-1/lap1:refs/heads/fleet/run-a/cc-builder-1/lap1\t[up to date]"))
            return _cp(stdout=_porcelain(
                "*\trefs/fleet-harvest/run-b/cc-builder-1/lap1:refs/heads/fleet/run-b/cc-builder-1/lap1\t[new branch]",
                "=\trefs/fleet-harvest/run-b/cc-builder-2/lap1:refs/heads/fleet/run-b/cc-builder-2/lap1\t[up to date]"))

        spec = {
            "ls-remote": _cp(stdout=lsremote),
            "fetch": _cp(),
            "for-each-ref": forref,
            "push": push,
            "update-ref": _cp(),
        }
        with patch("subprocess.run", side_effect=_router(spec)):
            report = _sweep()

        self.assertTrue(report["ok"])
        self.assertEqual(report["runs_seen"], 2)
        self.assertEqual(report["runs_mirrored"], 2)
        self.assertEqual(report["branches_mirrored"], 3)
        self.assertEqual(report["branches_new"], 1)

    def test_sweep_empty_runs_report_zero_new(self) -> None:
        spec = {
            "ls-remote": _cp(stdout="sha1 refs/heads/ccfarm/run-a/w/lap1\n"),
            "fetch": _cp(),
            "for-each-ref": _cp(stdout=""),  # fetched nothing — empty run
        }
        with patch("subprocess.run", side_effect=_router(spec)):
            report = _sweep()
        self.assertTrue(report["ok"])
        self.assertEqual(report["runs_empty"], 1)
        self.assertEqual(report["branches_new"], 0)


class GetToolsTests(TestCase):
    def test_exposes_both_harvest_tools(self) -> None:
        names = {fn.__name__ for fn in get_tools()}
        self.assertEqual(names, {"harvest_fleet_run", "list_fleet_runs"})
