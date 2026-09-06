import json
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from hearth.kernel.capabilities import TOOL_CAPABILITY
from hearth.projection.call_mix_dashboard import (
    DOOR_STATUS_TOOLS,
    FAMILY_COLORS,
    FAMILY_ORDER,
    HEALTH_TOOLS,
    MACRO_COLORS,
    MACRO_ORDER,
    MEDIA_CAPABILITY_FAMILIES,
    _macro_for_family,
    build_html,
    classify_event,
    summarize,
    write_dashboard,
)

TS = "2026-07-01T00:00:00Z"


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

    def test_execution_polling_is_door_status_not_unclassified(self) -> None:
        for tool in (
            "kernel_status",
            "list_execution_providers",
            "list_operations",
            "list_owned_executions",
            "get_image_status",
            "get_render_status",
            "get_image_session",
            "get_media_status",
            "list_image_workflows",
            "list_image_lanes",
            "list_render_lanes",
        ):
            with self.subTest(tool=tool):
                self.assertEqual(classify_event(_event(tool, "2026-07-01T00:00:00Z")), "Door status")
    def test_image_and_media_work_have_their_own_families(self) -> None:
        for tool in ("submit_image", "cancel_image", "start_image_session", "stop_image_session"):
            with self.subTest(tool=tool):
                self.assertEqual(classify_event(_event(tool, TS)), "Image generation")
        for tool in (
            "submit_render",
            "cancel_render",
            "submit_podcast",
            "submit_media_pipeline",
            "cancel_media",
        ):
            with self.subTest(tool=tool):
                self.assertEqual(classify_event(_event(tool, TS)), "Media / video render")

    def test_polling_precedes_the_media_capability_branch(self) -> None:
        """A status poll is door traffic, never image/media work.

        Fails if the capability branch is ever hoisted above the door-status
        branch in classify_event.
        """
        overlap = sorted(
            tool
            for tool in DOOR_STATUS_TOOLS
            if TOOL_CAPABILITY.get(tool) in MEDIA_CAPABILITY_FAMILIES
        )
        self.assertEqual(
            overlap,
            [
                "get_image_session",
                "get_image_status",
                "get_media_status",
                "get_render_status",
                "list_image_lanes",
                "list_image_workflows",
                "list_render_lanes",
            ],
        )
        for tool in overlap:
            with self.subTest(tool=tool):
                self.assertEqual(classify_event(_event(tool, TS)), "Door status")

    def test_classifier_agrees_with_the_capability_authority(self) -> None:
        """Iterate TOOL_CAPABILITY itself -- no second list of tool names here."""
        outcomes: Counter[str] = Counter()
        for tool, capability in TOOL_CAPABILITY.items():
            family = MEDIA_CAPABILITY_FAMILIES.get(capability)
            if family is None:
                continue
            expected = "Door status" if tool in DOOR_STATUS_TOOLS else family
            with self.subTest(tool=tool, capability=capability):
                self.assertEqual(classify_event(_event(tool, TS)), expected)
            outcomes[expected] += 1
        self.assertGreater(outcomes["Image generation"], 0)
        self.assertGreater(outcomes["Media / video render"], 0)
        self.assertGreater(outcomes["Door status"], 0)
        # Floor: the four capabilities covered 17 mounted tools at e9822d3.
        self.assertGreaterEqual(sum(outcomes.values()), 17)

    def test_every_family_has_a_colour_and_a_macro(self) -> None:
        self.assertEqual(set(FAMILY_COLORS), set(FAMILY_ORDER))
        self.assertEqual(set(MACRO_COLORS), set(MACRO_ORDER))
        for family in FAMILY_ORDER:
            with self.subTest(family=family):
                self.assertIn(_macro_for_family(family), MACRO_ORDER)
        self.assertEqual(_macro_for_family("Image generation"), "Media")
        self.assertEqual(_macro_for_family("Media / video render"), "Media")


class TestGuardedCounterFloor(unittest.TestCase):
    """The public `operational_observations` counter must not move backward.

    Both sets feed it downstream. Snapshots are the contents at e9822d3.
    """

    E9822D3_DOOR_STATUS_TOOLS = {
        "kernel_status",
        "list_execution_providers",
        "list_operations",
        "list_owned_executions",
        "get_image_status",
        "get_render_status",
    }
    E9822D3_HEALTH_TOOLS = {
        "mechnet_watchdog.patrol_snapshot",
        "mechnet_watchdog.watchfire",
        "mechnet_watchdog.patrol_trend",
        "mechnet_watchdog.revive",
        "mechnet_watchdog.rung_state",
        "bankedfire_drain.tick",
        "patrol",
        "remediate",
    }

    def test_door_status_tools_only_ever_grows(self) -> None:
        self.assertTrue(
            self.E9822D3_DOOR_STATUS_TOOLS.issubset(DOOR_STATUS_TOOLS),
            f"dropped from DOOR_STATUS_TOOLS: "
            f"{sorted(self.E9822D3_DOOR_STATUS_TOOLS - DOOR_STATUS_TOOLS)}",
        )

    def test_health_tools_match_the_accepted_snapshot(self) -> None:
        self.assertEqual(HEALTH_TOOLS, self.E9822D3_HEALTH_TOOLS)


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
    def test_render_shows_media_families_and_the_macro_legend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "events.ndjson"
            events = [
                _event("submit_image", "2026-08-01T00:00:00Z"),
                _event("submit_render", "2026-08-01T01:00:00Z"),
                _event("get_image_status", "2026-08-01T02:00:00Z"),
                _event("get_media_status", "2026-08-01T03:00:00Z"),
                _event("patrol", "2026-08-01T04:00:00Z"),
            ]
            ledger.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
            )

            summary = summarize(ledger, None, registered_tool_count=47)
            counts = summary["family_counts"]
            self.assertEqual(counts["Image generation"], 1)
            self.assertEqual(counts["Media / video render"], 1)
            self.assertEqual(counts["Door status"], 2)
            self.assertEqual(counts["Health / automation"], 1)
            self.assertEqual(counts["Other"], 0)
            self.assertEqual(summary["daily"][0]["series"]["Media"], 2)

            rendered = build_html(
                summary,
                generated_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            )
            self.assertIn("Image generation", rendered)
            self.assertIn("Media / video render", rendered)
            self.assertIn(">Media</text>", rendered)
            self.assertNotIn("SECRET PROMPT MUST NEVER RENDER", rendered)


if __name__ == "__main__":
    unittest.main()
