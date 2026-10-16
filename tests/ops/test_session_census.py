"""Tests for tools/ops/session_census.py, on fixture transcripts in tmp dirs."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "session_census", Path(__file__).resolve().parents[2] / "tools" / "ops" / "session_census.py"
)
sc = importlib.util.module_from_spec(_SPEC)
sys.modules["session_census"] = sc
_SPEC.loader.exec_module(sc)

TODAY = dt.date(2026, 10, 15)
DAY = "2026-10-14"


def sid(prefix: str) -> str:
    """A UUID whose first 8 chars are ``prefix``."""
    assert len(prefix) == 8
    return f"{prefix}-1111-4222-8333-444444444444"


def write_claude(root: Path, session_id: str, prompts: int, ts="2026-10-14T10:00:00.000Z", *, sub=False, name=None, tool_results=0):
    d = root / "-home-derek-proj"
    if sub:
        d = d / session_id / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "file-history-snapshot"}] if ts else []
    for i in range(prompts):
        lines.append(
            {
                "type": "user",
                "timestamp": ts,
                "sessionId": session_id,
                "message": {"role": "user", "content": f"prompt {i}"},
            }
        )
        lines.append({"type": "assistant", "sessionId": session_id, "message": {"role": "assistant", "content": "ok"}})
    for _ in range(tool_results):
        lines.append(
            {
                "type": "user",
                "sessionId": session_id,
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x"}]},
            }
        )
    p = d / (name or f"{session_id}.jsonl")
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


def write_codex(root: Path, day: str, name: str, user_texts, *, messages=True):
    y, m, d = day.split("-")
    dd = root / y / m / d
    dd.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "session_meta", "payload": {"id": name}}]
    if messages:
        lines.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /x\n<INSTRUCTIONS>"}],
                },
            }
        )
        for t in user_texts:
            lines.append(
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": t}]}}
            )
    p = dd / f"rollout-{day}T01-02-03-{name}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


@pytest.fixture
def env(tmp_path):
    e = {
        "claude_dir": tmp_path / "claude",
        "codex_dir": tmp_path / "codex",
        "offload": tmp_path / "offload.json",
        "events": tmp_path / "events.ndjson",
        "amux_sessions": tmp_path / "amux",
        "rd_ids": tmp_path / "rd.json",
    }
    e["claude_dir"].mkdir()
    e["codex_dir"].mkdir()
    e["amux_sessions"].mkdir()
    e["offload"].write_text(json.dumps({"by_task": [], "by_task_truncated": False}))
    e["events"].write_text("")
    e["rd_ids"].write_text(json.dumps({"_note": "x", "task_ids": []}))
    return e


def census(env, **kw):
    return sc.build_census(today=TODAY, **env, **kw)


def day_row(c, day=DAY):
    return next(r for r in c["per_day"] if r["day"] == day)


def test_window_is_14_days_ending_today(env):
    c = census(env)
    assert [r["day"] for r in c["per_day"]][0] == "2026-10-02"
    assert c["per_day"][-1]["day"] == "2026-10-15"
    assert len(c["per_day"]) == 14
    assert c["schema"] == "session-coverage.v1"
    assert c["unobservable"] == ["gemini"]


def test_eligibility_needs_three_prompts(env):
    write_claude(env["claude_dir"], sid("aaaaaaa1"), 2)
    write_claude(env["claude_dir"], sid("aaaaaaa2"), 3)
    r = day_row(census(env))
    assert r["claude_top_level"] == 2
    assert r["claude_eligible"] == 1


def test_tool_results_are_not_prompts(env):
    # 1 real prompt + 5 tool_result echoes must stay below the threshold
    write_claude(env["claude_dir"], sid("bbbbbbb1"), 1, tool_results=5)
    assert day_row(census(env))["claude_eligible"] == 0


def test_subagents_and_non_uuid_names_excluded(env):
    write_claude(env["claude_dir"], sid("ccccccc1"), 4)
    write_claude(env["claude_dir"], sid("ccccccc2"), 4, sub=True)
    write_claude(env["claude_dir"], sid("ccccccc3"), 4, name="agent-notauuid.jsonl")
    r = day_row(census(env))
    assert r["claude_top_level"] == 1
    assert r["claude_eligible"] == 1


def test_day_falls_back_to_mtime_without_timestamp(env):
    import os

    p = write_claude(env["claude_dir"], sid("ddddddd1"), 3, ts=None)
    # strip timestamps entirely: rewrite lines without the field
    lines = [json.loads(x) for x in p.read_text().splitlines()]
    for rec in lines:
        rec.pop("timestamp", None)
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    t = dt.datetime(2026, 10, 13, 12, tzinfo=dt.timezone.utc).timestamp()
    os.utime(p, (t, t))
    r = day_row(census(env), "2026-10-13")
    assert r["claude_eligible"] == 1


def test_sessions_outside_window_ignored(env):
    write_claude(env["claude_dir"], sid("eeeeeee1"), 5, ts="2026-09-01T00:00:00Z")
    assert sum(r["claude_top_level"] for r in census(env)["per_day"]) == 0


def test_rd_exclusion_list(env):
    write_claude(env["claude_dir"], sid("9568f28c"), 10)
    write_claude(env["claude_dir"], sid("fffffff1"), 10)
    env["rd_ids"].write_text(json.dumps(["cc-9568f28c"]))  # plain-list form also accepted
    r = day_row(census(env))
    assert r["claude_top_level"] == 2
    assert r["claude_excluded_rd"] == 1
    assert r["claude_eligible"] == 1


def test_rd_ids_object_form(env):
    env["rd_ids"].write_text(json.dumps({"_note": "n", "task_ids": ["cc-9568f28c"]}))
    assert sc.load_rd_ids(env["rd_ids"]) == {"cc-9568f28c"}


def test_join_hearth_by_task_and_events(env):
    write_claude(env["claude_dir"], sid("11111111"), 3)  # in offload by_task
    write_claude(env["claude_dir"], sid("22222222"), 3)  # only in an execution event
    write_claude(env["claude_dir"], sid("33333333"), 3)  # neither
    env["offload"].write_text(json.dumps({"by_task": [{"key": "cc-11111111", "calls": 3}, {"key": "w-abc"}]}))
    env["events"].write_text(
        json.dumps({"event_type": "x", "desired": {"arguments": {"task_id": "cc-22222222"}}})
        + "\n"
        + json.dumps({"task_id": 12345})  # non-string ignored
        + "\nnot json {\n"
    )
    c = census(env)
    r = day_row(c)
    assert r["claude_eligible"] == 3
    assert r["used_hearth"] == {"count": 2, "of_eligible": 3, "share": 0.6667}
    assert c["totals"]["used_hearth"]["count"] == 2


def test_hearth_requires_eligible(env):
    # an ineligible session that used HEARTH must not raise the numerator
    write_claude(env["claude_dir"], sid("44444444"), 1)
    env["offload"].write_text(json.dumps({"by_task": [{"key": "cc-44444444"}]}))
    r = day_row(census(env))
    assert r["used_hearth"] == {"count": 0, "of_eligible": 0, "share": None}


def test_join_amux_lane(env):
    write_claude(env["claude_dir"], sid("55555555"), 3)
    write_claude(env["claude_dir"], sid("66666666"), 3)
    (env["amux_sessions"] / "lane-a.meta.json").write_text(
        json.dumps({"cc_conversation_id": "55555555-aaaa-bbbb-cccc-dddddddddddd"})
    )
    (env["amux_sessions"] / "lane-b.meta.json").write_text(json.dumps({"other": 1}))
    (env["amux_sessions"] / "lane-c.meta.json").write_text("{bad")
    r = day_row(census(env))
    assert r["in_lane"] == {"count": 1, "of_eligible": 2, "share": 0.5}


def test_shares_keep_their_own_denominators_no_product(env):
    write_claude(env["claude_dir"], sid("77777777"), 3)
    env["offload"].write_text(json.dumps({"by_task": [{"key": "cc-77777777"}]}))
    c = census(env)
    t = c["totals"]
    assert set(t["used_hearth"]) == {"count", "of_eligible", "share"}
    assert not any("product" in k or "both" in k for k in t)


def test_codex_rollouts_day_from_path_and_prompts(env):
    write_codex(env["codex_dir"], DAY, "a", ["one", "two", "three"])
    write_codex(env["codex_dir"], DAY, "b", ["one"])
    r = day_row(census(env))
    assert r["codex_rollouts"] == 2
    assert r["codex_eligible"] == 1  # injected AGENTS.md message is not a prompt


def test_codex_unknown_prompts_gives_null(env):
    write_codex(env["codex_dir"], DAY, "c", [], messages=False)
    c = census(env)
    r = day_row(c)
    assert r["codex_rollouts"] == 1
    assert r["codex_eligible"] is None
    assert c["totals"]["codex_prompts_unknown"] == 1


def test_markdown_table_and_unobservable(env):
    write_claude(env["claude_dir"], sid("88888888"), 3)
    out = sc.render_markdown(census(env))
    assert "| 2026-10-14 |" in out
    assert "unobservable: gemini" in out
    assert "0/1 (0%)" in out


def test_main_writes_json(env, tmp_path, capsys):
    out = tmp_path / "out" / "session-coverage.json"
    argv = ["--today", "2026-10-15", "--out", str(out)]
    for k, v in env.items():
        argv += ["--" + k.replace("_", "-"), str(v)]
    assert sc.main(argv) == 0
    data = json.loads(out.read_text())
    assert data["schema"] == "session-coverage.v1"
    assert "method" in data
    assert "Session census" in capsys.readouterr().out
