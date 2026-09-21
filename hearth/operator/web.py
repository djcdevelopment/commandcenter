"""The operator web workspace: a loopback-bound, authenticated PROJECTION (Gate 6).

`operator serve` renders runs/operator/ and CURRENT.json for a browser. It is a
projection, never truth: it reads the same files the CLI reads and writes
nothing. It binds 127.0.0.1 only (a non-loopback bind is refused, not merely
discouraged) and every request must carry the workspace token -- as a
`Bearer` header, or as the cookie that `/login?token=...` sets once so a browser
can be used. Without the token: 401, no content, no listing.

The token comes from `HEARTH_OPERATOR_WEB_TOKEN`, else from
`hearth/var/operator/web.token` (git-ignored; generated on first start and
printed ONCE as a login URL to stderr, never to the access log).
"""
from __future__ import annotations

import html
import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from . import paths
from . import learn as learn_mod

TOKEN_ENV = "HEARTH_OPERATOR_WEB_TOKEN"
COOKIE = "operator_web"
DEFAULT_PORT = 8795


def resolve_token() -> tuple[str, str]:
    """(token, source). Generates and stores one when none is configured."""
    env = os.environ.get(TOKEN_ENV)
    if env:
        return env, f"env {TOKEN_ENV}"
    target = paths.var_dir() / "web.token"
    if target.is_file():
        stored = target.read_text(encoding="utf-8").strip()
        if stored:
            return stored, paths.repo_relative(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    target.write_text(token + "\n", encoding="utf-8")
    return token, f"{paths.repo_relative(target)} (generated)"


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


STYLE = (
    "<style>:root{--bg:#fbfbf9;--fg:#1c1c1a;--muted:#6b6b66;--line:#e2e2dc;--accent:#8a4b08;--ok:#2f6b3a;--bad:#9a2f2f}"
    "@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141412;--fg:#e8e6df;--muted:#9a9891;--line:#2c2c28;--accent:#e0a458;--ok:#7fc48a;--bad:#e07a7a}}"
    ":root[data-theme=dark]{--bg:#141412;--fg:#e8e6df;--muted:#9a9891;--line:#2c2c28;--accent:#e0a458;--ok:#7fc48a;--bad:#e07a7a}"
    "body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif;max-width:1100px;margin-inline:auto}"
    "h1{font-size:1.4rem;margin:0 0 4px}h2{font-size:1.05rem;margin:24px 0 8px;color:var(--accent)}"
    "table{border-collapse:collapse;width:100%;font-size:.9rem;display:block;overflow-x:auto}"
    "th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;white-space:nowrap}"
    "code,pre{font-family:ui-monospace,Consolas,monospace;font-size:.85em}pre{white-space:pre-wrap;border:1px solid var(--line);padding:10px;border-radius:6px;overflow-x:auto}"
    ".muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}a{color:var(--accent)}nav a{margin-right:14px}</style>"
)


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{_esc(title)}</title><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">{STYLE}</head><body>"
            "<nav><a href=\"/\">Workspace</a><a href=\"/learning\">Learning</a><a href=\"/current.json\">current.json</a><a href=\"/api/runs.json\">runs.json</a></nav>"
            f"{body}<p class=muted>Projection of runs/operator and CURRENT.json rendered by hearth.operator.web; loopback only; never truth.</p></body></html>")


def _current() -> dict:
    current = paths.current_path()
    data = json.loads(current.read_text(encoding="utf-8")) if current.is_file() else {}
    cap = data.get("capacity_snapshot") or {}
    snap_id = cap.get("snapshot_id")
    snapshot = None
    if snap_id:
        target = paths.snapshots_dir() / f"{snap_id}.json"
        if target.is_file():
            snapshot = json.loads(target.read_text(encoding="utf-8"))
    return {"current": data, "snapshot": snapshot}


def render_index() -> str:
    cur = _current()
    snap = cur["snapshot"] or {}
    rungs = snap.get("rungs") or {}
    door = ((snap.get("door") or {}).get("reachable") or {})
    parts = ["<h1>Operator workspace</h1>",
             f"<p class=muted>operator home <code>{_esc(paths.operator_home())}</code></p>",
             "<h2>CURRENT planning snapshot</h2>"]
    if snap:
        parts.append("<table>"
                     f"<tr><th>snapshot_id</th><td><code>{_esc(snap.get('snapshot_id'))}</code></td></tr>"
                     f"<tr><th>catalog_version</th><td><code>{_esc(snap.get('catalog_version'))}</code></td></tr>"
                     f"<tr><th>observed_at</th><td>{_esc(snap.get('observed_at'))}</td></tr>"
                     f"<tr><th>planning_valid_until</th><td>{_esc(snap.get('planning_valid_until'))}</td></tr>"
                     f"<tr><th>door.reachable</th><td>{_esc(door.get('value'))} <span class=muted>{_esc(door.get('reason') or '')}</span></td></tr>"
                     "</table><h2>Rungs</h2><table><tr><th>rung</th><th>ready</th><th>resident</th><th>source</th><th>reason</th></tr>")
        for rid, row in sorted(rungs.items()):
            ready = (row.get("ready") or {})
            res = (row.get("residency") or row.get("resident_models") or {})
            val = ready.get("value")
            cls = "ok" if val is True else ("bad" if val is False else "muted")
            parts.append(f"<tr><td><code>{_esc(rid)}</code></td><td class={cls}>{_esc(val)}</td>"
                         f"<td>{_esc(', '.join(res.get('value') or []) if isinstance(res.get('value'), list) else (res.get('value') or '-'))}</td>"
                         f"<td class=muted>{_esc(ready.get('source'))}</td><td class=muted>{_esc(ready.get('reason') or '')}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class=bad>No CURRENT.json / snapshot on disk: run <code>operator inspect --refresh</code>.</p>")
    rows = learn_mod._collect_runs(include_test_mode=True)
    parts.append("<h2>Runs</h2><table><tr><th>run</th><th>status</th><th>target</th><th>host</th><th>model</th><th>duration s</th><th>tokens in/out</th><th>attempts</th><th>replay</th><th>test</th></tr>")
    for r in rows:
        parts.append(f"<tr><td><a href=\"/run/{_esc(r['run_id'])}\"><code>{_esc(r['run_id'])}</code></a></td><td>{_esc(r['status'])}</td>"
                     f"<td><code>{_esc(r['target'])}</code></td><td>{_esc(r['host'])}</td><td>{_esc(r['model'])}</td><td>{_esc(r['duration_s'])}</td>"
                     f"<td>{_esc(r['prompt_tokens'])}/{_esc(r['completion_tokens'])}</td><td>{r['attempts']}</td>"
                     f"<td class={'ok' if r['reconstructable'] else 'muted'}>{'reconstructable' if r['reconstructable'] else 'auditable only'}</td>"
                     f"<td>{'test_mode' if r['test_mode'] else ''}</td></tr>")
    parts.append("</table>")
    return _page("Operator workspace", "".join(parts))


def render_run(run_id: str) -> Optional[str]:
    run_dir = paths.run_dir(run_id)
    if not run_dir.is_dir() or "/" in run_id or "\\" in run_id or ".." in run_id:
        return None
    state_path = paths.run_state_path(run_id)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    refs = paths.run_refs_dir(run_id)
    parts = [f"<h1>Run <code>{_esc(run_id)}</code></h1>", "<h2>RUN-STATE.json (replayed)</h2>",
             f"<pre>{_esc(json.dumps(state, indent=2, sort_keys=True))}</pre>", "<h2>refs/</h2><table><tr><th>ref</th><th>bytes</th></tr>"]
    if refs.is_dir():
        for p in sorted(refs.iterdir()):
            parts.append(f"<tr><td><a href=\"/run/{_esc(run_id)}/ref/{_esc(p.name)}\"><code>{_esc(p.name)}</code></a></td><td>{p.stat().st_size}</td></tr>")
    parts.append("</table>")
    for p in sorted(run_dir.glob("*.md")):
        parts.append(f"<h2>artifact {_esc(p.name)}</h2><pre>{_esc(p.read_text(encoding='utf-8', errors='replace'))}</pre>")
    try:
        from .explain import explain_run
        parts.append(f"<h2>explain</h2><pre>{_esc(explain_run(run_id))}</pre>")
    except Exception as exc:  # a projection reports, it does not hide
        parts.append(f"<p class=bad>explain failed: {_esc(type(exc).__name__)}: {_esc(exc)}</p>")
    return _page(f"Run {run_id}", "".join(parts))


class Handler(BaseHTTPRequestHandler):
    server_version = "operator-web/0.1"
    token: str = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # never log a query string
        path = urlsplit(self.path).path
        sys.stderr.write(f"{self.address_string()} {self.command} {path} {args[1] if len(args) > 1 else ''}\n")

    # --- auth -----------------------------------------------------------
    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), self.token):
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and secrets.compare_digest(value, self.token):
                return True
        return False

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        if url.path == "/login":
            token = (parse_qs(url.query).get("token") or [""])[0]
            if secrets.compare_digest(token, self.token):
                self._send(HTTPStatus.FOUND, b"", "text/plain",
                           {"Set-Cookie": f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200", "Location": "/"})
            else:
                self._send(HTTPStatus.UNAUTHORIZED, b"unauthorized\n", "text/plain")
            return
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, b"unauthorized: present the workspace token (Bearer header, or /login?token=...)\n",
                       "text/plain", {"WWW-Authenticate": "Bearer realm=\"operator-web\""})
            return
        try:
            if url.path == "/":
                self._send(HTTPStatus.OK, render_index().encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/current.json":
                self._send(HTTPStatus.OK, json.dumps(_current(), indent=2, sort_keys=True).encode("utf-8"), "application/json")
            elif url.path == "/api/runs.json":
                self._send(HTTPStatus.OK, json.dumps(learn_mod._collect_runs(include_test_mode=True), indent=2).encode("utf-8"), "application/json")
            elif url.path == "/learning":
                target = paths.operator_home() / "OPERATOR-LEARNING.html"
                if target.is_file():
                    self._send(HTTPStatus.OK, target.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"no OPERATOR-LEARNING.html: run operator learn report\n", "text/plain")
            elif url.path.startswith("/run/"):
                rest = url.path[len("/run/"):]
                run_id, _, tail = rest.partition("/")
                if tail.startswith("ref/"):
                    name = tail[len("ref/"):]
                    target = paths.run_refs_dir(run_id) / name
                    if name and "/" not in name and ".." not in name and target.is_file():
                        self._send(HTTPStatus.OK, target.read_bytes(), "application/json" if name.endswith(".json") else "text/plain; charset=utf-8")
                    else:
                        self._send(HTTPStatus.NOT_FOUND, b"no such ref\n", "text/plain")
                    return
                page = render_run(run_id)
                if page is None:
                    self._send(HTTPStatus.NOT_FOUND, b"no such run\n", "text/plain")
                else:
                    self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        except Exception as exc:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, f"projection error: {type(exc).__name__}: {exc}\n".encode("utf-8"), "text/plain")


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> int:
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"serve: refusing to bind {host}: the workspace is loopback-only by design", file=sys.stderr)
        return 2
    token, source = resolve_token()
    Handler.token = token
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"operator web workspace on http://{host}:{port}/  (token from {source})", file=sys.stderr)
    print(f"login once in a browser: http://{host}:{port}/login?token={token}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
