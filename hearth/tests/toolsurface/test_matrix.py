from __future__ import annotations

from unittest import TestCase

from hearth.experiments import matrix
from hearth.experiments.matrix import Cell, Role, build_pilot_cells, run_cell, run_matrix


class FakeGen:
    """Routes by prompt role, incl. the judge SCORE prompt. Records backends seen."""

    def __init__(self, score: int = 77, verdict: str = "CONVERGED") -> None:
        self.score = score
        self.verdict = verdict
        self.calls: list[tuple] = []

    def __call__(self, prompt, model=None, backend=None, system=None,
                 max_tokens=None, timeout_s=None):
        self.calls.append({"model": model, "backend": backend, "prompt": prompt,
                           "system": system})
        if "Output ONLY a final line" in prompt:            # judge
            return {"ok": True, "text": f"ok\nSCORE: {self.score}", "model": model}
        if "Write the full proposal now" in prompt:
            return {"ok": True, "text": "DRAFT", "model": model,
                    "tokens_out": 10, "duration_ms": 5}
        if "Output only the revised proposal" in prompt:
            return {"ok": True, "text": "DRAFT2", "model": model,
                    "tokens_out": 10, "duration_ms": 5}
        if "End your review" in prompt:
            return {"ok": True, "text": f"c\nVERDICT: {self.verdict}", "model": model,
                    "tokens_out": 8, "duration_ms": 4}
        raise AssertionError(f"unexpected prompt: {prompt[:50]!r}")


AM4 = [("am4-oxen", "oxen-planner"), ("am4-oxen", "oxen-critic")]
OMEN = (None, "qwen3-coder:30b")


class BuildCellsTests(TestCase):
    def test_pilot_grid_count_and_shape(self) -> None:
        # pin prompt_ids so the count is independent of how many PROMPTS exist
        three = ["choose-next-agent", "escalate-or-not", "plan-skeleton"]
        cells = build_pilot_cells(AM4, OMEN, prompt_ids=three, laps=(1, 3))
        # 2 am4 models x 3 prompts x 2 laps x 2 orderings = 24
        self.assertEqual(len(cells), 24)
        orderings = {c.ordering for c in cells}
        self.assertEqual(orderings, {"am4->omen", "omen->am4"})
        # every cell has one am4 role and one omen role
        for c in cells:
            nodes = {c.planner.node, c.critic.node}
            self.assertEqual(nodes, {"am4", "omen"})

    def test_node_derived_from_backend(self) -> None:
        cells = build_pilot_cells(AM4, OMEN, prompt_ids=["plan-skeleton"], laps=(1,))
        for c in cells:
            am4_role = c.planner if c.planner.node == "am4" else c.critic
            omen_role = c.critic if c.planner.node == "am4" else c.planner
            self.assertEqual(am4_role.backend, "am4-oxen")
            self.assertIsNone(omen_role.backend)


class RunCellTests(TestCase):
    def _cell(self) -> Cell:
        return Cell("c1", "plan-skeleton",
                    Role("am4", "am4-oxen", "oxen-planner"),
                    Role("omen", None, "qwen3-coder:30b"), laps=1, ordering="am4->omen")

    def test_run_cell_row_and_score(self) -> None:
        gen = FakeGen(score=77)
        row = run_cell(self._cell(), generate=gen)
        self.assertTrue(row["ok"])
        self.assertEqual(row["score"]["mean"], 77)
        self.assertEqual(row["planner"]["model"], "oxen-planner")
        self.assertEqual(row["critic"]["model"], "qwen3-coder:30b")
        self.assertEqual(row["final"], "DRAFT")             # converged round 1

    def test_backends_routed_per_role(self) -> None:
        gen = FakeGen()
        run_cell(self._cell(), generate=gen)
        # author (expand) call went to am4-oxen; critic review went to omen (None)
        expand = next(c for c in gen.calls if "Write the full proposal now" in c["prompt"])
        review = next(c for c in gen.calls if "End your review" in c["prompt"])
        self.assertEqual(expand["backend"], "am4-oxen")
        self.assertEqual(expand["model"], "oxen-planner")
        self.assertIsNone(review["backend"])
        self.assertEqual(review["model"], "qwen3-coder:30b")

    def test_score_uses_held_out_judge_backend(self) -> None:
        gen = FakeGen()
        run_cell(self._cell(), generate=gen, judges=[("am4-oxen", "oxen-critic")])
        judge = next(c for c in gen.calls if "Output ONLY a final line" in c["prompt"])
        self.assertEqual(judge["backend"], "am4-oxen")
        self.assertEqual(judge["model"], "oxen-critic")

    def test_matrix_and_summary(self) -> None:
        cells = build_pilot_cells(AM4, OMEN, prompt_ids=["plan-skeleton"], laps=(1,))
        rows = run_matrix(cells, generate=FakeGen(score=80))
        self.assertEqual(len(rows), 4)                       # 2 models x 1 prompt x 1 lap x 2 ord
        summary = matrix.dataset_summary(rows)
        self.assertEqual(summary["cells"], 4)
        self.assertEqual(summary["ok_cells"], 4)
        self.assertEqual(summary["mean_score_by_prompt"]["plan-skeleton"], 80)

    def test_variant_cells_thread_system_prompts(self) -> None:
        from hearth.experiments.matrix import build_variant_cells
        cfgs = [{"name": "min", "author_system": "WRITE SHORT", "critic_system": "BE MINIMAL"}]
        cells = build_variant_cells(cfgs, prompt_ids=["plan-skeleton"], laps=(1,), repeats=1)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].variant, "min")
        gen = FakeGen()
        row = run_cell(cells[0], generate=gen)
        self.assertEqual(row["variant"], "min")
        # the variant's system prompts reach the model calls
        expand = next(c for c in gen.calls if "Write the full proposal now" in c["prompt"])
        review = next(c for c in gen.calls if "End your review" in c["prompt"])
        self.assertEqual(expand["system"], "WRITE SHORT")
        self.assertEqual(review["system"], "BE MINIMAL")

    def test_variant_laps_in_summary(self) -> None:
        from hearth.experiments.matrix import build_variant_cells
        cfgs = [{"name": "a", "critic_system": None}, {"name": "b", "critic_system": "X"}]
        cells = build_variant_cells(cfgs, prompt_ids=["plan-skeleton"], laps=(1, 2), repeats=1)
        rows = run_matrix(cells, generate=FakeGen(score=70))
        summ = matrix.dataset_summary(rows)
        self.assertIn("a|L1", summ["mean_score_by_variant_laps"])
        self.assertIn("b|L2", summ["mean_score_by_variant_laps"])
        self.assertEqual(summ["mean_score_by_variant"]["a"], 70)

    def test_score_parse_clamps_and_picks_last(self) -> None:
        self.assertEqual(matrix._parse_score("SCORE: 150"), 100)
        self.assertEqual(matrix._parse_score("SCORE: 40\nSCORE: 88"), 88)
        self.assertIsNone(matrix._parse_score("no score here"))
