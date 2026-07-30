import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hearth.projection.call_mix_dashboard import (
    build_html,
    classify_event,
    summarize,
    write_dashboard,
)


def _event(
    tool: str,
    ts: str,
    *,
    backend: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> dict:
    return {
        "schema": "hearth-event.v1",
        "ts": ts,
        "tool": tool,
        "ok": True,
        "backend": backend,
        "cost": {"tokens_in": tokens_in, "tokens_out": tokens_out, "watt_s": None},
        "args_preview": "SECRET PROMPT MUST NEVER RENDER",
        "error": None,
    }


class TestClassification(unittest.TestCase):
    def test_local_and_cloud_inference_are_separate(self) -> None:
        self.assertEqual(classify_event(_event("local_generate", "2026-07-01T00:00:00Z", backend="am4-moe")), "Local inference")
        self.assertEqual(classify_event(_event("local_generate", "2026-07-01T00:00:00Z", backend="gcp-gemini")), "Cloud / remote inference")

    def test_developer_and_retro_families(self) -> None:
        self.assertEqual(classify_event(_event("read_file", "2026-07-01T00:00:00Z")), "Filesystem")
        self.assertEqual(classify_event(_event("git_status", "2026-07-01T00:00:00Z")), "Git / VCS")
        self.assertEqual(classify_event(_event("mechnet_watchdog.hindsight", "2026-07-01T00:00:00Z")), "Learning / retro")


class TestProjection(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        ledger = root / "events.ndjson"
        events = [
            _event("mechnet_watchdog.patrol_snapshot", "2026-07-01T00:00:00Z"),
            _event("kernel_status", "2026-07-01T01:00:00Z"),
            _event("read_file", "2026-07-02T00:00:00Z"),
            _event("git_status", "2026-07-02T01:00:00Z"),
            _event("local_generate", "2026-07-02T02:00:00Z", backend="am4-moe", tokens_in=100, tokens_out=20),
            _event("local_generate", "2026-07-02T03:00:00Z", backend="gcp-gemini", tokens_in=200, tokens_out=40),
        ]
        ledger.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

        sentinel = root / "ollama-direct.ndjson"
        sentinel.write_text(
            json.dumps({"ts": "2026-07-02T04:00:00Z", "process": None}) + "\n",
            encoding="utf-8",
        )
        return ledger, sentinel

    def test_summary_and_render_are_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, sentinel = self._fixture(Path(tmp))
            summary = summarize(ledger, sentinel, registered_tool_count=47)
            self.assertEqual(summary["events"], 6)
            self.assertEqual(summary["observed_tool_count"], 5)
            self.assertEqual(summary["family_counts"]["Local inference"], 1)
            self.assertEqual(summary["family_counts"]["Cloud / remote inference"], 1)
            self.assertEqual(summary["sentinel"]["observations"], 1)

            rendered = build_html(
                summary,
                generated_at=datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc),
            )
            self.assertIn("Calls by semantic family", rendered)
            self.assertIn("Daily traffic mix", rendered)
            self.assertIn("Inference token flow by backend", rendered)
            self.assertNotIn("SECRET PROMPT MUST NEVER RENDER", rendered)
            self.assertNotIn("args_preview", rendered)

    def test_write_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger, sentinel = self._fixture(root)
            out = root / "call-mix.html"
            result = write_dashboard(out, ledger, sentinel)
            self.assertTrue(out.exists())
            self.assertEqual(result["events"], 6)
            self.assertGreater(result["bytes"], 1000)


if __name__ == "__main__":
    unittest.main()
