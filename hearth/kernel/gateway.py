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
import json
import logging
import time
import typing
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP

from hearth.kernel.auth import HEADER_NAME, AuthRegistry
from hearth.kernel.context import HearthContext
from hearth.kernel.guards import GuardRejection, GuardStack
from hearth.kernel.ledger import REPO_ROOT, Ledger, hearth_root, new_event

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8710
KERNEL_DIR = Path(__file__).resolve().parent
BUILTIN_PROVIDER = "hearth.kernel.gateway#builtin"
KNOWLEDGE_MODULE_SUFFIX = ".knowledge"

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
                 guards: GuardStack, key_provider: KeyProvider) -> Callable:
    """Wrap one provider callable with auth + guards + provenance + ledger."""
    tool_name = fn.__name__
    sig, hints = _resolved_signature(fn)

    def wrapper(**kwargs: Any) -> Any:
        started = time.perf_counter()
        caller = auth.resolve(key_provider())
        if caller is None:
            raise PermissionError(
                f"unknown or missing {HEADER_NAME} key; rejection recorded in the ledger"
            )
        hearth.caller = caller

        def elapsed_ms() -> float:
            return (time.perf_counter() - started) * 1000.0

        try:
            guards.check(tool_name, kwargs)
        except GuardRejection as exc:
            hearth.ledger.append(new_event(
                caller.as_dict(), tool_name, args=kwargs,
                ok=False, error=str(exc), duration_ms=elapsed_ms(),
            ))
            raise

        result, ok, error = None, True, None
        try:
            result = fn(**kwargs)
            return result
        except Exception as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            hearth.ledger.append(new_event(
                caller.as_dict(), tool_name, args=kwargs, result=result,
                ok=ok, error=error, duration_ms=elapsed_ms(),
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

    providers = load_providers(providers_spec)
    mounted = [BUILTIN_PROVIDER, *providers]
    providers[BUILTIN_PROVIDER] = builtin_get_tools(hearth, mounted)

    for module_name, tools in providers.items():
        if module_name.endswith(KNOWLEDGE_MODULE_SUFFIX):
            guards.register_knowledge_tools(fn.__name__ for fn in tools)

    registered: set[str] = set()
    for module_name, tools in providers.items():
        for fn in tools:
            if fn.__name__ in registered:
                log.warning("duplicate tool %s from %s skipped", fn.__name__, module_name)
                continue
            mcp.add_tool(make_wrapper(fn, hearth, auth, guards, key_provider))
            registered.add(fn.__name__)
    log.info("hearth gateway: %d tools from %d providers", len(registered), len(providers))
    return mcp


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="HEARTH kernel gateway daemon")
    parser.add_argument("--providers", default="",
                        help="comma-separated provider modules, e.g. hearth.toolsurface.fs,hearth.toolsurface.git")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--callers", default=None,
                        help="path to callers.json (default hearth/etc/callers.json)")
    parser.add_argument("--ledger-dir", default=None,
                        help="ledger directory (default $HEARTH_ROOT/var/ledger)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp = build_server(providers_spec=args.providers, host=args.host, port=args.port,
                       callers_path=args.callers, ledger_dir=args.ledger_dir)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
