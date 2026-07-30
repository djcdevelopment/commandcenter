"""Hermetic tests for the ollama bypass sentinel (no subprocess, no live netstat).

The FIXTURE is written from OMEN's perspective: inbound rows are the server
side (local_port == 11434); a same-host caller contributes BOTH its client row
(remote_port == 11434, pid = the caller) and the matching inbound row. A
foreign caller (fleet VM) appears only as an inbound row — its identity is the
source IP. TIME_WAIT rows carry pid 0 on both sides.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fleet import ollama_sentinel as sentinel
from fleet.ollama_sentinel import (
    attribute, label_for, parse_netstat, run_once,
)
from hearth.kernel.timers import TIMERS

FIXTURE = """
  TCP    0.0.0.0:11434          0.0.0.0:0              LISTENING       14780
  TCP    192.168.12.7:11434     192.168.12.233:52344   ESTABLISHED     14780
  TCP    127.0.0.1:52001        127.0.0.1:11434        ESTABLISHED     4242
  TCP    127.0.0.1:11434        127.0.0.1:52001        ESTABLISHED     14780
  TCP    127.0.0.1:52002        127.0.0.1:11434        ESTABLISHED     9999
  TCP    127.0.0.1:11434        127.0.0.1:52002        ESTABLISHED     14780
  TCP    172.17.32.1:11434      172.17.32.1:51043      TIME_WAIT       0
  TCP    [::]:11434             [::]:0                 LISTENING       14780
  MALFORMED LINE JUNK
"""

# The door pair from FIXTURE after the connection closed: server side lingers
# in TIME_WAIT with pid 0 and there is no client row left to attribute it.
GHOST_FIXTURE = """
  TCP    0.0.0.0:11434          0.0.0.0:0              LISTENING       14780
  TCP    127.0.0.1:11434        127.0.0.1:52002        TIME_WAIT       0
"""

# The asymmetric case caught live 2026-07-16: a closed bypass whose
# server-side row is already gone — only the CLIENT-side TIME_WAIT ghost
# remains (pid 0), plus one still-ESTABLISHED client-only row with a live pid.
ASYMMETRIC_FIXTURE = """
  TCP    0.0.0.0:11434          0.0.0.0:0              LISTENING       14780
  TCP    127.0.0.1:49800        127.0.0.1:11434        TIME_WAIT       0
  TCP    127.0.0.1:49900        127.0.0.1:11434        ESTABLISHED     7777
"""


class TestOllamaSentinel(unittest.TestCase):
    def test_parse_netstat(self):
        parsed = parse_netstat(FIXTURE, 11434)
        self.assertIn(14780, parsed["listener_pids"])
        # inbound: foreign 52344, local 52001, local 52002 (door), ghost 51043
        self.assertEqual(len(parsed["inbound"]), 4)
        # client rows: only the two same-host callers (4242 and 9999)
        self.assertEqual(len(parsed["client_rows"]), 2)

    def test_attribute(self):
        parsed = parse_netstat(FIXTURE, 11434)
        process_lookup = lambda pid: "test_proc.exe" if pid == 4242 else None
        direct, excluded = attribute(
            parsed["inbound"], parsed["client_rows"], {9999}, process_lookup
        )

        self.assertEqual(excluded, ["127.0.0.1:52002"])
        self.assertEqual(len(direct), 3)

        labels = {d["label"] for d in direct}
        self.assertIn("lan-am4", labels)
        self.assertIn("loopback", labels)
        self.assertIn("hyperv-nat", labels)

        local_rec = next(d for d in direct if d["source_port"] == 52001)
        self.assertEqual(local_rec["pid"], 4242)
        self.assertEqual(local_rec["process"], "test_proc.exe")

        foreign_rec = next(d for d in direct if d["source_port"] == 52344)
        self.assertIsNone(foreign_rec["pid"])
        self.assertEqual(foreign_rec["label"], "lan-am4")

        ghost_rec = next(d for d in direct if d["source_port"] == 51043)
        self.assertIsNone(ghost_rec["pid"])
        self.assertEqual(ghost_rec["state"], "TIME_WAIT")

    def test_client_side_sweep_catches_asymmetric_ghosts(self):
        parsed = parse_netstat(ASYMMETRIC_FIXTURE, 11434)
        self.assertEqual(len(parsed["inbound"]), 0)
        self.assertEqual(len(parsed["client_rows"]), 2)

        process_lookup = lambda pid: "curl.exe" if pid == 7777 else None
        direct, excluded = attribute(
            parsed["inbound"], parsed["client_rows"], {9999}, process_lookup
        )
        self.assertEqual(excluded, [])
        self.assertEqual(len(direct), 2)

        ghost = next(d for d in direct if d["source_port"] == 49800)
        self.assertIsNone(ghost["pid"])
        self.assertEqual(ghost["state"], "TIME_WAIT")

        live = next(d for d in direct if d["source_port"] == 49900)
        self.assertEqual(live["pid"], 7777)
        self.assertEqual(live["process"], "curl.exe")

    def test_client_side_sweep_still_excludes_door_pid(self):
        parsed = parse_netstat(ASYMMETRIC_FIXTURE, 11434)
        direct, excluded = attribute(
            parsed["inbound"], parsed["client_rows"], {7777}, lambda p: None
        )
        self.assertEqual(excluded, ["127.0.0.1:49900"])
        self.assertEqual(len(direct), 1)  # only the pid-0 ghost remains

    def test_paired_rows_yield_one_record_not_two(self):
        # FIXTURE's 4242 caller appears as BOTH an inbound row and a client
        # row — the sweep must not double-report the tuple.
        parsed = parse_netstat(FIXTURE, 11434)
        direct, _ = attribute(
            parsed["inbound"], parsed["client_rows"], {9999}, lambda p: None
        )
        keys = [f"{d['source_ip']}:{d['source_port']}" for d in direct]
        self.assertEqual(len(keys), len(set(keys)))

    def test_run_once_dedup_and_door_ghosts(self):
        with tempfile.TemporaryDirectory() as td:
            var_dir = Path(td)
            # Tick 1: three direct records; the door pair (9999) is excluded
            # but its tuple is remembered in seen-state.
            res1 = run_once(FIXTURE, {9999}, var_dir, 1000.0, lambda p: None)
            self.assertEqual(res1["new_direct"], 3)

            # Tick 2: identical sample -> everything dedups.
            res2 = run_once(FIXTURE, {9999}, var_dir, 1010.0, lambda p: None)
            self.assertEqual(res2["new_direct"], 0)

            # Tick 3: the door connection has closed; its TIME_WAIT ghost
            # (pid 0, no client row) must NOT re-report as direct.
            res3 = run_once(GHOST_FIXTURE, {9999}, var_dir, 1020.0, lambda p: None)
            self.assertEqual(res3["new_direct"], 0)

            lines = (var_dir / "ollama-direct.ndjson").read_text().splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn("ts", json.loads(lines[0]))

    def test_ttl_expiry_re_reports(self):
        with tempfile.TemporaryDirectory() as td:
            var_dir = Path(td)
            res1 = run_once(FIXTURE, {9999}, var_dir, 1000.0, lambda p: None)
            self.assertEqual(res1["new_direct"], 3)

            # 601s later: past SEEN_TTL_S (600), the tuples re-report.
            res2 = run_once(FIXTURE, {9999}, var_dir, 1601.0, lambda p: None)
            self.assertEqual(res2["new_direct"], 3)

    def test_label_for(self):
        self.assertEqual(label_for("127.0.0.1"), "loopback")
        self.assertEqual(label_for("::1"), "loopback")
        self.assertEqual(label_for("192.168.12.50"), "lan-am4")
        self.assertEqual(label_for("192.168.1.1"), "lan")
        self.assertEqual(label_for("172.17.32.1"), "hyperv-nat")
        self.assertEqual(label_for("100.99.98.97"), "tailnet")
        self.assertEqual(label_for("8.8.8.8"), "other")

    def test_timers_registry_arms_the_sentinel(self):
        spec = next((t for t in TIMERS if t.name == "ollama-sentinel"), None)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.interval_s, 120.0)
        argv = spec.argv_builder()
        self.assertIn("fleet.ollama_sentinel", argv)
        self.assertIn("--exclude-pid", argv)


if __name__ == "__main__":
    unittest.main()


class ServiceabilityTests(unittest.TestCase):
    """Pins the 2026-07-30 failure mode: ollama.exe up and answering /api/tags with a
    version, while lib/ollama had been emptied to a single shortcut so every generate
    returned HTTP 500. Reachability read green for half an hour."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.install = self.root / "Ollama"
        self.install.mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _place_runtime(self, subdir: str, name: str = "llama-server.exe") -> Path:
        target = self.install / subdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
        return target

    # --- runtime discovery --------------------------------------------------

    def test_finds_runtime_in_the_standard_layout(self):
        expected = self._place_runtime("lib/ollama")
        self.assertEqual(sentinel.find_llama_runtime(self.install), expected)

    def test_finds_runtime_in_alternate_layouts(self):
        for subdir in ("*", "build/lib/ollama", "dist/windows-amd64/lib/ollama"):
            with self.subTest(subdir=subdir):
                root = Path(tempfile.mkdtemp()).resolve()
                self.addCleanup(shutil.rmtree, root, True)
                install = root / "Ollama"
                place = install if subdir == "*" else install / subdir
                place.mkdir(parents=True)
                (place / "llama-server.exe").write_bytes(b"MZ")
                self.assertIsNotNone(sentinel.find_llama_runtime(install))

    def test_a_shortcut_is_not_a_runtime(self):
        """The exact 2026-07-30 shape: lib/ exists and holds only Ollama.lnk."""
        (self.install / "lib").mkdir()
        (self.install / "lib" / "Ollama.lnk").write_bytes(b"shortcut")

        self.assertIsNone(sentinel.find_llama_runtime(self.install))
        verdict = sentinel.check_runtime(self.install)
        self.assertFalse(verdict["ok"])
        self.assertIn("llama-server binary not found", verdict["reason"])
        self.assertIn("reinstall", verdict["reason"])

    def test_missing_install_dir_is_not_ok(self):
        verdict = sentinel.check_runtime(self.root / "absent")
        self.assertFalse(verdict["ok"])
        self.assertIn("install dir missing", verdict["reason"])

    def test_unlocatable_install_dir_is_not_ok(self):
        verdict = sentinel.check_runtime(None)
        self.assertFalse(verdict["ok"])
        self.assertIn("OLLAMA_INSTALL_DIR", verdict["reason"])

    def test_install_dir_honours_the_env_override(self):
        self.assertEqual(
            sentinel.ollama_install_dir({"OLLAMA_INSTALL_DIR": str(self.install)}),
            self.install)

    def test_install_dir_falls_back_to_localappdata(self):
        got = sentinel.ollama_install_dir({"LOCALAPPDATA": str(self.root)})
        self.assertEqual(got, self.root / "Programs" / "Ollama")

    # --- combined verdict ---------------------------------------------------

    def test_skipped_generate_probe_is_never_counted_as_a_pass(self):
        self._place_runtime("lib/ollama")

        report = sentinel.check_serviceability(self.install, generate_probe=None)

        self.assertIsNone(report["generate"]["ok"])
        self.assertIn("throttled", report["generate"]["reason"])
        self.assertTrue(report["serviceable"])  # runtime present, generate not claimed

    def test_missing_runtime_makes_it_unserviceable(self):
        report = sentinel.check_serviceability(self.install, generate_probe=None)
        self.assertFalse(report["serviceable"])

    def test_failing_generate_makes_it_unserviceable_even_with_a_runtime_present(self):
        """Catches present-but-broken: bad GPU driver, corrupt binary."""
        self._place_runtime("lib/ollama")

        report = sentinel.check_serviceability(
            self.install, generate_probe=lambda: (False, "HTTP 500: kaboom"))

        self.assertFalse(report["serviceable"])
        self.assertIn("kaboom", report["generate"]["reason"])

    def test_everything_healthy_is_serviceable(self):
        self._place_runtime("lib/ollama")
        report = sentinel.check_serviceability(self.install,
                                              generate_probe=lambda: (True, "ok"))
        self.assertTrue(report["serviceable"])

    def test_a_raising_probe_is_a_failure_not_a_crash(self):
        def boom():
            raise RuntimeError("probe exploded")

        verdict = sentinel.check_generate(boom)

        self.assertFalse(verdict["ok"])
        self.assertIn("probe exploded", verdict["reason"])

    # --- throttle -----------------------------------------------------------

    def test_absent_throttle_state_means_due(self):
        self.assertTrue(sentinel.generate_probe_is_due(self.root / "none.json", 1000.0, 100))

    def test_unreadable_throttle_state_means_due(self):
        """A lost throttle file must not silently suppress the check."""
        path = self.root / "bad.json"
        path.write_text("{ truncated", encoding="utf-8")
        self.assertTrue(sentinel.generate_probe_is_due(path, 1000.0, 100))

    def test_recent_probe_is_not_due_and_stale_probe_is(self):
        path = self.root / "state.json"
        sentinel.record_generate_probe(path, 1000.0)

        self.assertFalse(sentinel.generate_probe_is_due(path, 1050.0, 100))
        self.assertTrue(sentinel.generate_probe_is_due(path, 1101.0, 100))
