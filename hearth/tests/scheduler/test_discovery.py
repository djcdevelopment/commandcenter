"""steppewise-consul M2 (V7, V8): DiscoveryAdapter edge cases against a fake Consul HTTP server."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hearth.scheduler.discovery import DiscoveryAdapter, DiscoveryRefusal, Snapshot


def _row(seat: str, route: str, model: str, gen: str = "g1", status: str = "passing", alias: str | None = None) -> dict:
    meta = {"seat_id": seat, "generation": gen, "route": route, "wire_model_id": model}
    if alias:
        meta["alias"] = alias
    return {"Service": {"ID": f"{seat}@{gen}", "Meta": meta},
            "Checks": [{"Name": "engine", "Status": status, "Output": f"engine {status}", "ServiceID": f"{seat}@{gen}"},
                       {"Name": "serfHealth", "Status": "passing", "Output": "", "ServiceID": ""}]}


class _FakeConsul:
    """Scripted responses: each entry is (status, index, headers, rows)."""

    def __init__(self) -> None:
        self.script: list[tuple[int, int, dict, list]] = []
        self.seen: list[str] = []
        self.tokens: list[str] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.seen.append(self.path)
                fake.tokens.append(self.headers.get("X-Consul-Token", ""))
                status, index, headers, rows = fake.script.pop(0) if fake.script else (200, 1, {}, [])
                body = json.dumps(rows).encode()
                self.send_response(status)
                self.send_header("X-Consul-Index", str(index))
                self.send_header("X-Consul-KnownLeader", headers.get("leader", "true"))
                if headers.get("filtered"):
                    self.send_header("X-Consul-Results-Filtered-By-ACLs", "true")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@dataclass(frozen=True)
class _B:
    name: str
    endpoint: str
    models: tuple


@dataclass(frozen=True)
class _Pool:
    backends: tuple


POOL = _Pool((
    _B("omen-dense-27b", "http://127.0.0.1:18095", ("qwen3.8-27b",)),
    _B("omen-nemotron-74b", "http://127.0.0.1:18095", ("nemotron-3.5-bolt-74b-a9b",)),
    _B("omen-dense-27b-b", "http://127.0.0.1:18096", ("qwen3.8-27b",)),
    _B("am4-vllm", "http://10.44.0.2:8090", ("am4-dense-27b",)),
    _B("fx99-vllm", "http://192.168.12.220:18093", ("qwen2.5-coder-7b",)),
))


class DiscoveryAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.token = Path(self.tmp.name) / "read.token"
        self.token.write_text("test-read-token\n", encoding="utf-8")
        self.consul = _FakeConsul()
        self.adapter = DiscoveryAdapter(f"http://127.0.0.1:{self.consul.port}", str(self.token), freshness_s=30, wait_s=1)

    def tearDown(self) -> None:
        self.adapter.stop()
        self.consul.close()
        self.tmp.cleanup()

    # --- blocking-query index rules ---
    def test_first_read_sends_no_index_then_blocks_on_returned_index(self) -> None:
        self.consul.script = [(200, 7, {}, []), (200, 7, {}, [])]
        nxt = self.adapter.fetch_once(0)
        self.assertEqual(7, nxt)
        self.assertNotIn("index=", self.consul.seen[0])
        self.assertEqual("test-read-token", self.consul.tokens[0])
        nxt = self.adapter.fetch_once(nxt)          # timeout with no change: same index back
        self.assertEqual(7, nxt)
        self.assertIn("index=7&wait=1s", self.consul.seen[1])

    def test_repeated_index_keeps_blocking_on_it(self) -> None:
        self.consul.script = [(200, 9, {}, []), (200, 9, {}, [])]
        self.assertEqual(9, self.adapter.fetch_once(9))

    def test_index_going_backwards_resets_to_zero(self) -> None:
        self.consul.script = [(200, 3, {}, [])]
        self.assertEqual(0, self.adapter.fetch_once(10))

    def test_index_zero_from_server_is_never_sent_back(self) -> None:
        self.consul.script = [(200, 0, {}, [])]
        self.assertEqual(1, self.adapter.fetch_once(0))

    def test_http_429_and_500_raise_and_watcher_records_error(self) -> None:
        for code in (429, 500):
            self.consul.script = [(code, 1, {}, [])]
            with self.assertRaises(Exception):
                self.adapter.fetch_once(0)
        self.consul.script = [(500, 1, {}, [])]
        self.adapter.start()
        deadline = time.monotonic() + 3
        while self.adapter.snapshot().error is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual("http 500", self.adapter.snapshot().error)
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.evaluate(POOL)
        self.assertEqual("discovery_unavailable", ctx.exception.code)

    def test_connection_refused_is_unavailable(self) -> None:
        dead = DiscoveryAdapter("http://127.0.0.1:1", str(self.token), wait_s=1)
        with self.assertRaises(Exception):
            dead.fetch_once(0)
        with self.assertRaises(DiscoveryRefusal) as ctx:
            dead.evaluate(POOL)
        self.assertEqual("discovery_unavailable", ctx.exception.code)

    # --- freshness and trust (fail closed) ---
    def test_no_leader_refuses(self) -> None:
        self.consul.script = [(200, 2, {"leader": "false"}, [_row("omen-dense-27b", "http://127.0.0.1:18095", "qwen3.8-27b")])]
        self.adapter.fetch_once(0)
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.evaluate(POOL)
        self.assertEqual("discovery_no_leader", ctx.exception.code)

    def test_missing_leader_header_is_no_leader(self) -> None:   # Codex M2 M4
        self.adapter._set(Snapshot(consul_index=2, fetched_at=time.monotonic(), known_leader=None))
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.evaluate(POOL)
        self.assertEqual("discovery_no_leader", ctx.exception.code)

    def test_node_level_check_failure_makes_instance_not_passing(self) -> None:   # Codex M2 B1
        row = _row("omen-dense-27b", "http://127.0.0.1:18095", "qwen3.8-27b")
        row["Checks"][1]["Status"] = "critical"          # serfHealth (node scope, ServiceID "")
        self.consul.script = [(200, 3, {}, [row])]
        self.adapter.fetch_once(0)
        d = self.adapter.evaluate(POOL)
        self.assertNotIn("omen-dense-27b", d["eligible"])
        self.assertIn("serfHealth=critical", d["refused"]["omen-dense-27b"])

    def test_capacity_metadata_absent_or_invalid_is_capability_unknown(self) -> None:   # R2-M3
        rows = []
        for i, val in enumerate((None, "0", "-1", "abc")):
            r = _row(f"s{i}", "http://127.0.0.1:18095", "qwen3.8-27b", gen=f"g{i}")
            if val is not None:
                r["Service"]["Meta"]["max_model_len"] = val
            rows.append(r)
        self.consul.script = [(200, 3, {}, rows)]
        self.adapter.fetch_once(0)
        d = self.adapter.evaluate(POOL, ["omen-dense-27b"], need_tokens=100)
        self.assertEqual({}, d["eligible"])
        self.assertIn("capability_unknown", d["refused"]["omen-dense-27b"])

    def test_invalid_freshness_is_rejected_and_never_breaks_off_traffic(self) -> None:   # R3-m5
        import os
        from unittest.mock import patch
        import hearth.scheduler.discovery as disc
        for bad in (float("nan"), float("inf"), 0, -1):
            with self.assertRaises(ValueError):
                DiscoveryAdapter("http://127.0.0.1:1", str(self.token), freshness_s=bad)
        with patch.dict(os.environ, {"HEARTH_DISCOVERY_CONSUL": "http://127.0.0.1:1",
                                     "HEARTH_DISCOVERY_TOKEN_FILE": str(self.token),
                                     "HEARTH_DISCOVERY_FRESHNESS_S": "nan"}), \
                patch.object(disc, "_ADAPTER", None), patch.object(disc, "_ADAPTER_ERROR", None):
            self.assertIsNone(disc.shared_adapter())                 # no exception
            self.assertIn("invalid discovery configuration", disc.adapter_error())

    def test_acl_filtered_is_unauthorized_never_zero_seats(self) -> None:   # V7
        self.consul.script = [(200, 2, {"filtered": True}, [])]
        self.adapter.fetch_once(0)
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.evaluate(POOL)
        self.assertEqual("discovery_unauthorized", ctx.exception.code)

    def test_stale_snapshot_refuses(self) -> None:
        self.adapter._set(Snapshot(consul_index=5, fetched_at=time.monotonic() - 31, known_leader=True))
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.evaluate(POOL)
        self.assertEqual("discovery_stale", ctx.exception.code)

    # --- mapping and eligibility ---
    def test_mapping_by_endpoint_and_model_or_alias(self) -> None:
        self.consul.script = [(200, 4, {}, [
            _row("omen-dense-27b", "http://127.0.0.1:18095", "qwen3.8-27b"),
            _row("omen-dense-27b-b", "http://127.0.0.1:18096", "qwen3.8-27b", status="critical"),
            _row("am4-dense-27b", "http://10.44.0.2:8090", "qwen3-27b", gen="remote", alias="am4-dense-27b"),
            _row("fx99-coder-7b", "http://192.168.12.220:18093", "qwen2.5-coder-7b", gen="remote", status="critical"),
        ])]
        self.adapter.fetch_once(0)
        d = self.adapter.evaluate(POOL)
        self.assertEqual({"omen-dense-27b", "am4-vllm"}, set(d["eligible"]))      # 74B shares :18095 but not the model
        self.assertNotIn("omen-nemotron-74b", d["eligible"])
        self.assertIn("engine=critical", d["refused"]["omen-dense-27b-b"])
        self.assertIn("engine=critical", d["refused"]["fx99-vllm"])
        self.assertEqual("consul", d["discovery_source"])
        self.assertEqual(4, d["consul_index"])

    def test_recheck_detects_generation_change_and_missing_instance(self) -> None:
        self.consul.script = [(200, 4, {}, [_row("omen-dense-27b", "http://127.0.0.1:18095", "qwen3.8-27b", gen="g2")])]
        self.adapter.fetch_once(0)
        self.assertIn("snapshot_age_ms", self.adapter.recheck("omen-dense-27b@g2", "g2"))
        with self.assertRaises(DiscoveryRefusal) as ctx:
            self.adapter.recheck("omen-dense-27b@g1", "g1")
        self.assertEqual("seat_lost_before_dispatch", ctx.exception.code)

    def test_token_never_appears_in_refusal_text(self) -> None:
        self.consul.script = [(500, 1, {}, [])]
        try:
            self.adapter.fetch_once(0)
        except Exception as exc:  # noqa: BLE001
            self.assertNotIn("test-read-token", str(exc))


if __name__ == "__main__":
    unittest.main()
