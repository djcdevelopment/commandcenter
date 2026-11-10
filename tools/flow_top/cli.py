#!/usr/bin/env python3
"""CLI & Interactive Terminal Controller for `flow-top` (`tools/flow_top/cli.py`).

Exposes `main(argv: Optional[List[str]] = None) -> int`.

Features:
  - Mirrors `am4-top` flags (`--render`, `--once`, `--color`, `--no-color`, `--tty`,
    `--view`, `--fixture`, `--list-fixtures`, `--width`, `--dump-json`, `--interval`)
  - Task selection (`--task <index_or_id>`) by 1-based index (`1..10`) or `task_id` substring
  - Optional receipts directory override (`--runs-dir <path>`)
  - Fault-isolation verification (`--simulate-unavailable <panel[,panel...]>`)
  - Non-blocking interactive loop (`o`/`p`/`s`/`c`/`d` for views, `1`..`9`/`0` and `j`/`k`/`n`
    to switch selected task and redraw from memory without re-polling, `r` to force poll, `q` to quit)
"""

from __future__ import annotations

import argparse
import inspect
import os
from pathlib import Path
import select
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from .contract import VALID_FLOW_VIEWS, FlowSnapshot, load_flow_snapshot_cache
from .renderer import render_flow_snapshot

KEY_TO_VIEW = {
    "o": "overview",
    "p": "path",
    "s": "services",
    "c": "caches",
    "d": "data",
}


def _load_fixture_names() -> List[str]:
    from . import fixtures

    if hasattr(fixtures, "FIXTURE_NAMES"):
        return list(getattr(fixtures, "FIXTURE_NAMES"))
    if hasattr(fixtures, "list_fixtures"):
        return list(fixtures.list_fixtures())
    if hasattr(fixtures, "FIXTURES"):
        return list(getattr(fixtures, "FIXTURES").keys())
    return []


def _load_fixture_snapshot(name: str) -> FlowSnapshot:
    from . import fixtures

    for fn_name in ("get_fixture_snapshot", "get_flow_fixture", "get_fixture", "build_fixture"):
        if hasattr(fixtures, fn_name):
            return getattr(fixtures, fn_name)(name)
    if hasattr(fixtures, "FIXTURES") and name in getattr(fixtures, "FIXTURES"):
        val = getattr(fixtures, "FIXTURES")[name]
        return val() if callable(val) else val
    raise KeyError(f"Unknown fixture: {name!r}")


def _parse_simulated_unavailable(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _apply_task_selection(snap: FlowSnapshot, task_selector: Optional[str]) -> None:
    if not task_selector or not snap.tasks:
        return
    sel = task_selector.strip()
    if not sel:
        return
    if sel.isdigit():
        num = int(sel)
        if num == 0:
            idx = min(9, len(snap.tasks) - 1)
        else:
            idx = max(0, min(num - 1, len(snap.tasks) - 1))
        snap.selected_task_index = idx
        return

    sel_low = sel.lower()
    for i, t in enumerate(snap.tasks):
        if t.task_id.lower() == sel_low:
            snap.selected_task_index = i
            return
    for i, t in enumerate(snap.tasks):
        if sel_low in t.task_id.lower() or sel_low in (t.title or "").lower():
            snap.selected_task_index = i
            return


def _apply_simulated_unavailable(snap: FlowSnapshot, sim_list: List[str]) -> None:
    if not sim_list:
        return
    for item in sim_list:
        if item not in snap.simulated_unavailable:
            snap.simulated_unavailable.append(item)

    for panel_name in sim_list:
        p = panel_name.strip().lower()
        if p in ("service_bus", "broker", "servicebus"):
            snap.service_bus.state = "unavailable"
            snap.service_bus.explorer_online = False
            snap.service_bus.emulator_amqp_online = False
            snap.service_bus.error_note = (
                "SIMULATED UNAVAILABLE: Service Bus :5672 / Explorer :18080 unreachable (fault-isolated)"
            )
            for s in snap.services:
                if "service_bus" in s.service_id.lower() or "broker" in s.service_id.lower() or "5672" in s.host_endpoint:
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"
        elif p == "ontology":
            snap.ontology.state = "unavailable"
            snap.ontology.omen_proxy_online = False
            snap.ontology.am4_backend_online = False
            snap.ontology.error_note = (
                "SIMULATED UNAVAILABLE: Ontology :13000 -> AM4 10.44.0.2 unreachable (fault-isolated)"
            )
            for s in snap.services:
                if "ontology" in s.service_id.lower() or "13000" in s.host_endpoint:
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"
        elif p == "duckdb":
            snap.duckdb.state = "unavailable"
            snap.duckdb.error_note = (
                "SIMULATED UNAVAILABLE: DuckDB intake snapshot/manifest unreachable (fault-isolated)"
            )
            for s in snap.services:
                if "duckdb" in s.service_id.lower():
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"
        elif p in ("lifetime_caches", "caches"):
            snap.lifetime_caches.state = "unavailable"
            snap.lifetime_caches.npu_ast_online = False
        elif p == "aspire":
            for s in snap.services:
                if "aspire" in s.service_id.lower() or "18888" in s.host_endpoint:
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"
        elif p == "hearth":
            for s in snap.services:
                if "hearth" in s.service_id.lower() or "8710" in s.host_endpoint:
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"
        elif p in ("jepa", "jepa_router"):
            for s in snap.services:
                if "jepa" in s.service_id.lower() or "18097" in s.host_endpoint:
                    s.state = "unavailable"
                    s.detail = "simulated unavailable"

    online_svcs = sum(1 for s in snap.services if s.state in ("active", "idle"))
    total_svcs = len(snap.services)
    snap.overall_state = "unavailable"
    snap.headline_available = (
        f"Services: {online_svcs}/{total_svcs} online (DEGRADED: {','.join(snap.simulated_unavailable)} unavailable)"
    )


def _collect_live_snapshot(
    max_cache_age_s: float = 0.0,
    timeout_s: float = 2.5,
    runs_dir: Optional[str] = None,
    task_selector: Optional[str] = None,
    simulate_unavailable: Optional[List[str]] = None,
) -> FlowSnapshot:
    sim_list = simulate_unavailable or []
    if max_cache_age_s > 0.0 and not sim_list and not runs_dir and not os.environ.get("WASHPLANT_READINESS_MANIFEST"):
        cached = load_flow_snapshot_cache(max_age_s=max_cache_age_s)
        if cached is not None:
            _apply_task_selection(cached, task_selector)
            return cached

    from . import collectors

    fn = None
    for candidate in ("collect_flow_snapshot", "collect_snapshot"):
        if hasattr(collectors, candidate):
            fn = getattr(collectors, candidate)
            break
    if fn is None:
        raise RuntimeError("No snapshot collector entrypoint found in tools.flow_top.collectors")

    sig = inspect.signature(fn)
    kwargs: Dict[str, Any] = {}
    if "use_cached_snapshot_max_age_s" in sig.parameters:
        kwargs["use_cached_snapshot_max_age_s"] = max_cache_age_s if not sim_list else 0.0
    elif "max_cache_age_s" in sig.parameters:
        kwargs["max_cache_age_s"] = max_cache_age_s if not sim_list else 0.0
    if "timeout_s" in sig.parameters:
        kwargs["timeout_s"] = timeout_s
    if runs_dir and "runs_dir" in sig.parameters:
        kwargs["runs_dir"] = Path(runs_dir) if sig.parameters["runs_dir"].annotation == "Path" else runs_dir
    if task_selector and "selected_task" in sig.parameters:
        kwargs["selected_task"] = task_selector
    elif task_selector and "task_selector" in sig.parameters:
        kwargs["task_selector"] = task_selector
    if sim_list and "simulate_unavailable" in sig.parameters:
        kwargs["simulate_unavailable"] = sim_list

    snap: FlowSnapshot = fn(**kwargs)
    _apply_task_selection(snap, task_selector)
    _apply_simulated_unavailable(snap, sim_list)
    return snap


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flow-top",
        description=(
            "Sovereign Workflow, Service Bus, Router, Ontology & DuckDB Intake Monitor "
            "(companion to am4-top)"
        ),
    )
    p.add_argument(
        "--render",
        action="store_true",
        help="Render a single frame immediately and exit.",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Default entrypoint: enter live loop if interactive TTY with no one-shot flags, else render once.",
    )
    p.add_argument(
        "--color",
        dest="color_mode",
        action="store_const",
        const="color",
        default=None,
        help="Force 256-color ANSI output.",
    )
    p.add_argument(
        "--no-color",
        dest="color_mode",
        action="store_const",
        const="no-color",
        help="Disable ANSI color codes.",
    )
    p.add_argument(
        "--tty",
        action="store_true",
        help="Format output with direct TTY cursor control sequences (\\033[H\\033[?25l and \\r\\n).",
    )
    p.add_argument(
        "--view",
        choices=VALID_FLOW_VIEWS,
        default=None,
        help="Select monitor view: overview (default), path, services, caches, or data.",
    )
    p.add_argument(
        "--task",
        type=str,
        default=None,
        help="Select correlated task by 1-based index (1..10) or task_id substring.",
    )
    p.add_argument(
        "--runs-dir",
        type=str,
        default=None,
        help="Optional override/additional directory for correlated task receipts.",
    )
    p.add_argument(
        "--simulate-unavailable",
        type=str,
        default=None,
        help=(
            "Comma-separated list of collectors to simulate as unavailable "
            "(service_bus,ontology,duckdb,lifetime_caches,aspire,hearth) to verify fault isolation."
        ),
    )
    p.add_argument(
        "--fixture",
        type=str,
        default=None,
        help="Render a named deterministic fixture from fixtures.py and exit immediately.",
    )
    p.add_argument(
        "--list-fixtures",
        action="store_true",
        help="List available deterministic fixtures and exit.",
    )
    p.add_argument(
        "--width",
        type=int,
        default=None,
        help="Override inner box width (clamped to [76, 300], default 156).",
    )
    p.add_argument(
        "--dump-json",
        action="store_true",
        help="Print the FlowSnapshot JSON and exit.",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Refresh interval in seconds for interactive TTY mode (default: 2.0).",
    )
    return p


def _write_tty_frame(rendered: str, out_stream=None) -> None:
    stream = out_stream or sys.stdout
    lines = rendered.splitlines()
    clean_lines = [line + "\033[K" for line in lines]
    payload = "\033[H\033[?25l" + "\r\n".join(clean_lines) + "\033[J\r\n"
    stream.write(payload)
    stream.flush()


def _run_interactive_loop(
    initial_view: str,
    use_color: bool,
    width_override: Optional[int],
    interval_s: float,
    runs_dir: Optional[str] = None,
    initial_task_selector: Optional[str] = None,
    sim_list: Optional[List[str]] = None,
) -> int:
    import termios
    import tty

    interval_s = max(0.5, float(interval_s))
    current_view = initial_view
    state_lock = threading.Lock()
    current_snapshot: FlowSnapshot = _collect_live_snapshot(
        max_cache_age_s=1.0,
        runs_dir=runs_dir,
        task_selector=initial_task_selector,
        simulate_unavailable=sim_list,
    )
    selected_idx = current_snapshot.selected_task_index
    needs_redraw = threading.Event()
    needs_redraw.set()
    refresh_requested = threading.Event()
    stop_event = threading.Event()

    def _collector_worker() -> None:
        nonlocal current_snapshot, selected_idx
        last_poll = time.monotonic()
        while not stop_event.is_set():
            now = time.monotonic()
            should_poll = refresh_requested.is_set() or ((now - last_poll) >= interval_s)
            if should_poll:
                refresh_requested.clear()
                try:
                    snap = _collect_live_snapshot(
                        max_cache_age_s=0.0,
                        runs_dir=runs_dir,
                        simulate_unavailable=sim_list,
                    )
                    with state_lock:
                        if snap.tasks:
                            snap.selected_task_index = max(0, min(selected_idx, len(snap.tasks) - 1))
                        current_snapshot = snap
                    needs_redraw.set()
                except Exception:
                    pass
                last_poll = time.monotonic()
            stop_event.wait(0.1)

    prev_winch = None

    def _on_sigwinch(signum, frame) -> None:
        needs_redraw.set()

    try:
        prev_winch = signal.signal(signal.SIGWINCH, _on_sigwinch)
    except Exception:
        prev_winch = None

    stdin_fd: Optional[int] = None
    old_termios = None
    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            old_termios = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)
    except Exception:
        stdin_fd = None
        old_termios = None

    worker = threading.Thread(target=_collector_worker, name="flow-top-collector", daemon=True)
    worker.start()

    try:
        while not stop_event.is_set():
            if needs_redraw.is_set():
                needs_redraw.clear()
                with state_lock:
                    snap_to_draw = current_snapshot
                    if snap_to_draw.tasks:
                        snap_to_draw.selected_task_index = max(
                            0, min(selected_idx, len(snap_to_draw.tasks) - 1)
                        )
                frame_str = render_flow_snapshot(
                    snap_to_draw,
                    view=current_view,
                    use_color=use_color,
                    width=width_override,
                    is_tty=True,
                )
                _write_tty_frame(frame_str, sys.stdout)

            if stdin_fd is not None:
                rlist, _, _ = select.select([stdin_fd], [], [], 0.15)
                if rlist:
                    ch_bytes = os.read(stdin_fd, 1)
                    if not ch_bytes:
                        break
                    ch = ch_bytes.decode("utf-8", errors="ignore").lower()
                    if ch in ("q", "\x03", "\x04"):
                        break
                    if ch in KEY_TO_VIEW:
                        new_v = KEY_TO_VIEW[ch]
                        if new_v != current_view:
                            current_view = new_v
                        needs_redraw.set()
                    elif ch in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0"):
                        with state_lock:
                            n_tasks = min(10, len(current_snapshot.tasks))
                            if n_tasks > 0:
                                target = 9 if ch == "0" else (int(ch) - 1)
                                selected_idx = max(0, min(target, n_tasks - 1))
                                current_snapshot.selected_task_index = selected_idx
                        needs_redraw.set()
                    elif ch in ("j", "n"):
                        with state_lock:
                            n_tasks = min(10, len(current_snapshot.tasks))
                            if n_tasks > 0:
                                selected_idx = (selected_idx + 1) % n_tasks
                                current_snapshot.selected_task_index = selected_idx
                        needs_redraw.set()
                    elif ch in ("k",):
                        with state_lock:
                            n_tasks = min(10, len(current_snapshot.tasks))
                            if n_tasks > 0:
                                selected_idx = (selected_idx - 1) % n_tasks
                                current_snapshot.selected_task_index = selected_idx
                        needs_redraw.set()
                    elif ch == "r":
                        refresh_requested.set()
            else:
                time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if stdin_fd is not None and old_termios is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
            except Exception:
                pass
        if prev_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, prev_winch)
            except Exception:
                pass
        try:
            sys.stdout.write("\033[0m\033[?25h\r\n")
            sys.stdout.flush()
        except Exception:
            pass

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_fixtures:
        for name in _load_fixture_names():
            print(name)
        return 0

    selected_view = args.view or "overview"
    explicit_view_flag = args.view is not None
    sim_list = _parse_simulated_unavailable(args.simulate_unavailable)

    stdout_is_tty = bool(sys.stdout.isatty())
    stdin_is_tty = bool(sys.stdin.isatty())

    if args.color_mode == "no-color":
        use_color = False
    elif args.color_mode == "color" or args.tty:
        use_color = True
    else:
        use_color = True if not args.dump_json else False

    if args.fixture:
        try:
            snap = _load_fixture_snapshot(args.fixture)
        except KeyError as exc:
            sys.stderr.write(f"Error: {exc}. Use --list-fixtures to see valid fixture names.\n")
            return 2
        _apply_task_selection(snap, args.task)
        _apply_simulated_unavailable(snap, sim_list)
        if args.dump_json:
            print(snap.to_json(indent=2))
            return 0
        out = render_flow_snapshot(
            snap,
            view=selected_view,
            use_color=use_color,
            width=args.width,
            is_tty=args.tty,
        )
        if args.tty:
            _write_tty_frame(out, sys.stdout)
        else:
            print(out)
        return 0

    if args.dump_json:
        snap = _collect_live_snapshot(
            max_cache_age_s=1.5 if not sim_list else 0.0,
            runs_dir=args.runs_dir,
            task_selector=args.task,
            simulate_unavailable=sim_list,
        )
        print(snap.to_json(indent=2))
        return 0

    is_one_shot = (
        args.render
        or args.tty
        or not stdout_is_tty
        or not stdin_is_tty
        or (explicit_view_flag and not args.once)
        or (args.task is not None and not args.once)
        or (bool(sim_list) and not args.once)
    )

    if is_one_shot:
        cache_age = 1.5 if (explicit_view_flag and not args.render and not sim_list) else 0.0
        snap = _collect_live_snapshot(
            max_cache_age_s=cache_age,
            runs_dir=args.runs_dir,
            task_selector=args.task,
            simulate_unavailable=sim_list,
        )
        out = render_flow_snapshot(
            snap,
            view=selected_view,
            use_color=use_color,
            width=args.width,
            is_tty=args.tty,
        )
        if args.tty:
            _write_tty_frame(out, sys.stdout)
        else:
            print(out)
        return 0

    return _run_interactive_loop(
        initial_view=selected_view,
        use_color=use_color,
        width_override=args.width,
        interval_s=args.interval,
        runs_dir=args.runs_dir,
        initial_task_selector=args.task,
        sim_list=sim_list,
    )


if __name__ == "__main__":
    raise SystemExit(main())
