from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from tempfile import mkdtemp
from unittest import TestCase
from unittest.mock import patch

from hearth.projection import rebuild as rebuild_module
from hearth.projection.rebuild import (
    EXPECTED_FILES,
    STAGING_DIRNAME,
    rebuild_knowledge,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_RUNS = REPO_ROOT / "fixtures" / "workflow" / "runs"


class RebuildScopedTestCase(TestCase):
    """Every test runs against a temp sandbox — the real knowledge/ and runs/ dirs are
    never touched. Fixture run trees are copied in (this also strips the 'fixtures'
    path component, so the fixture-taint guard sees an ordinary non-repo out dir)."""

    def setUp(self) -> None:
        self.scope = Path(mkdtemp()).resolve()
        self._previous_scope = os.environ.get("HEARTH_SCOPE")
        os.environ["HEARTH_SCOPE"] = str(self.scope)
        shutil.copytree(FIXTURE_RUNS, self.scope / "runs")

    def tearDown(self) -> None:
        if self._previous_scope is None:
            os.environ.pop("HEARTH_SCOPE", None)
        else:
            os.environ["HEARTH_SCOPE"] = self._previous_scope
        shutil.rmtree(self.scope, ignore_errors=True)

    def _write_ledger(self, relative_path: str = "hearth/var/ledger/events.ndjson") -> Path:
        ledger = self.scope / relative_path
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {
                "schema": "hearth-event.v1",
                "event_id": "he_a",
                "ts": "2026-07-04T00:00:00+00:00",
                "caller": {"id": "local-1", "runner_class": "local", "node": "omen"},
                "tool": "run_tests",
                "ok": True,
                "duration_ms": 1000,
                "cost": {"tokens_in": 10, "tokens_out": 50, "watt_s": None},
                "task_id": None,
            },
        ]
        with ledger.open("w", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        return ledger


class RebuildBasicTests(RebuildScopedTestCase):
    def test_rebuild_produces_every_expected_file(self) -> None:
        self._write_ledger()
        result = rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertTrue(result["ok"])
        self.assertTrue(result["corpus_digest"].startswith("sha256:"))
        self.assertGreater(result["corpus_event_count"], 0)

        knowledge_dir = self.scope / "knowledge"
        for name in EXPECTED_FILES:
            path = knowledge_dir / name
            self.assertTrue(path.is_file(), name)
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertIn("contract_version", document, name)

    def test_staging_dir_removed_after_success(self) -> None:
        self._write_ledger()
        rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        staging_dir = self.scope / "knowledge" / STAGING_DIRNAME
        self.assertFalse(staging_dir.exists())

    def test_missing_ledger_still_rebuilds_event_derived_files(self) -> None:
        # No ledger written: capacity.json still materializes as an empty document
        # (build_capacity_document tolerates a missing ledger), everything else
        # comes from the fixture runs corpus.
        result = rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertTrue(result["ok"])
        capacity = json.loads((self.scope / "knowledge" / "capacity.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(capacity["bucket_count"], 0)
        offload = json.loads((self.scope / "knowledge" / "offload.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(offload["bucket_count"], 0)


class RebuildDeterminismTests(RebuildScopedTestCase):
    """The load-bearing deliverable: rebuild twice from the same fixture corpus into
    two separate output dirs must produce byte-identical files for every knowledge
    file. No projector output in this repo embeds a wall-clock field, so no
    documented exclusion is needed — every byte is compared."""

    def test_two_rebuilds_are_byte_identical(self) -> None:
        self._write_ledger()

        first_out = "knowledge-first"
        second_out = "knowledge-second"
        first = rebuild_knowledge(out=first_out, ledger_path="hearth/var/ledger/events.ndjson")
        second = rebuild_knowledge(out=second_out, ledger_path="hearth/var/ledger/events.ndjson")

        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(first["corpus_digest"], second["corpus_digest"])
        self.assertEqual(first["corpus_event_count"], second["corpus_event_count"])
        self.assertEqual(first["watermark"], second["watermark"])

        first_dir = self.scope / first_out
        second_dir = self.scope / second_out
        for name in EXPECTED_FILES:
            first_bytes = (first_dir / name).read_bytes()
            second_bytes = (second_dir / name).read_bytes()
            self.assertEqual(first_bytes, second_bytes, f"{name} differs between rebuilds")

    def test_rebuild_is_byte_identical_to_incremental_project(self) -> None:
        """A from-zero rebuild must agree byte-for-byte with the normal incremental
        project() + project_capacity_knowledge() path over the identical corpus —
        the rebuild button must not silently compute something different."""
        from hearth.toolsurface.knowledge import project, project_capacity_knowledge

        self._write_ledger()

        rebuild_out = "knowledge-rebuilt"
        rebuild_result = rebuild_knowledge(out=rebuild_out, ledger_path="hearth/var/ledger/events.ndjson")
        self.assertTrue(rebuild_result["ok"])

        incremental_out = "knowledge-incremental"
        project_result = project(out=incremental_out)
        self.assertTrue(project_result["ok"], project_result)
        project_capacity_knowledge(ledger_path="hearth/var/ledger/events.ndjson", out=incremental_out)
        from hearth.toolsurface.knowledge import project_offload_knowledge
        project_offload_knowledge(ledger_path="hearth/var/ledger/events.ndjson", out=incremental_out)

        rebuilt_dir = self.scope / rebuild_out
        incremental_dir = self.scope / incremental_out
        for name in EXPECTED_FILES:
            self.assertEqual(
                (rebuilt_dir / name).read_bytes(),
                (incremental_dir / name).read_bytes(),
                f"{name} differs between rebuild and incremental project",
            )


class RebuildFailureIsolationTests(RebuildScopedTestCase):
    """A projector fault partway through must never touch the live knowledge/ dir,
    and must never leave the staging directory behind."""

    def test_failure_mid_rebuild_leaves_live_knowledge_untouched(self) -> None:
        self._write_ledger()

        # First, populate a real, live knowledge/ dir via a normal rebuild.
        first = rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertTrue(first["ok"])
        knowledge_dir = self.scope / "knowledge"
        live_snapshot = {
            name: (knowledge_dir / name).read_bytes()
            for name in EXPECTED_FILES
        }

        # Now break one projector (coverage runs after findings/associations in the
        # dependency order) so the rebuild fails after some staged files already
        # exist, but before the swap.
        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic projector fault")

        with patch.object(rebuild_module, "materialize_coverage", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        # Live files are byte-identical to what they were before the failed rebuild.
        for name in EXPECTED_FILES:
            self.assertEqual(
                (knowledge_dir / name).read_bytes(), live_snapshot[name],
                f"{name} was touched despite a mid-rebuild failure",
            )

    def test_staging_dir_removed_after_failure(self) -> None:
        self._write_ledger()

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic projector fault")

        with patch.object(rebuild_module, "materialize_policy", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        staging_dir = self.scope / "knowledge" / STAGING_DIRNAME
        self.assertFalse(staging_dir.exists())

    def test_failure_before_any_live_dir_exists_creates_no_live_files(self) -> None:
        # No prior rebuild: knowledge/ never existed. A failure must leave it with no
        # live knowledge files (the staging subdir may transiently be created as part
        # of staging setup, but it is always cleaned up — asserted separately — and no
        # top-level knowledge/*.json file is ever written before the swap).
        self._write_ledger()

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic projector fault")

        with patch.object(rebuild_module, "materialize_findings", side_effect=_boom):
            with self.assertRaises(RuntimeError):
                rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")

        knowledge_dir = self.scope / "knowledge"
        for name in EXPECTED_FILES:
            self.assertFalse((knowledge_dir / name).exists(), name)


class RebuildToolSurfaceTests(RebuildScopedTestCase):
    """rebuild_knowledge must be reachable the same way other knowledge tools are:
    importable from hearth.toolsurface.knowledge and listed in get_tools()."""

    def test_rebuild_knowledge_is_registered_as_a_knowledge_tool(self) -> None:
        from hearth.toolsurface.knowledge import get_tools, rebuild_knowledge as ts_rebuild_knowledge

        names = [fn.__name__ for fn in get_tools()]
        self.assertIn("rebuild_knowledge", names)

    def test_rebuild_knowledge_toolsurface_wrapper_delegates(self) -> None:
        from hearth.toolsurface.knowledge import rebuild_knowledge as ts_rebuild_knowledge

        self._write_ledger()
        result = ts_rebuild_knowledge(ledger_path="hearth/var/ledger/events.ndjson")
        self.assertTrue(result["ok"])
        self.assertTrue((self.scope / "knowledge" / "policy.json").is_file())


class RebuildScopeTests(RebuildScopedTestCase):
    def test_out_dir_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rebuild_knowledge(out="../leaky-knowledge")

    def test_sources_outside_scope_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rebuild_knowledge(sources=["../escape"])


class RebuildMainExitCodeTests(RebuildScopedTestCase):
    """main() is the timer entry point (knowledge_rebuild, every 21600s). Its exit code
    is the only staleness signal that leaves the process, so the acknowledged-vs-stale
    distinction has to be pinned here."""

    def _run_main(self, *extra: str) -> tuple[int, str]:
        ledger = self._write_ledger()
        argv = ["--out", "knowledge", "--ledger", str(ledger),
                "--allow-fixture-sources", "--skip-bridge", *extra]
        with patch("builtins.print") as printed:
            code = rebuild_module.main(argv)
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        return code, output

    def _ack_all_stale(self) -> dict[str, str]:
        """Rebuild once, then acknowledge every document the guard calls stale, pinned
        to the watermark the projection actually produced.

        No artificial ageing: the fixture corpus is already far behind the synthetic
        ledger head, and pinning to the real projected value is what the mechanism
        does in production -- a hardcoded pin would silently stop matching the moment
        the rebuild regenerated the document.
        """
        from hearth.projection.freshness import check_freshness
        self._run_main()
        report = check_freshness(
            knowledge_dir=self.scope / "knowledge",
            ledger_path=self._write_ledger(),
            sources=[self.scope / "runs"],
            cursor_path=self.scope / "hearth" / "var" / "projection_cursor.json",
        )
        stale = {check["name"]: check["watermark"] for check in report["checks"]
                 if check["stale"] and check["kind"] == "document"}
        self.assertTrue(stale, "fixture rebuild produced no stale document to acknowledge")
        self._write_ack([
            {"name": name, "watermark": watermark,
             "reason": "structurally stale pending ADR-0027",
             "expires_at_ledger_head": "2099-01-01T00:00:00Z"}
            for name, watermark in stale.items()
        ])
        return stale

    def _write_ack(self, entries: list[dict]) -> None:
        (self.scope / "knowledge" / "freshness_ack.json").write_text(json.dumps({
            "contract_version": "freshness-ack.v1", "active": True, "author": "derek",
            "created": "2026-07-30", "acknowledged": entries,
        }), encoding="utf-8")

    def test_main_returns_one_on_unacknowledged_staleness(self) -> None:
        self._run_main()

        code, output = self._run_main()

        self.assertEqual(code, 1)
        self.assertIn("COMPLETED WITH STALENESS", output)

    def test_main_returns_zero_when_all_staleness_is_acknowledged(self) -> None:
        self._ack_all_stale()

        code, output = self._run_main()

        self.assertEqual(code, 0)
        self.assertIn("ACKNOWLEDGED STALENESS", output)
        self.assertNotIn("COMPLETED WITH STALENESS", output)

    def test_no_acknowledge_flag_ignores_the_ack_file(self) -> None:
        self._ack_all_stale()

        code, output = self._run_main("--no-acknowledge")

        self.assertEqual(code, 1)
        self.assertIn("COMPLETED WITH STALENESS", output)

    def test_bridge_failure_returns_one_even_when_staleness_is_acknowledged(self) -> None:
        """A bridge failure is never ackable: it is the exact fault that froze eleven
        projections for 26 days."""
        self._ack_all_stale()
        ledger = self._write_ledger()

        with patch("hearth.projection.ledger_adapter.project_ledger",
                   side_effect=RuntimeError("bridge is down")):
            with patch("builtins.print") as printed:
                code = rebuild_module.main(["--out", "knowledge", "--ledger", str(ledger),
                                            "--allow-fixture-sources"])
        output = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)

        self.assertEqual(code, 1)
        self.assertIn("bridge: FAILED", output)
        self.assertIn("COMPLETED WITH STALENESS", output)
