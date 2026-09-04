"""Doc/ADR bench harness (M1a): arms carry a model, rows count as evidence,
the panel is held out, and a dry run dispatches nothing."""
from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import tempfile
from unittest import TestCase, mock

from hearth.experiments import doc_adr_bench, run_doc_adr_bench
from hearth.experiments.doc_adr_bench import (
    BENCH_CALLER_ID, DOC_ADR_TASKS, arm_label, bench_identity, bench_summary, parse_arm,
    plan_flat_matrix, run_flat_cell, run_flat_matrix, task_payload_bytes,
)
from hearth.experiments.panel import PanelConflict
from hearth.observation.identity import DispatchIdentity, current_identity
from hearth.toolsurface.backends import Backend, Pool

TASK = "adr-0042-vs-launcher"
COMPACT = ["adr-0042-vs-launcher", "adr-0041-claims-vs-receipts"]
POUR = ["omen-swap:phi4-vk1", "omen-swap:qwen14b-vk1"]
FX99 = ("fx99-ollama", "qwen2.5:14b")
FLASH = ("gcp-gemini", "gemini-3.5-flash")


def _b(name: str, models: list[str], node: str | None = None, hw: str | None = None,
       context_bytes: int | None = None, retired: bool = False) -> Backend:
    settings: dict = {}
    if node:
        settings["node"] = node
    if hw:
        settings["hardware_profile_id"] = hw
    if context_bytes is not None:
        settings["context_bytes"] = context_bytes
    return Backend(name=name, endpoint=f"http://{name}", api="openai",
                   models=tuple(models), retired=retired, settings=settings)


def _fake_pool() -> Pool:
    """The live pool's shape (names, models, nodes, budgets) without the file."""
    hw = "omen-285k-dual-b70-2026H2"
    return Pool(default="omen-arc", backends=(
        _b("omen-arc", ["qwen3-30b-a3b"], "omen", hw, 229376),
        _b("omen-swap", ["phi4-vk1", "phi4-vk0", "qwen14b-vk1", "qwen14b-vk0",
                         "mistral24b-vk1", "qwen38-27b-dual"], "omen", hw, 14336),
        _b("fx99-ollama", ["qwen2.5:14b", "qwen2.5:7b"], "fx99", "fx99-2070super-2026H2", 24576),
        _b("gcp-gemini", ["gemini-3.5-flash"], "gcp-vertex"),
        _b("gcp-gemini-pro", ["gemini-3.1-pro-preview"], "gcp-vertex"),
    ))


class FakeGen:
    """Records every call's kwargs and the dispatch identity in force at call time."""

    def __init__(self, score: int = 80, ok: bool = True) -> None:
        self.score = score
        self.ok = ok
        self.calls: list[dict] = []

    def __call__(self, prompt, model=None, backend=None, system=None, files=None,
                 max_tokens=None, timeout_s=None, **extra):
        call = {"prompt": prompt, "model": model, "backend": backend, "system": system,
                "files": files, "max_tokens": max_tokens, "timeout_s": timeout_s,
                "extra": extra, "identity": current_identity()}
        self.calls.append(call)
        if "Output ONLY a final line" in prompt:                       # judge seat
            return {"ok": True, "text": f"ok\nSCORE: {self.score}", "model": model}
        if not self.ok:
            return {"ok": False, "error": "connect refused", "error_code": "cold_start"}
        return {"ok": True, "text": f"ANSWER from {backend}/{model}",
                "model": model or f"{backend}-default", "routed_by": f"pinned:{backend}",
                "tokens_in": 1200, "tokens_out": 300, "duration_ms": 900}

    @property
    def arm_calls(self) -> list[dict]:
        return [c for c in self.calls if "Output ONLY a final line" not in c["prompt"]]

    @property
    def judge_calls(self) -> list[dict]:
        return [c for c in self.calls if "Output ONLY a final line" in c["prompt"]]


class ArmParsingTests(TestCase):
    def test_bare_backend_means_rung_default(self) -> None:
        self.assertEqual(parse_arm("omen-arc"), ("omen-arc", None))
        self.assertEqual(arm_label("omen-arc"), "omen-arc")

    def test_backend_colon_model_splits_on_first_colon_only(self) -> None:
        self.assertEqual(parse_arm("omen-swap:phi4-vk1"), ("omen-swap", "phi4-vk1"))
        # an Ollama tag keeps its own colon
        self.assertEqual(parse_arm("fx99-ollama:qwen2.5:7b"), ("fx99-ollama", "qwen2.5:7b"))
        self.assertEqual(arm_label(("fx99-ollama", "qwen2.5:7b")), "fx99-ollama:qwen2.5:7b")

    def test_pair_list_and_dict_shapes(self) -> None:
        self.assertEqual(parse_arm(("omen-swap", "phi4-vk1")), ("omen-swap", "phi4-vk1"))
        self.assertEqual(parse_arm(["omen-swap", None]), ("omen-swap", None))
        self.assertEqual(parse_arm({"backend": "omen-swap", "model": "qwen14b-vk1"}),
                         ("omen-swap", "qwen14b-vk1"))

    def test_rejects_unpinned_and_malformed_arms(self) -> None:
        for bad in ("", "   ", "omen-swap:", ":phi4", (None, "phi4-vk1"), ("only",), 42,
                    {"model": "phi4-vk1"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_arm(bad)


class FlatCellTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_model_is_threaded_to_generate_and_kept_on_the_row(self) -> None:
        gen = FakeGen()
        row = run_flat_cell(TASK, ("omen-swap", "phi4-vk1"), gen, judges=[FX99, FLASH],
                            max_tokens=512, timeout_s=120, pool=self.pool)
        arm = gen.arm_calls[0]
        self.assertEqual(arm["backend"], "omen-swap")
        self.assertEqual(arm["model"], "phi4-vk1")
        self.assertEqual(arm["max_tokens"], 512)
        self.assertEqual(arm["timeout_s"], 120)
        self.assertEqual(arm["files"], DOC_ADR_TASKS[TASK]["files"])
        self.assertEqual(arm["prompt"], DOC_ADR_TASKS[TASK]["prompt"])
        self.assertNotIn("task", arm["extra"])          # pinned, never tag-routed
        self.assertEqual(row["backend"], "omen-swap")
        self.assertEqual(row["model"], "phi4-vk1")
        self.assertEqual(row["model_requested"], "phi4-vk1")
        self.assertEqual(row["arm"], "omen-swap:phi4-vk1")
        self.assertEqual(row["routed_by"], "pinned:omen-swap")
        self.assertEqual(row["score"]["mean"], 80)
        self.assertEqual({(c["backend"], c["model"]) for c in gen.judge_calls}, {FX99, FLASH})

    def test_string_arm_and_omitted_budgets_leave_rung_defaults(self) -> None:
        gen = FakeGen()
        row = run_flat_cell(TASK, "omen-swap:qwen14b-vk1", gen, judges=[FX99], pool=self.pool)
        arm = gen.arm_calls[0]
        self.assertEqual((arm["backend"], arm["model"]), ("omen-swap", "qwen14b-vk1"))
        self.assertIsNone(arm["max_tokens"])
        self.assertIsNone(arm["timeout_s"])
        self.assertEqual(row["arm"], "omen-swap:qwen14b-vk1")

    def test_bare_backend_arm_reports_the_model_the_rung_served(self) -> None:
        gen = FakeGen()
        row = run_flat_cell(TASK, "omen-arc", gen, judges=[FX99, FLASH], pool=self.pool)
        self.assertIsNone(gen.arm_calls[0]["model"])
        self.assertEqual(row["backend"], "omen-arc")
        self.assertEqual(row["model"], "omen-arc-default")
        self.assertIsNone(row["model_requested"])
        self.assertEqual(row["arm"], "omen-arc")

    def test_failed_dispatch_keeps_requested_model_and_error_code(self) -> None:
        gen = FakeGen(ok=False)
        row = run_flat_cell(TASK, ("omen-swap", "phi4-vk1"), gen, judges=[FX99], pool=self.pool)
        self.assertFalse(row["ok"])
        self.assertIsNone(row["score"])
        self.assertEqual(row["model"], "phi4-vk1")
        self.assertEqual(row["error_code"], "cold_start")
        self.assertEqual(gen.judge_calls, [])            # nothing to score


class HeldOutTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_judge_sharing_the_arms_backend_is_refused_before_any_call(self) -> None:
        gen = FakeGen()
        with self.assertRaises(PanelConflict) as ctx:
            run_flat_cell(TASK, ("omen-swap", "phi4-vk1"), gen,
                          judges=[("omen-swap", "mistral24b-vk1"), FX99], pool=self.pool)
        self.assertIn("shares backend 'omen-swap'", str(ctx.exception))
        self.assertEqual(gen.calls, [])

    def test_placement_sibling_counts_as_the_same_model(self) -> None:
        gen = FakeGen()
        with self.assertRaises(PanelConflict):
            run_flat_matrix(POUR, gen, task_ids=[TASK],
                            judges=[("some-other-rung", "phi4-vk0"), FX99], pool=self.pool)
        self.assertEqual(gen.calls, [])

    def test_matrix_refuses_the_whole_sweep_up_front(self) -> None:
        gen = FakeGen()
        with self.assertRaises(PanelConflict):
            run_flat_matrix(["omen-arc", "omen-swap:phi4-vk1"], gen, task_ids=[TASK],
                            judges=[("omen-arc", "qwen3-30b-a3b"), FX99], pool=self.pool)
        self.assertEqual(gen.calls, [])

    def test_default_panel_is_derived_from_the_arms(self) -> None:
        gen = FakeGen()
        rows = run_flat_matrix(POUR, gen, task_ids=[TASK], pool=self.pool)
        seats = {(c["backend"], c["model"]) for c in gen.judge_calls}
        self.assertEqual(seats, {FLASH, FX99, ("omen-arc", "qwen3-30b-a3b"),
                                 ("gcp-gemini-pro", "gemini-3.1-pro-preview")})
        self.assertNotIn("omen-swap", {c["backend"] for c in gen.judge_calls})
        self.assertEqual([r["arm"] for r in rows], POUR)

    def test_fallback_pour_drops_the_fx99_seat_that_is_an_arm(self) -> None:
        gen = FakeGen()
        run_flat_matrix(["fx99-ollama:qwen2.5:7b", "fx99-ollama:qwen2.5:14b"], gen,
                        task_ids=[TASK], pool=self.pool)
        self.assertNotIn("fx99-ollama", {c["backend"] for c in gen.judge_calls})
        self.assertEqual({c["model"] for c in gen.arm_calls}, {"qwen2.5:7b", "qwen2.5:14b"})


class IdentityTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_bench_identity_shape(self) -> None:
        ident = bench_identity()
        self.assertEqual(ident, DispatchIdentity(BENCH_CALLER_ID, "local", platform.node()))
        self.assertEqual(BENCH_CALLER_ID, "experiment-doc-adr-bench")

    def test_every_dispatch_runs_under_the_bench_identity_and_it_is_reset(self) -> None:
        gen = FakeGen()
        self.assertIsNone(current_identity())
        run_flat_matrix(POUR, gen, task_ids=[TASK], judges=[FX99, FLASH], pool=self.pool)
        self.assertEqual(len(gen.arm_calls), 2)
        self.assertEqual(len(gen.judge_calls), 4)
        for call in gen.calls:                       # arms AND judges are observations
            ident = call["identity"]
            self.assertIsNotNone(ident, call["backend"])
            self.assertEqual(ident.caller_id, "experiment-doc-adr-bench")
            self.assertEqual(ident.runner_class, "local")
            self.assertEqual(ident.node, platform.node())
        self.assertIsNone(current_identity())       # popped after the sweep

    def test_explicit_identity_is_honoured(self) -> None:
        gen = FakeGen()
        ident = DispatchIdentity("experiment-doc-adr-bench", "local", "lab", task_id="pour-a")
        run_flat_matrix(POUR, gen, task_ids=[TASK], judges=[FX99], pool=self.pool,
                        identity=ident)
        self.assertTrue(all(c["identity"] is ident for c in gen.calls))

    def test_identity_is_reset_even_when_generate_raises(self) -> None:
        def boom(**_kw):
            raise RuntimeError("door down")
        with self.assertRaises(RuntimeError):
            run_flat_matrix(POUR, boom, task_ids=[TASK], judges=[FX99], pool=self.pool)
        self.assertIsNone(current_identity())


class MatrixAndSummaryTests(TestCase):
    def test_two_models_on_one_backend_stay_two_columns(self) -> None:
        gen = FakeGen()
        rows = run_flat_matrix(POUR, gen, task_ids=COMPACT, judges=[FX99, FLASH],
                               pool=_fake_pool())
        self.assertEqual(len(rows), 4)
        self.assertEqual({r["backend"] for r in rows}, {"omen-swap"})
        self.assertEqual({r["model"] for r in rows}, {"phi4-vk1", "qwen14b-vk1"})
        summary = bench_summary(rows)
        self.assertEqual(summary["arms"], POUR)
        self.assertEqual(set(summary["mean_score_by_backend"]), set(POUR))
        self.assertEqual(summary["ok_cells"], 4)
        self.assertEqual(summary["cells"], 4)

    def test_legacy_rows_without_arm_summarise_by_backend(self) -> None:
        rows = [{"backend": "omen-arc", "score": {"mean": 70}, "cost_usd": 0.0,
                 "duration_ms": 100, "ok": True},
                {"backend": "gcp-gemini", "score": {"mean": 90}, "cost_usd": 0.01,
                 "duration_ms": 50, "ok": True}]
        summary = bench_summary(rows)
        self.assertEqual(summary["mean_score_by_backend"],
                         {"gcp-gemini": 90, "omen-arc": 70})
        self.assertEqual(summary["total_cost_usd_by_backend"]["gcp-gemini"], 0.01)

    def test_unknown_task_is_refused_before_any_call(self) -> None:
        gen = FakeGen()
        with self.assertRaises(KeyError):
            run_flat_matrix(POUR, gen, task_ids=["no-such-task"], judges=[FX99],
                            pool=_fake_pool())
        self.assertEqual(gen.calls, [])

    def test_progress_names_the_arm_label(self) -> None:
        seen: list[str] = []
        run_flat_matrix(["omen-swap:phi4-vk1"], FakeGen(), task_ids=[TASK], judges=[FX99],
                        on_progress=seen.append, pool=_fake_pool())
        self.assertEqual(seen, [f"omen-swap:phi4-vk1: {TASK}"])


class PlanTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def test_plan_reports_cells_panel_identity_and_budget_fit(self) -> None:
        plan = plan_flat_matrix(POUR, COMPACT, pool=self.pool)
        self.assertEqual(plan["arms"], POUR)
        self.assertEqual(plan["tasks"], COMPACT)
        self.assertEqual(plan["identity"]["caller_id"], "experiment-doc-adr-bench")
        self.assertEqual(plan["identity"]["node"], platform.node())
        self.assertEqual([tuple(j) for j in plan["judges"]][:2], [FLASH, FX99])
        self.assertIn("omen-arc", plan["panel_note"])            # co-resident, reported
        self.assertEqual(len(plan["cells"]), 4)
        self.assertEqual(plan["planned_calls"], 4 * (1 + len(plan["judges"])))
        for cell in plan["cells"]:
            self.assertEqual(cell["context_bytes"], 14336)
            self.assertEqual(cell["files_missing"], [])
            self.assertTrue(cell["fits"], cell)                 # the compact tasks fit

    def test_the_original_tasks_do_not_fit_the_side_seat_budget(self) -> None:
        plan = plan_flat_matrix(["omen-swap:phi4-vk1"], ["adr-vs-code-fail-closed"],
                                judges=[FX99], pool=self.pool)
        cell = plan["cells"][0]
        self.assertGreater(cell["payload_bytes_est"], 14336)
        self.assertFalse(cell["fits"])
        self.assertIsNone(plan["panel"])                        # explicit panel, not derived

    def test_unlimited_rung_has_no_fit_verdict(self) -> None:
        plan = plan_flat_matrix(["gcp-gemini"], [TASK], judges=[FX99], pool=self.pool)
        self.assertIsNone(plan["cells"][0]["context_bytes"])
        self.assertIsNone(plan["cells"][0]["fits"])

    def test_missing_task_file_is_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            present = os.path.join(root, "a.md")
            with open(present, "w", encoding="utf-8") as f:
                f.write("x" * 100)
            est = task_payload_bytes({"prompt": "p" * 10, "files": ["a.md", "gone.md"]},
                                     repo_root=root)
        self.assertEqual(est["files_missing"], ["gone.md"])
        self.assertEqual(est["payload_bytes_est"], 10 + 100 + len("a.md") + 27)

    def test_plan_refuses_a_self_judging_panel(self) -> None:
        with self.assertRaises(PanelConflict):
            plan_flat_matrix(POUR, [TASK], judges=[("omen-swap", "qwen38-27b-dual")],
                             pool=self.pool)


class CliTests(TestCase):
    def setUp(self) -> None:
        self.pool = _fake_pool()

    def _run(self, argv: list[str], gen: FakeGen | None = None,
             out_root: str | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(run_doc_adr_bench, "local_generate",
                               side_effect=AssertionError("live door touched")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_doc_adr_bench.main(argv, generate=gen, out_root=out_root, pool=self.pool)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_cells_and_judges_and_dispatches_nothing(self) -> None:
        gen = FakeGen()
        rc, out, _ = self._run(["--dry-run", "--arms", *POUR, "--tasks", *COMPACT], gen)
        self.assertEqual(rc, 0)
        self.assertEqual(gen.calls, [])
        self.assertIn("dry-run: nothing dispatched", out)
        self.assertIn("identity: experiment-doc-adr-bench", out)
        for arm in POUR:
            for task in COMPACT:
                self.assertIn(f"{arm}: {task}", out)
        self.assertIn("judges: gcp-gemini/gemini-3.5-flash, fx99-ollama/qwen2.5:14b", out)
        self.assertIn("fits", out)

    def test_dry_run_flags_an_over_budget_cell(self) -> None:
        rc, out, _ = self._run(["--dry-run", "--arms", "omen-swap:phi4-vk1",
                                "--tasks", "adr-vs-code-fail-closed"], FakeGen())
        self.assertEqual(rc, 0)
        self.assertIn("OVER BUDGET", out)

    def test_judge_sharing_an_arms_backend_exits_2_without_dispatching(self) -> None:
        gen = FakeGen()
        rc, _, err = self._run(["--arms", *POUR, "--tasks", TASK,
                                "--judges", "omen-swap:phi4-vk0"], gen)
        self.assertEqual(rc, 2)
        self.assertIn("refused", err)
        self.assertEqual(gen.calls, [])

    def test_judge_without_a_model_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._run(["--dry-run", "--arms", *POUR, "--judges", "fx99-ollama"], FakeGen())
        self.assertEqual(ctx.exception.code, 2)

    def test_sweep_threads_budgets_pushes_identity_and_persists_rows(self) -> None:
        gen = FakeGen()
        with tempfile.TemporaryDirectory() as root:
            rc, out, _ = self._run(["--arms", *POUR, "--tasks", *COMPACT,
                                    "--judges", "fx99-ollama:qwen2.5:14b",
                                    "--max-tokens", "1024", "--timeout-s", "600"],
                                   gen, out_root=root)
            self.assertEqual(rc, 0)
            run_dirs = os.listdir(root)
            self.assertEqual(len(run_dirs), 1)
            run_dir = os.path.join(root, run_dirs[0])
            with open(os.path.join(run_dir, "rows.jsonl"), encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            with open(os.path.join(run_dir, "plan.json"), encoding="utf-8") as f:
                plan = json.load(f)
        self.assertEqual(len(rows), 4)
        self.assertEqual({(r["backend"], r["model"]) for r in rows},
                         {("omen-swap", "phi4-vk1"), ("omen-swap", "qwen14b-vk1")})
        self.assertEqual(plan["identity"]["caller_id"], "experiment-doc-adr-bench")
        for call in gen.arm_calls:
            self.assertEqual(call["max_tokens"], 1024)
            self.assertEqual(call["timeout_s"], 600)
        for call in gen.calls:
            self.assertEqual(call["identity"].caller_id, "experiment-doc-adr-bench")
        self.assertEqual({c["backend"] for c in gen.judge_calls}, {"fx99-ollama"})
        self.assertIn("omen-swap:phi4-vk1", out)
        self.assertIn("omen-swap:qwen14b-vk1", out)

    def test_smoke_takes_the_first_arm_and_one_task(self) -> None:
        gen = FakeGen()
        with tempfile.TemporaryDirectory() as root:
            rc, _, _ = self._run(["--smoke", "--arms", *POUR, "--tasks", *COMPACT,
                                  "--judges", "fx99-ollama:qwen2.5:14b"], gen, out_root=root)
        self.assertEqual(rc, 0)
        self.assertEqual(len(gen.arm_calls), 1)
        self.assertEqual(gen.arm_calls[0]["model"], "phi4-vk1")

    def test_default_sweep_without_a_deliberate_panel_is_refused(self) -> None:
        # gcp-gemini, gcp-gemini-pro and omen-arc as arms leave one held-out seat
        # in the pool -- below min_seats; the operator must name a panel.
        gen = FakeGen()
        rc, _, err = self._run(["--dry-run"], gen)
        self.assertEqual(rc, 2)
        self.assertIn("below min_seats", err)
        self.assertEqual(gen.calls, [])

    def test_backends_flag_is_still_an_alias_for_arms(self) -> None:
        gen = FakeGen()
        rc, out, _ = self._run(["--dry-run", "--backends", "omen-arc", "--tasks", TASK], gen)
        self.assertEqual(rc, 0)
        self.assertIn("arms: omen-arc", out)
        self.assertEqual(gen.calls, [])


class DocstringTests(TestCase):
    def test_module_documents_both_pours(self) -> None:
        doc = doc_adr_bench.__doc__
        self.assertIn("omen-swap:phi4-vk1 omen-swap:qwen14b-vk1", doc)
        self.assertIn("fx99-ollama:qwen2.5:7b fx99-ollama:qwen2.5:14b", doc)
        self.assertIn("14336", doc)                     # the budget that picks the tasks
        for task in COMPACT:
            self.assertIn(task, DOC_ADR_TASKS)
