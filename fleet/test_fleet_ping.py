"""Unit tests for fleet-ping — pure logic only, no real network.

Run from repo root:  python -m unittest fleet.test_fleet_ping
"""
import unittest
from pathlib import Path

from fleet.fleet_ping import (
    classify, is_failure, resolve_targets, summarize, sweep, load_inventory,
)

INVENTORY = Path(__file__).with_name("inventory.toml")


class TestClassify(unittest.TestCase):
    def test_up_expected_and_reachable(self):
        self.assertEqual(classify("up", True), "up")

    def test_up_expected_but_down_is_failure_status(self):
        self.assertEqual(classify("up", False), "down")

    def test_optional_reachable_is_up_opt(self):
        self.assertEqual(classify("optional", True), "up-opt")

    def test_optional_unreachable_is_offline_not_down(self):
        self.assertEqual(classify("optional", False), "offline")


class TestIsFailure(unittest.TestCase):
    def test_only_expected_up_and_down_counts_as_failure(self):
        self.assertTrue(is_failure("up", False))
        self.assertFalse(is_failure("up", True))
        self.assertFalse(is_failure("optional", False))  # offline is not a failure
        self.assertFalse(is_failure("optional", True))


class TestResolveTargets(unittest.TestCase):
    def test_primary_only_by_default(self):
        node = {"address": "h", "checks": [{"service": "ssh", "port": 22},
                                           {"service": "ollama", "port": 11434}]}
        t = resolve_targets(node, all_services=False)
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["service"], "ssh")

    def test_all_services_returns_every_check(self):
        node = {"address": "h", "checks": [{"service": "ssh", "port": 22},
                                           {"service": "ollama", "port": 11434}]}
        self.assertEqual(len(resolve_targets(node, all_services=True)), 2)

    def test_check_host_overrides_node_address(self):
        node = {"address": "shell-host", "checks": [{"service": "ollama", "port": 11434, "host": "backend-host"}]}
        t = resolve_targets(node, all_services=False)
        self.assertEqual(t[0]["host"], "backend-host")

    def test_check_without_host_falls_back_to_address(self):
        node = {"address": "shell-host", "checks": [{"service": "ssh", "port": 22}]}
        self.assertEqual(resolve_targets(node, all_services=False)[0]["host"], "shell-host")

    def test_no_checks_yields_nothing(self):
        self.assertEqual(resolve_targets({"address": "h"}, all_services=False), [])


class TestSweep(unittest.TestCase):
    def _fake_prober(self, down_hosts):
        def prober(host, port, timeout):
            if host in down_hosts:
                return False, None, "TimeoutError"
            return True, 5.0, None
        return prober

    def test_primary_reachability_and_statuses(self):
        nodes = [
            {"name": "a", "expect": "up", "address": "a", "checks": [{"service": "ssh", "port": 22}]},
            {"name": "b", "expect": "up", "address": "b", "checks": [{"service": "ssh", "port": 22}]},
            {"name": "c", "expect": "optional", "address": "c", "checks": [{"service": "ssh", "port": 22}]},
        ]
        rows = sweep(nodes, all_services=False, timeout=1.0, prober=self._fake_prober({"b", "c"}))
        by = {r["name"]: r for r in rows}
        self.assertEqual(by["a"]["status"], "up")
        self.assertEqual(by["b"]["status"], "down")       # expected up, unreachable
        self.assertEqual(by["c"]["status"], "offline")    # optional, unreachable
        # exit-gate logic: only b is a real failure
        self.assertTrue(any(is_failure(r["expect"], r["reachable"]) for r in rows))

    def test_primary_target_decides_reachability_even_with_more_services(self):
        # primary (ssh) down but secondary (ollama) up -> node counts as down
        node = {"name": "x", "expect": "up", "address": "shell",
                "checks": [{"service": "ssh", "port": 22},
                           {"service": "ollama", "port": 11434, "host": "backend"}]}
        rows = sweep([node], all_services=True, timeout=1.0, prober=self._fake_prober({"shell"}))
        self.assertFalse(rows[0]["reachable"])
        self.assertEqual(rows[0]["status"], "down")
        self.assertTrue(rows[0]["probes"][1]["reachable"])  # backend still probed & up

    def test_empty_nodes(self):
        self.assertEqual(sweep([], all_services=False, timeout=1.0, prober=self._fake_prober(set())), [])


class TestSummarize(unittest.TestCase):
    def test_counts(self):
        rows = [{"status": "up"}, {"status": "up-opt"}, {"status": "down"}, {"status": "offline"}]
        s = summarize(rows)
        self.assertEqual((s["up"], s["down"], s["offline"], s["total"]), (2, 1, 1, 4))


class TestInventoryParses(unittest.TestCase):
    def test_real_inventory_loads_and_has_expected_shape(self):
        inv = load_inventory(INVENTORY)
        names = {n["name"] for n in inv["nodes"]}
        # a representative sample the fleet depends on
        for expected in {"omen", "cc-conductor", "am4", "claudefarm1", "omen-worker-1"}:
            self.assertIn(expected, names)
        # every node has the fields the sweep needs
        for n in inv["nodes"]:
            self.assertIn("name", n)
            self.assertIn("expect", n)
            self.assertIn("checks", n)
            self.assertTrue(n["checks"], f"{n['name']} has no checks")


if __name__ == "__main__":
    unittest.main()
