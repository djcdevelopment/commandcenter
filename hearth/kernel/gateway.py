"""HEARTH kernel gateway (Stream H-A, frozen contract 3): the single always-on
MCP door through which every agent acts on the lab.

FastMCP over streamable-http at http://127.0.0.1:8710/mcp. Each provider tool
(contract: module exposing ``get_tools() -> list[Callable]`` of plain typed
functions with docstrings) is registered wrapped so that every call:

  (a) resolves caller identity from the X-Hearth-Key HTTP header,
  (b) rejects unknown keys (the rejection is itself a ledger event),
  (c) is timed,
  (d) has sha256 digests of its JSON-serialized args and result computed,
  (e) lands in the append-only ledger as a hearth-event.v1, and
  (f) returns the provider's result unchanged.

Header extraction (mcp 1.28.1): the streamable-http transport stores the
starlette Request as ``RequestContext.request``; inside a tool call it is
reachable via ``FastMCP.get_context().request_context.request.headers``.

Run:
  <venv-python> -m hearth.kernel.gateway --providers hearth.toolsurface.fs,hearth.toolsurface.git

Missing provider modules are logged and skipped, so the kernel runs standalone
(with its built-in kernel_status / kernel_change tools) before other streams land.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import ipaddress
import json
import logging
import os
import time
import typing
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP

from hearth.kernel.auth import HEADER_NAME, AuthRegistry
from hearth.kernel.capabilities import assert_surface_complete, check_tool_access
from hearth.kernel.context import HearthContext
from hearth.kernel.guards import GuardRejection, GuardStack
from hearth.kernel.ledger import REPO_ROOT, Ledger, classify_error, hearth_root, new_event
from hearth.kernel.timers import start_timers
from hearth.toolsurface._scope import caller_repo_access, caller_scope

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8710
# ADR-0019 container access. Precedence for both host and port is CLI > env >
# default, so an operator can flip the mode from a launcher script without
# editing code, and a deliberate command line still wins over the environment.
HOST_ENV_VAR = "HEARTH_GATEWAY_HOST"
PORT_ENV_VAR = "HEARTH_GATEWAY_PORT"
CONTAINER_ACCESS_ENV_VAR = "HEARTH_CONTAINER_ACCESS_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}
KERNEL_DIR = Path(__file__).resolve().parent
BUILTIN_PROVIDER = "hearth.kernel.gateway#builtin"
KNOWLEDGE_MODULE_SUFFIX = ".knowledge"
# Tools outside the knowledge module that still legitimately reference a
# knowledge/ path in their own args — read-only queries, or the am4 catalog
# owner that writes its OWN non-corpus file. The guard can't tell read from
# write (and FastMCP passes default arg values, so even a caller who omits the
# path trips it), so these need the same trust as the knowledge module's tools.
# Keep this tight: only tools whose knowledge-path use is verified benign.
EXTRA_KNOWLEDGE_READERS = {
    "patrol",              # patrol.py — capacity_path, read-only
    "query_am4_catalog",   # am4.py — reads knowledge/am4_catalog.json
    "gather_am4_catalog",  # am4.py — writes its OWN knowledge/am4_catalog.json (not the belief corpus)
    "propose_schedule",    # scheduler.py — reads capacity.json + am4_catalog.json
    "schedule_hindsight",  # scheduler.py — reads knowledge/capacity.json
}

# JS1: task_class bucketing for ledger events, keyed by tool name (exact match
# first, then a prefix match against TOOL_CLASS_PREFIXES). Unknown tools get
# task_class=None rather than a guess.
TOOL_CLASS: dict[str, str] = {
    "local_generate": "inference",
    "submit_task": "dispatch",
    "task_status": "dispatch",
    "run_tests": "test",
    "read_file": "io",
    "write_file": "io",
    "glob_files": "io",
    "list_dir": "io",
    "project": "query",
    "preflight": "health",
    "masters_pet": "health",
    "patrol": "health",
}
TOOL_CLASS_PREFIXES: tuple[tuple[str, str], ...] = (
    ("git_", "vcs"),
    ("query_", "query"),
)

# JS1: model-threading mechanism. A provider tool that resolves a model_id
# locally (e.g. local_generate picking a backend's model) cannot itself write
# the ledger event -- the wrapper does that, after the call returns. Rather
# than plumb a new return channel through every provider, a tool may stash the
# resolved model under the "_ledger_model" key in its (dict) result; the
# wrapper lifts that key into the event's `model` field and pops it back out
# before returning the result to the caller, so the public tool contract is
# unchanged.
LEDGER_MODEL_KEY = "_ledger_model"


def _env_flag(name: str) -> bool:
    """True when an env var is set to an affirmative value. Anything else — unset,
    empty, '0', 'false', a typo — is False: consent must be stated, not guessed."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_loopback_host(host: str) -> bool:
    """True only for addresses that cannot be reached from off this machine.

    Fails CLOSED: an unresolvable or unrecognized hostname is treated as
    non-loopback, so a typo produces a refusal to start rather than an
    unnoticed exposure. Note that 0.0.0.0 and :: are 'unspecified', not
    loopback — binding them means every interface, which is exactly the case
    this gate exists for.
    """
    text = (host or "").strip()
    if not text:
        return False
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def _resolve_bind_host(cli_value: Optional[str]) -> tuple[str, str]:
    """(host, where it came from) — CLI > env > default."""
    if cli_value is not None:
        return cli_value, "--host"
    from_env = os.environ.get(HOST_ENV_VAR, "").strip()
    if from_env:
        return from_env, f"${HOST_ENV_VAR}"
    return DEFAULT_HOST, "default"


def _resolve_bind_port(cli_value: Optional[int]) -> tuple[int, str]:
    """(port, where it came from) — CLI > env > default."""
    if cli_value is not None:
        return cli_value, "--port"
    from_env = os.environ.get(PORT_ENV_VAR, "").strip()
    if from_env:
        try:
            return int(from_env), f"${PORT_ENV_VAR}"
        except ValueError as exc:
            raise SystemExit(f"{PORT_ENV_VAR} is not an integer: {from_env!r}") from exc
    return DEFAULT_PORT, "default"


def _task_class_for(tool_name: str) -> Optional[str]:
    if tool_name in TOOL_CLASS:
        return TOOL_CLASS[tool_name]
    for prefix, task_class in TOOL_CLASS_PREFIXES:
        if tool_name.startswith(prefix):
            return task_class
    return None


def _lift_ledger_model(result: Any) -> Optional[str]:
    """Pop and return the _ledger_model hint from a dict result, if present."""
    if isinstance(result, dict) and LEDGER_MODEL_KEY in result:
        return result.pop(LEDGER_MODEL_KEY)
    return None


LEDGER_TASK_CLASS_KEY = "_ledger_task_class"


def _lift_ledger_task_class(result: Any) -> Optional[str]:
    """Pop and return the _ledger_task_class hint from a dict result, if present.
    A tool-supplied task_class overrides the static TOOL_CLASS-derived one."""
    if isinstance(result, dict) and LEDGER_TASK_CLASS_KEY in result:
        return result.pop(LEDGER_TASK_CLASS_KEY)
    return None


log = logging.getLogger("hearth.gateway")

KeyProvider = Callable[[], Optional[str]]


def make_header_key_provider(mcp: FastMCP) -> KeyProvider:
    """Key provider reading X-Hearth-Key from the current HTTP request headers."""
    def provider() -> Optional[str]:
        try:
            request = mcp.get_context().request_context.request
        except (ValueError, LookupError):
            return None
        if request is None or not hasattr(request, "headers"):
            return None
        return request.headers.get(HEADER_NAME)
    return provider


def make_task_id_provider(mcp: FastMCP) -> Callable[[], Optional[str]]:
    """task_id provider reading the caller-supplied MCP request meta (_meta),
    the channel HearthClient uses (call_tool(..., meta={"task_id": ...}))."""
    def provider() -> Optional[str]:
        try:
            meta = mcp.get_context().request_context.meta
        except (ValueError, LookupError):
            return None
        if meta is None:
            return None
        task_id = getattr(meta, "task_id", None)
        if task_id is None:
            extra = getattr(meta, "model_extra", None) or {}
            task_id = extra.get("task_id")
        return str(task_id) if task_id is not None else None
    return provider


def _resolved_signature(fn: Callable) -> tuple[inspect.Signature, dict]:
    """The provider's signature with string annotations resolved in its own
    module, so FastMCP builds the tool schema correctly from the wrapper."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    params = [p.replace(annotation=hints.get(p.name, p.annotation))
              for p in sig.parameters.values()]
    sig = sig.replace(parameters=params,
                      return_annotation=hints.get("return", sig.return_annotation))
    return sig, hints


def make_wrapper(fn: Callable, hearth: HearthContext, auth: AuthRegistry,
                 guards: GuardStack, key_provider: KeyProvider,
                 task_id_provider: Callable[[], Optional[str]] = lambda: None) -> Callable:
    """Wrap one provider callable with auth + guards + provenance + ledger."""
    tool_name = fn.__name__
    sig, hints = _resolved_signature(fn)
    task_class = _task_class_for(tool_name)

    def wrapper(**kwargs: Any) -> Any:
        started = time.perf_counter()

        def elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        caller = auth.resolve(key_provider())
        if caller is None:
            raise PermissionError(
                f"unknown or missing {HEADER_NAME} key; rejection recorded in the ledger"
            )
        task_id = task_id_provider()

        # ADR-0019: authorization sits between authentication and everything
        # else. It runs BEFORE the guard stack and before any argument
        # inspection, so a caller that may not reach this tool cannot learn
        # anything from how its arguments were handled. The denial event names
        # the identity, profile, tool and mapped capability — and deliberately
        # carries args=None, because a refused call must not write the arguments
        # it was refused for into the audit trail.
        profile = auth.profile_for(caller)
        allowed, capability = check_tool_access(profile, tool_name)
        if not allowed:
            denial = (
                f"capability: profile {caller.ledger_profile!r} does not grant "
                f"{capability!r} (required by tool {tool_name!r})" if capability else
                f"capability: tool {tool_name!r} has no capability mapping; "
                f"profiled caller {caller.id!r} is denied fail-closed"
            )
            hearth.ledger.append(new_event(
                caller.as_dict(), tool_name, args=None,
                ok=False, error=denial, duration_ms=elapsed_ms(),
                task_id=task_id, task_class=task_class,
                profile=caller.ledger_profile,
            ))
            raise PermissionError(denial)

        hearth.caller = caller

        try:
            guards.check(tool_name, kwargs)
        except GuardRejection as exc:
            hearth.ledger.append(new_event(
                caller.as_dict(), tool_name, args=kwargs,
                ok=False, error=str(exc), duration_ms=elapsed_ms(),
                task_id=task_id, task_class=task_class,
                profile=caller.ledger_profile,
            ))
            raise

        result, ok, error, model = None, True, None, None
        event_task_class = task_class
        backend, routed_by, occupancy, error_code = None, None, None, None
        cost = None
        try:
            # Both authority domains are in force for the duration of the call
            # and reset on the way out, so a raising tool cannot leak one
            # caller's grants into the next call on this thread. Filesystem and
            # repository authority are pushed independently: neither implies the
            # other (ADR-0019).
            with caller_scope(caller.file_scope), caller_repo_access(caller.repo_access):
                result = fn(**kwargs)
            model = _lift_ledger_model(result)
            lifted = _lift_ledger_task_class(result)
            if lifted:
                event_task_class = lifted
            if isinstance(result, dict):
                # S1: routing provenance rides the result dict; only strings are
                # ledgered (some tools return occupancy as a nested dict).
                raw = (result.get("backend"), result.get("routed_by"), result.get("occupancy"))
                backend, routed_by, occupancy = (
                    v if isinstance(v, str) else None for v in raw)
                if result.get("ok") is False and isinstance(result.get("error"), str):
                    error_code = classify_error(result["error"])
                # S2: token counts ride the result dict too (inference results carry
                # tokens_in/tokens_out); lift them into the event's cost so per-rung
                # token economics are computable. Numbers only.
                tokens = {key: result.get(key) for key in ("tokens_in", "tokens_out")}
                tokens = {key: v for key, v in tokens.items()
                          if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if tokens:
                    cost = tokens
            return result
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
            error_code = classify_error(error)
            raise
        finally:
            hearth.ledger.append(new_event(
                caller.as_dict(), tool_name, args=kwargs, result=result,
                ok=ok, error=error, duration_ms=elapsed_ms(), cost=cost,
                task_id=task_id, task_class=event_task_class, model=model,
                backend=backend, routed_by=routed_by, occupancy=occupancy,
                error_code=error_code, profile=caller.ledger_profile,
            ))

    wrapper.__name__ = tool_name
    wrapper.__qualname__ = tool_name
    wrapper.__doc__ = fn.__doc__
    wrapper.__signature__ = sig  # type: ignore[attr-defined]
    wrapper.__annotations__ = dict(hints)
    return wrapper


def builtin_get_tools(hearth: HearthContext,
                      mounted: list[str]) -> list[Callable]:
    """The kernel's own provider: self-test status + the kernel_change ceremony."""

    def kernel_status() -> dict[str, Any]:
        """Kernel self-test: gateway identity, mounted providers, ledger location
        and event count."""
        events = hearth.ledger.query()
        return {
            "kernel": "hearth",
            "repo_root": str(hearth.repo_root),
            "providers": list(mounted),
            "ledger_dir": str(hearth.ledger.dir),
            "event_count": len(events),
            "caller": hearth.caller.as_dict() if hearth.caller else None,
        }

    def kernel_change(description: str, diff_path: str) -> dict[str, Any]:
        """Kernel change ceremony: snapshot the kernel source dir to a zip under
        hearth/var/kernel_snapshots/ and record a ledger event BEFORE acknowledging.
        Kernel edits are rare, logged, and reversible (L0)."""
        snapshot_dir = hearth_root() / "var" / "kernel_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot_path = snapshot_dir / f"kernel-{stamp}-{uuid.uuid4().hex[:8]}.zip"
        with zipfile.ZipFile(snapshot_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(KERNEL_DIR.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(KERNEL_DIR.parent))
        caller = hearth.caller.as_dict() if hearth.caller else {
            "id": "__kernel__", "runner_class": "human", "node": "unknown"}
        event_id = hearth.ledger.append(new_event(
            caller, "kernel_change.snapshot",
            args={"description": description, "diff_path": diff_path},
            result={"snapshot": str(snapshot_path)},
        ))
        return {
            "acknowledged": True,
            "snapshot": str(snapshot_path),
            "ceremony_event_id": event_id,
            "description": description,
            "diff_path": diff_path,
        }

    return [kernel_status, kernel_change]


def load_providers(spec: str) -> dict[str, list[Callable]]:
    """Import each comma-separated module and collect its get_tools() output.
    Missing or malformed modules are logged and skipped."""
    providers: dict[str, list[Callable]] = {}
    for name in filter(None, (part.strip() for part in spec.split(","))):
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            log.warning("provider %s not importable, skipped: %s", name, exc)
            continue
        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            log.warning("provider %s has no get_tools(), skipped", name)
            continue
        providers[name] = list(get_tools())
    return providers


def wire_knowledge_guards(guards: GuardStack, providers: dict[str, list[Callable]]) -> None:
    """Register every trusted knowledge-path referrer: the knowledge module's
    own tools (derived from the mounted providers) plus EXTRA_KNOWLEDGE_READERS."""
    for module_name, tools in providers.items():
        if module_name.endswith(KNOWLEDGE_MODULE_SUFFIX):
            guards.register_knowledge_tools(fn.__name__ for fn in tools)
    guards.register_knowledge_tools(EXTRA_KNOWLEDGE_READERS)


def build_server(providers_spec: str = "", host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT,
                 callers_path: Optional[Path | str] = None,
                 ledger_dir: Optional[Path | str] = None) -> FastMCP:
    """Assemble the gateway: ledger, auth, guards, built-in + provider tools."""
    ledger = Ledger(ledger_dir)
    hearth = HearthContext(repo_root=REPO_ROOT, ledger=ledger)
    auth = AuthRegistry(callers_path=callers_path, ledger=ledger)
    guards = GuardStack(repo_root=REPO_ROOT)

    mcp = FastMCP("hearth")
    mcp.settings.host = host
    mcp.settings.port = port
    key_provider = make_header_key_provider(mcp)
    task_id_provider = make_task_id_provider(mcp)

    providers = load_providers(providers_spec)
    mounted = [BUILTIN_PROVIDER, *providers]
    providers[BUILTIN_PROVIDER] = builtin_get_tools(hearth, mounted)

    wire_knowledge_guards(guards, providers)

    registered: set[str] = set()
    for module_name, tools in providers.items():
        for fn in tools:
            if fn.__name__ in registered:
                log.warning("duplicate tool %s from %s skipped", fn.__name__, module_name)
                continue
            mcp.add_tool(make_wrapper(fn, hearth, auth, guards, key_provider,
                                      task_id_provider))
            registered.add(fn.__name__)

    # ADR-0019 §3: fail closed on an unclassified tool. Checked against the
    # tools ACTUALLY registered above rather than a hand-maintained list, so a
    # new provider cannot introduce a tool that silently lands inside somebody's
    # profile. This raises — a door that half-knows its own policy stays shut.
    assert_surface_complete(registered)

    log.info("hearth gateway: %d tools from %d providers", len(registered), len(providers))

    # ADR-0019 §6: legacy callers reach the full surface. That is the deliberate
    # compatibility path, but it must never be quiet.
    legacy = auth.legacy_caller_ids
    if legacy:
        log.warning(
            "%d caller(s) carry NO capability profile and reach all %d tools "
            "unrestricted: %s - ledgered as 'legacy-unrestricted'. Assign a profile "
            "with `callerctl rotate --profile <name>` when convenient.",
            len(legacy), len(registered), ", ".join(legacy))
    return mcp


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="HEARTH kernel gateway daemon")
    parser.add_argument("--providers", default="",
                        help="comma-separated provider modules, e.g. hearth.toolsurface.fs,hearth.toolsurface.git")
    parser.add_argument("--host", default=None,
                        help=f"bind address (default {DEFAULT_HOST}; env {HOST_ENV_VAR}). "
                             f"A non-loopback address additionally requires --allow-non-loopback "
                             f"or {CONTAINER_ACCESS_ENV_VAR}=1")
    parser.add_argument("--port", type=int, default=None,
                        help=f"bind port (default {DEFAULT_PORT}; env {PORT_ENV_VAR})")
    parser.add_argument("--allow-non-loopback", action="store_true", default=False,
                        help="consent to binding a non-loopback interface (container access, ADR-0019)")
    parser.add_argument("--callers", default=None,
                        help="path to callers.json (default hearth/etc/callers.json)")
    parser.add_argument("--ledger-dir", default=None,
                        help="ledger directory (default $HEARTH_ROOT/var/ledger)")
    parser.add_argument("--no-timers", action="store_true",
                        help="don't start the in-process ops-loop timers (ADR-0015)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    host, host_source = _resolve_bind_host(args.host)
    port, port_source = _resolve_bind_port(args.port)
    container_access = args.allow_non_loopback or _env_flag(CONTAINER_ACCESS_ENV_VAR)

    # ADR-0019 §1: a non-loopback bind is an explicit, consented act. Refuse —
    # never quietly fall back to loopback, because a gateway that binds narrower
    # than it was asked to is indistinguishable from a container networking
    # fault, and that ambiguity costs hours.
    if not is_loopback_host(host) and not container_access:
        parser.exit(2, (
            f"\nREFUSING TO START: asked to bind {host}:{port} ({host_source}), which is not a\n"
            f"loopback address, without explicit container access.\n\n"
            f"  Loopback-only is HEARTH's secure default and is doing real containment work.\n"
            f"  To bind beyond loopback deliberately, set {CONTAINER_ACCESS_ENV_VAR}=1 or pass\n"
            f"  --allow-non-loopback, and pair it with a firewall rule scoped to the Docker/WSL\n"
            f"  subnet (docs/operations/gateway-bindings.md).\n\n"
            f"  Not falling back to loopback: you asked for something specific and did not get it.\n"))

    mcp = build_server(providers_spec=args.providers, host=host, port=port,
                       callers_path=args.callers, ledger_dir=args.ledger_dir)

    log.info("hearth gateway binding %s:%d (host from %s, port from %s)",
             host, port, host_source, port_source)
    if not is_loopback_host(host):
        for line in (
            "=" * 72,
            "HEARTH IS BINDING A NON-LOOPBACK INTERFACE (container access mode)",
            f"  bind        : {host}:{port}",
            "  reachable by: anything that can route to this address, not just this host",
            "  auth        : X-Hearth-Key over PLAINTEXT HTTP — the key is the only control",
            "  required    : a firewall rule scoping inbound to the Docker/WSL subnet",
            "  rollback    : unset the container-access flag and restart (loopback-only)",
            "=" * 72,
        ):
            log.warning(line)

    timers_enabled = not args.no_timers and os.environ.get("HEARTH_TIMERS", "").lower() != "off"
    handles = start_timers(timers_enabled)
    if handles:
        armed = ", ".join(f"{h.spec.name}={int(h.spec.interval_s)}s" for h in handles)
        print(f"hearth timers armed: {armed}")
    else:
        print("hearth timers disabled")

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
