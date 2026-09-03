"""Unit tests for fleet_dashboard — pure rendering + injected probers, no real network.

Run from repo root:  python -m unittest fleet.test_fleet_dashboard
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fleet.fleet_dashboard import (
    DEFAULT_OUT, build_sweep, collect_rung_state, main, render_fleet_html,
)

INVENTORY = Path(__file__).with_name("inventory.toml")


def _fake_prober(down_hosts):
    def prober(host, port, timeout):
        if host in down_hosts:
            return False, None, "TimeoutError"
        return True, 4.2, None
    return prober


def _sweep_doc():
    """A fleet_ping --json shaped document with every status present."""
    return {
        "summary": {"total": 4, "up": 2, "down": 1, "offline": 1},
        "nodes": [
            {"name": "omen", "kind": "physical-host", "expect": "up", "reachable": True, "status": "up",
             "purpose": "hypervisor + HEARTH door", "note": None,
             "probes": [{"service": "ssh", "host": "omen", "port": 22, "reachable": True, "latency_ms": 3.0, "error": None},
                        {"service": "hearth", "host": "127.0.0.1", "port": 8710, "reachable": True, "latency_ms": 1.0, "error": None},
                        {"service": "arc", "host": "127.0.0.1", "port": 8082, "reachable": False, "latency_ms": None, "error": "ConnectionRefusedError"}]},
            {"name": "cc-conductor", "kind": "vm", "expect": "up", "reachable": False, "status": "down",
             "purpose": "build pool <b>conductor</b>", "note": "parked for the RAM reclaim",
             "probes": [{"service": "ssh", "host": "cc-conductor.mshome.net", "port": 22, "reachable": False, "latency_ms": None, "error": "TimeoutError"}]},
            {"name": "i5", "kind": "physical-host", "expect": "optional", "reachable": False, "status": "offline",
             "purpose": "laptop", "note": None,
             "probes": [{"service": "ssh", "host": "i5", "port": 22, "reachable": False, "latency_ms": None, "error": "OSError"}]},
            {"name": "am4", "kind": "physical-host", "expect": "optional", "reachable": True, "status": "up-opt",
             "purpose": "services host", "note": None,
             "probes": [{"service": "ssh", "host": "192.168.12.5", "port": 22, "reachable": True, "latency_ms": 12.0, "error": None}]},
        ],
    }


def _rung_state(verdict="degraded"):
    return {
        "rung": "omen-arc", "port": 8082, "verdict": verdict,
        "baseline_tok_s": 106.0, "baseline_epoch": "2026-08-29T18:22:00Z",
        "envelope": {"fail_below": 0.8, "warn_below": 0.9},
        "observed_tok_s": 65.3, "observed_at": "2026-09-03T10:00:00Z", "observed_age_s": 41.0,
        "frac_of_baseline": 0.6161, "prefill_stall_recent": False,
        "last_ping_ok": True, "deep_samples": 7, "excluded_windows": ["rot-cutover-20260903"],
        "note": "envelope is of THIS baseline epoch, not of capacity (ADR-0044)",
    }


class TestRenderFleetHtml(unittest.TestCase):
    def test_summary_nodes_and_timestamp_present(self):
        page = render_fleet_html(_sweep_doc(), datetime(2026, 9, 3, 12, 30, 5, tzinfo=timezone.utc))
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn("<title>Fleet Dashboard</title>", page)
        self.assertIn("2026-09-03 12:30:05 UTC", page)
        for name in ("omen", "cc-conductor", "i5", "am4"):
            self.assertIn(f"<strong>{name}</strong>", page)
        self.assertIn("1 expected-up node DOWN", page)
        # every status label rendered once per node
        self.assertIn(">UP<", page)
        self.assertIn(">DOWN<", page)
        self.assertIn(">offline<", page)
        self.assertIn(">up (optional)<", page)
        # theme-aware inline CSS, no external assets
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("<link", page)

    def test_generated_at_accepts_string_and_naive_datetime(self):
        self.assertIn("hand-written stamp", render_fleet_html(_sweep_doc(), "hand-written stamp"))
        self.assertIn("2026-01-02 03:04:05 UTC", render_fleet_html(_sweep_doc(), datetime(2026, 1, 2, 3, 4, 5)))

    def test_html_is_escaped(self):
        page = render_fleet_html(_sweep_doc(), "t")
        self.assertNotIn("<b>conductor</b>", page)
        self.assertIn("&lt;b&gt;conductor&lt;/b&gt;", page)

    def test_down_node_note_shown_but_not_for_up_nodes(self):
        page = render_fleet_html(_sweep_doc(), "t")
        self.assertIn("parked for the RAM reclaim", page)

    def test_all_services_subrows_only_when_asked(self):
        doc = _sweep_doc()
        primary_only = render_fleet_html(doc, "t")
        self.assertNotIn("127.0.0.1:8082", primary_only)
        every = render_fleet_html(doc, "t", extras={"all_services": True})
        self.assertIn("127.0.0.1:8082", every)
        self.assertIn("ConnectionRefusedError", every)           # the failed extra probe names its error
        self.assertIn('<span class="bad">FAIL</span>', every)
        self.assertIn("127.0.0.1:8710", every)
        self.assertIn("all declared services", every)
        self.assertIn("primary service only", primary_only)

    def test_bare_row_list_is_accepted_and_summarized(self):
        page = render_fleet_html(_sweep_doc()["nodes"], "t")
        self.assertIn("<strong>omen</strong>", page)
        self.assertIn("1 expected-up node DOWN", page)

    def test_empty_sweep_renders(self):
        page = render_fleet_html({"summary": {"total": 0, "up": 0, "down": 0, "offline": 0}, "nodes": []}, "t")
        self.assertIn("no nodes in the sweep", page)

    def test_all_up_headline(self):
        doc = _sweep_doc()
        doc["nodes"] = [n for n in doc["nodes"] if n["status"] != "down"]
        doc["summary"] = {"total": 3, "up": 2, "down": 0, "offline": 1}
        self.assertIn("every expected-up node reachable", render_fleet_html(doc, "t"))

    def test_inventory_meta_timeout_and_notes_in_header(self):
        page = render_fleet_html(_sweep_doc(), "t", extras={
            "inventory_meta": {"tailnet": "tail-example", "updated": "2026-08-29"},
            "timeout": 2.5, "notes": ["keep-alive restarted from warm", "<danger>"],
        })
        self.assertIn("tail-example", page)
        self.assertIn("inventory updated 2026-08-29", page)
        self.assertIn("timeout 2.5 s", page)
        self.assertIn("keep-alive restarted from warm", page)
        self.assertIn("&lt;danger&gt;", page)

    def test_port_open_is_not_model_ready_caveat(self):
        self.assertIn("port-open", render_fleet_html(_sweep_doc(), "t"))


class TestRungStateBlock(unittest.TestCase):
    def test_absent_key_renders_no_block(self):
        page = render_fleet_html(_sweep_doc(), "t")
        self.assertNotIn("Rung state", page)

    def test_degraded_verdict_with_numbers(self):
        page = render_fleet_html(_sweep_doc(), "t", extras={"rung_state": _rung_state("degraded")})
        self.assertIn("Rung state (ADR-0044)", page)
        self.assertIn('class="metric bad">degraded<', page)
        self.assertIn("65.3", page)
        self.assertIn("106.0", page)
        self.assertIn("62%", page)
        self.assertIn("rot-cutover-20260903", page)
        self.assertIn("warn &lt; 90%", page)
        self.assertIn("fail &lt; 80%", page)
        self.assertIn("not of capacity", page)   # the ADR-0044 note travels with the verdict

    def test_at_rate_is_good_and_stale_is_warn(self):
        self.assertIn('class="metric good">at_rate<',
                      render_fleet_html(_sweep_doc(), "t", extras={"rung_state": _rung_state("at_rate")}))
        self.assertIn('class="metric warn">stale<',
                      render_fleet_html(_sweep_doc(), "t", extras={"rung_state": _rung_state("stale")}))

    def test_unknown_with_error_shows_error(self):
        st = {"rung": "omen-arc", "port": None, "verdict": "unknown", "error": "OSError: boom", "note": "n"}
        page = render_fleet_html(_sweep_doc(), "t", extras={"rung_state": st})
        self.assertIn('class="metric muted">unknown<', page)
        self.assertIn("OSError: boom", page)

    def test_none_renders_unavailable_line(self):
        page = render_fleet_html(_sweep_doc(), "t", extras={"rung_state": None})
        self.assertIn("Rung state unavailable", page)


class TestCollectRungState(unittest.TestCase):
    def test_injected_loader_dict_passes_through(self):
        self.assertEqual(collect_rung_state("omen-arc", loader=lambda rung: {"verdict": "warn", "rung": rung})["verdict"], "warn")

    def test_loader_raising_yields_none(self):
        def boom(rung):
            raise RuntimeError("no keep-alive")
        self.assertIsNone(collect_rung_state(loader=boom))

    def test_non_dict_yields_none(self):
        self.assertIsNone(collect_rung_state(loader=lambda rung: "not a dict"))

    def test_default_loader_never_raises(self):
        # With or without hearth.health.rungstate importable, this must return dict-or-None.
        out = collect_rung_state()
        self.assertTrue(out is None or isinstance(out, dict))


class TestBuildSweep(unittest.TestCase):
    def test_real_inventory_with_fake_prober(self):
        doc = build_sweep(INVENTORY, all_services=True, timeout=0.1, prober=_fake_prober({"nowhere"}))
        self.assertEqual(set(doc), {"summary", "nodes", "meta", "all_services", "timeout"})
        self.assertTrue(doc["nodes"])
        self.assertEqual(doc["summary"]["total"], len(doc["nodes"]))
        self.assertEqual(doc["summary"]["down"], 0)
        self.assertTrue(doc["all_services"])
        self.assertIn("omen", {n["name"] for n in doc["nodes"]})


class TestMain(unittest.TestCase):
    def test_default_out_is_repo_root_dashboard(self):
        self.assertEqual(DEFAULT_OUT.name, "FLEET-DASHBOARD.html")
        self.assertEqual(DEFAULT_OUT.parent, Path(__file__).resolve().parents[1])

    def test_end_to_end_writes_html_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            inv = Path(td) / "inv.toml"
            inv.write_text(
                '[meta]\ntailnet = "t-example"\nupdated = "2026-09-03"\n'
                '[[node]]\nname = "alpha"\nkind = "vm"\naddress = "alpha"\nexpect = "up"\npurpose = "p"\n'
                'checks = [{ service = "ssh", port = 22 }, { service = "api", port = 8080 }]\n'
                '[[node]]\nname = "beta"\nkind = "vm"\naddress = "beta"\nexpect = "up"\npurpose = "q"\n'
                'checks = [{ service = "ssh", port = 22 }]\n',
                encoding="utf-8")
            out = Path(td) / "sub" / "FLEET.html"
            jout = Path(td) / "feed.json"
            rc = main(["--inventory", str(inv), "--out", str(out), "--json-out", str(jout),
                       "--all-services", "--timeout", "0.1"],
                      prober=_fake_prober({"beta"}),
                      rung_state_fn=lambda rung: _rung_state("warn"))
            self.assertEqual(rc, 0)
            page = out.read_text(encoding="utf-8")
            self.assertIn("<strong>alpha</strong>", page)
            self.assertIn("<strong>beta</strong>", page)
            self.assertIn("1 expected-up node DOWN", page)
            self.assertIn("alpha:8080", page)               # --all-services sub-row
            self.assertIn('class="metric warn">warn<', page)
            self.assertIn("t-example", page)
            feed = json.loads(jout.read_text(encoding="utf-8"))
            self.assertEqual(feed["summary"]["down"], 1)
            self.assertEqual({n["name"] for n in feed["nodes"]}, {"alpha", "beta"})
            self.assertEqual(feed["rung_state"]["verdict"], "warn")
            self.assertTrue(feed["all_services"])
            self.assertTrue(feed["generated_at"].endswith("Z"))

    def test_no_rung_state_flag_omits_block(self):
        with tempfile.TemporaryDirectory() as td:
            inv = Path(td) / "inv.toml"
            inv.write_text('[[node]]\nname = "a"\naddress = "a"\nexpect = "up"\n'
                           'checks = [{ service = "ssh", port = 22 }]\n', encoding="utf-8")
            out = Path(td) / "d.html"
            rc = main(["--inventory", str(inv), "--out", str(out), "--no-rung-state"],
                      prober=_fake_prober(set()), rung_state_fn=lambda rung: _rung_state())
            self.assertEqual(rc, 0)
            self.assertNotIn("Rung state", out.read_text(encoding="utf-8"))

    def test_rung_state_loader_failure_still_writes_page(self):
        def boom(rung):
            raise RuntimeError("keep-alive missing")
        with tempfile.TemporaryDirectory() as td:
            inv = Path(td) / "inv.toml"
            inv.write_text('[[node]]\nname = "a"\naddress = "a"\nexpect = "up"\n'
                           'checks = [{ service = "ssh", port = 22 }]\n', encoding="utf-8")
            out = Path(td) / "d.html"
            rc = main(["--inventory", str(inv), "--out", str(out)], prober=_fake_prober(set()), rung_state_fn=boom)
            self.assertEqual(rc, 0)
            self.assertIn("Rung state unavailable", out.read_text(encoding="utf-8"))

    def test_missing_inventory_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            rc = main(["--inventory", str(Path(td) / "nope.toml"), "--out", str(Path(td) / "d.html")],
                      prober=_fake_prober(set()), rung_state_fn=lambda rung: None)
            self.assertEqual(rc, 2)

    def test_malformed_inventory_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            inv = Path(td) / "bad.toml"
            inv.write_text("[[node\nname = ", encoding="utf-8")
            rc = main(["--inventory", str(inv), "--out", str(Path(td) / "d.html")],
                      prober=_fake_prober(set()), rung_state_fn=lambda rung: None)
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
