"""The `/rung` skill (C-04): the two SKILL.md copies checked against their sources.

A skill is prose an agent obeys, so the failure modes are prose failure modes: a
family name that no longer exists, a tool name that was never mounted, a rate
figure quoted from memory, a scheduling call that crept into a recommendation, or
a Codex mirror that quietly drifted from the Claude original. Each of those is
checked here against the source that decides it, never against a copy of the text:

* families      -- ``hearth.scheduler.families.load_families().names()``
* tool names    -- ``hearth.kernel.capabilities.TOOL_CAPABILITY`` (the taxonomy the
                   gateway asserts exhaustive against the ACTUALLY-mounted surface
                   at startup, ``assert_surface_complete``), plus, for every tool
                   the skill names, its provider's own ``get_tools()`` and that
                   provider's presence in the launcher's ``--providers`` list
* call kwargs   -- ``inspect.signature`` of the real tool function
* verdicts      -- ``hearth.toolsurface.rungstate.VERDICTS``
* citations     -- a regex fence: a machine path, a fleet host name, or a
                   ``tok/s`` figure may only appear on a line that cites the claim
                   register

ADR-0008 is the load-bearing one: the skill recommends and never schedules, so the
rotation actuators, the fleet dispatch tool, the applying form of the pet, and the
bare llama-swap unload endpoint must appear ONLY inside the "Never" step.
"""
from __future__ import annotations

import inspect
import re
from importlib import import_module
from pathlib import Path
from unittest import TestCase

import yaml

from hearth.kernel.capabilities import TOOL_CAPABILITY
from hearth.scheduler.families import load_families
from hearth.toolsurface.rungstate import VERDICTS

REPO = Path(__file__).resolve().parents[3]
CLAUDE_SKILL = REPO / ".claude" / "skills" / "rung" / "SKILL.md"
CODEX_DIR = REPO / "docs" / "agents" / "codex-skills" / "rung"
CODEX_SKILL = CODEX_DIR / "SKILL.md"
CODEX_YAML = CODEX_DIR / "agents" / "openai.yaml"
CODEX_README = REPO / "docs" / "agents" / "codex-skills" / "README.md"
LAUNCHER = REPO / "hearth" / "etc" / "start-hearth-gateway.cmd"

CODEX_PREFIX = "mcp__hearth__"
LOOPBACK_URL = "http://127.0.0.1:8710/mcp"

# Every tool either SKILL.md is allowed to name, and the provider that mounts it.
# A tool named in the skill with no entry here fails `test_every_named_tool_is_mapped`
# -- adding a tool to the prose forces naming the provider that actually serves it.
PROVIDER_OF = {
    "recommend_rung": "hearth.toolsurface.rotation",
    "rotation_load": "hearth.toolsurface.rotation",
    "rotation_unload": "hearth.toolsurface.rotation",
    "rotation_window": "hearth.toolsurface.rotation",
    "query_rung_state": "hearth.toolsurface.rungstate",
    "local_generate": "hearth.toolsurface.inference",
    "queue_status": "hearth.toolsurface.task_lane",
    "submit_task": "hearth.toolsurface.task_lane",
    "masters_pet": "hearth.toolsurface.masters_pet",
}

# The skill recommends; it does not schedule (ADR-0008). These may appear only in
# the "Never" step. The last entry is the bare llama-swap unload path, which is not
# a door tool at all -- it is the endpoint that takes production down with it.
FORBIDDEN = ("rotation_load", "rotation_unload", "rotation_window", "submit_task",
             "masters_pet", "/api/models/unload")

_STEP_RE = re.compile(r"^### (\d+)\. (.+?)\s*$", re.M)
_CALL_RE = re.compile(r"\b((?:mcp__hearth__)?[a-z][a-z0-9_]{2,})\(")
_KWARG_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=")
_TASK_FAMILY_RE = re.compile(r"task_family\s*=\s*\"?([A-Za-z_<][\w<>-]*)")
_FAMILY_ROW_RE = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.M)
# Inline code only: fenced blocks are stripped first, so a ``` fence cannot
# throw the backtick pairing off and swallow half the document into one "token".
_FENCE_RE = re.compile(r"^```.*?^```", re.M | re.S)
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# Citation fence. A drive-lettered path, a fleet host name, or a rate figure is only
# citable in the corrected form the register carries, so any line carrying one must
# also carry the register's name. The drive-path pattern deliberately requires the
# letter to stand alone, so a URL scheme ("http://") is not a path.
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
_HOSTNAME_RE = re.compile(r"(?<![\w-])(OMEN|AM4|fx99|i5|oxen)(?![\w-])", re.I)
_TOK_S_RE = re.compile(r"\d[\d.,]*\s*tok/s")
_CITATION_MARK = "CLAIM-REGISTER"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict:
    """Parse the leading `---` block. Returns {} when there is no frontmatter."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    return yaml.safe_load(text[4:end + 1]) or {}


def _frontmatter_lines(text: str, key: str) -> int:
    """How many physical lines the raw frontmatter value for ``key`` spans."""
    end = text.find("\n---\n", 4)
    lines = text[4:end + 1].splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"{key}:"))
    span = 1
    for ln in lines[start + 1:]:
        if re.match(r"^[A-Za-z_][\w-]*:", ln):
            break
        span += 1
    return span


def _steps(text: str) -> list[tuple[str, str, str]]:
    """(number, title, body) for each `### N. Title` step, in document order."""
    marks = list(_STEP_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        nxt = re.search(r"^## ", text[m.end():stop], re.M)
        if nxt is not None:
            stop = m.end() + nxt.start()
        out.append((m.group(1), m.group(2), text[m.end():stop]))
    return out


def _normalize(name: str) -> str:
    return name[len(CODEX_PREFIX):] if name.startswith(CODEX_PREFIX) else name


def _tool_calls(text: str) -> list[str]:
    """Every tool name written in call form, normalized, in document order."""
    return [_normalize(m.group(1)) for m in _CALL_RE.finditer(text)]


def _call_kwargs(text: str) -> list[tuple[str, list[str]]]:
    """(tool, [kwarg names]) for each call-form mention, argument span line-local."""
    out = []
    for m in _CALL_RE.finditer(text):
        line_end = text.find("\n", m.end())
        line_end = len(text) if line_end == -1 else line_end
        span = text[m.end():line_end]
        close = span.rfind(")")
        args = span[:close] if close != -1 else span
        out.append((_normalize(m.group(1)), _KWARG_RE.findall(args)))
    return out


def _inline_code(text: str) -> set[str]:
    """Every inline-code token, fenced blocks removed."""
    return set(_BACKTICK_RE.findall(_FENCE_RE.sub("", text)))


def _families_table(text: str) -> list[str]:
    start = text.index("<!-- families:begin -->")
    end = text.index("<!-- families:end -->")
    return _FAMILY_ROW_RE.findall(text[start:end])


def _never_span(text: str) -> tuple[int, int]:
    steps = _steps(text)
    never = [s for s in steps if s[1].strip().lower() == "never"]
    assert len(never) == 1, "exactly one step must be the Never step"
    body = never[0][2]
    start = text.index(body)
    return start, start + len(body)


SKILLS = {"claude": CLAUDE_SKILL, "codex": CODEX_SKILL}


class FrontmatterTests(TestCase):
    def test_both_files_exist(self) -> None:
        for path in (CLAUDE_SKILL, CODEX_SKILL, CODEX_YAML, CODEX_README):
            self.assertTrue(path.is_file(), f"missing: {path}")

    def test_frontmatter_parses_and_names_the_skill(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                fm = _frontmatter(_read(path))
                self.assertEqual(fm.get("name"), "rung")
                self.assertTrue(str(fm.get("description", "")).strip())

    def test_description_is_at_most_two_lines(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                self.assertLessEqual(_frontmatter_lines(_read(path), "description"), 2)

    def test_description_carries_the_trigger_phrases(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                desc = str(_frontmatter(_read(path))["description"]).lower()
                for phrase in ("which rung", "what should run this",
                               "rung for this task", "/rung"):
                    self.assertIn(phrase, desc)


class FamilyTests(TestCase):
    """Source of truth: hearth.scheduler.families.load_families().names()."""

    def setUp(self) -> None:
        self.names = set(load_families().names())

    def test_table_matches_the_declaration_exactly(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                listed = _families_table(_read(path))
                self.assertEqual(len(listed), len(set(listed)), "duplicate row")
                self.assertEqual(set(listed), self.names)
                self.assertEqual(len(listed), len(self.names))

    def test_no_stale_family_named_in_a_task_family_argument(self) -> None:
        placeholders = {"<name>", "<family>"}
        for label, path in SKILLS.items():
            with self.subTest(label):
                for value in _TASK_FAMILY_RE.findall(_read(path)):
                    if value in placeholders:
                        continue
                    self.assertIn(value, self.names, f"{value} is not a family")

    def test_both_copies_list_the_same_families(self) -> None:
        self.assertEqual(_families_table(_read(CLAUDE_SKILL)),
                         _families_table(_read(CODEX_SKILL)))


class ToolSurfaceTests(TestCase):
    """Every tool named must exist on the mounted surface, with real parameters."""

    def test_every_named_tool_is_mapped(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                for tool in set(_tool_calls(_read(path))):
                    self.assertIn(tool, PROVIDER_OF, f"unmapped tool {tool!r}")

    def test_named_tools_are_in_the_capability_taxonomy(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                for tool in set(_tool_calls(_read(path))):
                    self.assertIn(tool, TOOL_CAPABILITY, f"{tool} is not a mounted tool")

    def test_named_tools_are_exported_by_a_launched_provider(self) -> None:
        providers = _read(LAUNCHER).split("--providers")[1].split()[0].split(",")
        named = set(_tool_calls(_read(CLAUDE_SKILL))) | set(_tool_calls(_read(CODEX_SKILL)))
        for tool in sorted(named):
            with self.subTest(tool):
                module_name = PROVIDER_OF[tool]
                self.assertIn(module_name, providers, "provider is not launched")
                exported = [t.__name__ for t in import_module(module_name).get_tools()]
                self.assertIn(tool, exported)

    def test_emitted_calls_use_real_parameters(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                for tool, kwargs in _call_kwargs(_read(path)):
                    fn = getattr(import_module(PROVIDER_OF[tool]), tool)
                    params = inspect.signature(fn).parameters
                    for kwarg in kwargs:
                        self.assertIn(kwarg, params, f"{tool}({kwarg}=...)")

    def test_the_driven_tools_are_all_named(self) -> None:
        for tool in ("recommend_rung", "query_rung_state", "local_generate"):
            for label, path in SKILLS.items():
                with self.subTest(f"{label}:{tool}"):
                    self.assertIn(tool, _tool_calls(_read(path)))


class VerdictTests(TestCase):
    """Source of truth: hearth.toolsurface.rungstate.VERDICTS."""

    def test_decision_table_covers_every_verdict(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                quoted = _inline_code(_read(path))
                for verdict in VERDICTS:
                    self.assertIn(verdict, quoted, f"no guidance for {verdict!r}")


class SchedulingAuthorityTests(TestCase):
    """ADR-0008: the skill recommends. Scheduling appears only under 'Never'."""

    def test_forbidden_tools_appear_only_in_the_never_step(self) -> None:
        for label, path in SKILLS.items():
            text = _read(path)
            lo, hi = _never_span(text)
            for token in FORBIDDEN:
                with self.subTest(f"{label}:{token}"):
                    hits = [m.start() for m in re.finditer(re.escape(token), text)]
                    self.assertTrue(hits, f"{token} is never mentioned")
                    for pos in hits:
                        self.assertTrue(lo <= pos < hi,
                                        f"{token} outside the Never step at {pos}")

    def test_never_step_states_the_authority_rule(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                text = _read(path)
                lo, hi = _never_span(text)
                self.assertIn("ADR-0008", text[lo:hi])


class CitationDisciplineTests(TestCase):
    """A path, a host name, or a rate may only ride on a claim-register citation."""

    FILES = (CLAUDE_SKILL, CODEX_SKILL, CODEX_YAML, CODEX_README)

    def _offenders(self, path: Path, pattern: re.Pattern) -> list[str]:
        return [ln for ln in _read(path).splitlines()
                if pattern.search(ln) and _CITATION_MARK not in ln]

    def test_no_machine_path_outside_a_citation(self) -> None:
        for path in self.FILES:
            with self.subTest(path.name):
                self.assertEqual(self._offenders(path, _DRIVE_PATH_RE), [])

    def test_no_fleet_host_name_outside_a_citation(self) -> None:
        for path in self.FILES:
            with self.subTest(path.name):
                self.assertEqual(self._offenders(path, _HOSTNAME_RE), [])

    def test_no_rate_figure_outside_a_citation(self) -> None:
        for path in self.FILES:
            with self.subTest(path.name):
                self.assertEqual(self._offenders(path, _TOK_S_RE), [])

    def test_the_register_is_actually_cited(self) -> None:
        for label, path in SKILLS.items():
            with self.subTest(label):
                self.assertIn("docs/CLAIM-REGISTER.md", _read(path))


class CodexYamlTests(TestCase):
    def setUp(self) -> None:
        self.doc = yaml.safe_load(_read(CODEX_YAML))

    def test_interface_block(self) -> None:
        interface = self.doc["interface"]
        self.assertEqual(interface["display_name"], "Rung")
        self.assertEqual(interface["short_description"],
                         "Pick the rung and model for a task from authored evidence")
        self.assertEqual(
            interface["default_prompt"],
            "Use $rung to choose the rung and model for this task before offloading.")

    def test_mcp_dependency_is_loopback(self) -> None:
        tool = self.doc["dependencies"]["tools"][0]
        self.assertEqual(tool["type"], "mcp")
        self.assertEqual(tool["value"], "hearth")
        self.assertEqual(tool["transport"], "streamable_http")
        self.assertEqual(tool["url"], LOOPBACK_URL)

    def test_invocation_is_explicit_only(self) -> None:
        self.assertIs(self.doc["policy"]["allow_implicit_invocation"], False)


class MirrorParityTests(TestCase):
    """The two copies may differ in tool naming and in nothing else."""

    def setUp(self) -> None:
        self.claude = _steps(_read(CLAUDE_SKILL))
        self.codex = _steps(_read(CODEX_SKILL))

    def test_same_step_sequence(self) -> None:
        self.assertTrue(self.claude, "no steps found")
        self.assertEqual([(n, t) for n, t, _ in self.claude],
                         [(n, t) for n, t, _ in self.codex])

    def test_steps_are_numbered_from_one(self) -> None:
        self.assertEqual([n for n, _, _ in self.claude],
                         [str(i) for i in range(1, len(self.claude) + 1)])

    def test_same_tools_in_the_same_order_per_step(self) -> None:
        for (num, title, a), (_, _, b) in zip(self.claude, self.codex):
            with self.subTest(f"{num}. {title}"):
                self.assertEqual(_tool_calls(a), _tool_calls(b))

    def test_codex_copy_prefixes_every_tool_mention(self) -> None:
        raw = [m.group(1) for m in _CALL_RE.finditer(_read(CODEX_SKILL))]
        self.assertTrue(raw)
        for name in raw:
            self.assertTrue(name.startswith(CODEX_PREFIX), name)

    def test_claude_copy_never_uses_the_codex_prefix(self) -> None:
        self.assertNotIn(CODEX_PREFIX, _read(CLAUDE_SKILL))


class FenceSanityTests(TestCase):
    """A pattern that matches nothing passes every file trivially. These assert the
    fences bite on known offenders and stay off the things they must not catch."""

    def test_drive_path_pattern(self) -> None:
        self.assertTrue(_DRIVE_PATH_RE.search(r"receipts under E:\work\battlemage"))
        self.assertTrue(_DRIVE_PATH_RE.search("cd C:/work/commandcenter"))
        self.assertIsNone(_DRIVE_PATH_RE.search(LOOPBACK_URL))

    def test_hostname_pattern_spares_rung_names(self) -> None:
        self.assertTrue(_HOSTNAME_RE.search("resident on OMEN"))
        self.assertTrue(_HOSTNAME_RE.search("the AM4 services host"))
        self.assertIsNone(_HOSTNAME_RE.search("omen-arc is the door default"))
        self.assertIsNone(_HOSTNAME_RE.search("pin omen-arc-oss with cause"))

    def test_rate_pattern(self) -> None:
        self.assertTrue(_TOK_S_RE.search("about 108 tok/s single-stream"))
        self.assertTrue(_TOK_S_RE.search("104.83 tok/s"))
        self.assertIsNone(_TOK_S_RE.search("8,192 prompt tokens"))

    def test_call_pattern_finds_and_normalizes(self) -> None:
        sample = "`mcp__hearth__recommend_rung(task_family=x)` then `local_generate(p=1)`"
        self.assertEqual(_tool_calls(sample), ["recommend_rung", "local_generate"])

    def test_inline_code_is_not_confused_by_fences(self) -> None:
        sample = "a `one` then\n```\nfenced `text` here\n```\nand `two`\n"
        self.assertEqual(_inline_code(sample), {"one", "two"})


class ReadmeTests(TestCase):
    def test_readme_names_both_install_targets(self) -> None:
        # Path separators are normalized so a Windows-shaped example still reads.
        text = _read(CODEX_README).replace("\\", "/")
        for token in (".codex/skills/rung", ".claude/skills/rung",
                      "docs/agents/codex-skills/rung"):
            self.assertIn(token, text)

    def test_readme_says_the_tracked_copy_is_the_source(self) -> None:
        self.assertIn("tracked copy here is the source", _read(CODEX_README))

    def test_readme_names_the_loopback_url(self) -> None:
        self.assertIn(LOOPBACK_URL, _read(CODEX_README))
