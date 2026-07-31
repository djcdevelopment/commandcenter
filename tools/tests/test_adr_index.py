from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.adr_index import build_index, find_registers, main, render_markdown


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class AdrIndexTests(TestCase):
    """The index exists because bare ADR numbers are not resolvable references here:
    `0001` names eleven different decisions across eleven registers. It must be additive
    (renames nothing), must find all four live naming conventions, and must be stable for
    an unchanged tree so `--check` means 'content drifted', not 'time passed'."""

    def _tree(self, root: Path) -> None:
        write(root / "repo-a" / "docs" / "adr" / "0001-first.md",
              "# ADR-0001 First decision\n\n**Status:** Accepted (2026-01-01)\n")
        write(root / "repo-a" / "docs" / "adr" / "0013-thirteen.md",
              "# Thirteen in repo A\n\nStatus: Superseded\n")
        # A different register reusing the same numbers -- the whole problem.
        write(root / "repo-b" / "docs" / "adrs" / "0001-also-first.md",
              "# Also numbered one\n\nStatus: Accepted\n")
        write(root / "repo-b" / "docs" / "adrs" / "0013-also-thirteen.md",
              "# Also thirteen\n\nStatus: Proposed\n")
        # The other two conventions.
        write(root / "repo-c" / "docs" / "decisions" / "pd-1-governance.md",
              "# PD-1 Governance\n\nStatus: Open\n")
        write(root / "repo-d" / "docs" / "decisions" / "ADR-001-vendored.md",
              "# ADR-001 Prefixed form\n\nStatus: Accepted\n")
        # Must be ignored: README, worktree mirror, vendored upstream, node_modules.
        write(root / "repo-a" / "docs" / "adr" / "README.md", "# Index\n")
        write(root / "repo-a" / ".claude" / "worktrees" / "wt" / "docs" / "adr" / "0001-first.md",
              "# ADR-0001 First decision\n")
        write(root / "repo-e" / "upstream" / "vendor" / "docs" / "decisions" / "0001-theirs.md",
              "# Vendored\n")
        write(root / "repo-a" / "node_modules" / "pkg" / "docs" / "adr" / "0001-dep.md", "# Dep\n")

    def test_finds_every_first_party_register_and_excludes_the_rest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            registers = {p.relative_to(root).as_posix() for p in find_registers(root)}
            self.assertEqual(registers, {
                "repo-a/docs/adr", "repo-b/docs/adrs",
                "repo-c/docs/decisions", "repo-d/docs/decisions",
            })

    def test_reports_collisions_with_fully_qualified_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            index = build_index(root)
            self.assertEqual(index["record_count"], 6)
            self.assertEqual(index["collisions"]["1"],
                             ["repo-a/docs/adr#1", "repo-b/docs/adrs#1", "repo-c/docs/decisions#1",
                              "repo-d/docs/decisions#1"])
            self.assertEqual(index["collisions"]["13"],
                             ["repo-a/docs/adr#13", "repo-b/docs/adrs#13"])

    def test_recognizes_all_four_naming_conventions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            index = build_index(root)
            self.assertEqual(
                {r["convention"] for r in index["records"]},
                {"numbered", "pd-prefixed", "adr-prefixed"},
            )

    def test_detects_byte_identical_stale_copies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            write(root / "repo-f" / "docs" / "adr" / "0001-first.md",
                  "# ADR-0001 First decision\n\n**Status:** Accepted (2026-01-01)\n")
            index = build_index(root)
            duplicated = [keys for keys in index["identical_content"].values()
                          if "repo-f/docs/adr#1" in keys]
            self.assertEqual(len(duplicated), 1)
            self.assertIn("repo-a/docs/adr#1", duplicated[0])

    def test_is_byte_stable_for_an_unchanged_tree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            first = json.dumps(build_index(root), indent=2)
            second = json.dumps(build_index(root), indent=2)
            self.assertEqual(first, second, "index must not embed a wall clock")

    def test_check_mode_detects_drift(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            out_json, out_md = root / "out.json", root / "out.md"
            argv = ["--root", str(root), "--out-json", str(out_json), "--out-md", str(out_md)]
            self.assertEqual(main(argv), 0)
            self.assertEqual(main(argv + ["--check"]), 0)

            write(root / "repo-a" / "docs" / "adr" / "0099-new.md", "# New\n\nStatus: Draft\n")
            self.assertEqual(main(argv + ["--check"]), 1)

    def test_flags_encoding_damage_rather_than_hiding_it(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            broken = root / "repo-a" / "docs" / "adr" / "0042-broken.md"
            broken.parent.mkdir(parents=True, exist_ok=True)
            # Raw 0x97 is a CP1252 em-dash and invalid UTF-8 -- the mojibake tell for a
            # programmatic write through a mis-encoding path.
            broken.write_bytes(b"# Title \x97 broken\n\nStatus: Accepted\n")
            index = build_index(root)
            record = next(r for r in index["records"] if r["number"] == "42")
            self.assertTrue(record["encoding_damaged"])
            self.assertNotIn("�", record["title"] or "")
            self.assertEqual(index["encoding_damaged_count"], 1)

    def test_markdown_renders_registers_and_records(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._tree(root)
            markdown = render_markdown(build_index(root))
            self.assertIn("# ADR Index", markdown)
            self.assertIn("`repo-a/docs/adr#13`", markdown)
            self.assertIn("Colliding numbers", markdown)
            self.assertIn("**none**", markdown)  # a register with no README
