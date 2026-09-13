"""End-to-end proof of the VM inference proxy site block (:8083) in hearth/etc/caddy/Caddyfile.

WHY THIS EXISTS
    llama-swap on 127.0.0.1:8081 serves an OpenAI-compatible ``/v1`` surface next to a
    completely unauthenticated admin surface (``/api/models/unload*``, ``/upstream/*``,
    ``/running``, ``/unload``, ``/ui``, ``/logs``, ``/metrics``).  Exposing :8081 to the
    builder VMs would let any of them unload production.  The ``:8083`` site block is the
    narrowing: ``/v1/*`` forwarded with the rung's bearer injected from the launcher
    environment, everything else 404 without a dial, headers redacted from every log.

    Nothing in that sentence is checkable by reading the config, so this module runs it.
    A throwaway Caddy is started on ephemeral loopback ports against a fake upstream that
    records every request it sees.  THE LIVE PORTS (8081/8082/8083/8710/8711) ARE NEVER
    TOUCHED and the live Caddy is never signalled: the derived config moves every listener
    and every upstream, and the only process this module ever stops is the one it started,
    by handle.

HOW IT STAYS HONEST
    The temporary config is DERIVED from the real Caddyfile by a fixed set of regex
    substitutions, each of which asserts its own match count.  Change the real upstream,
    the real port, or delete the redaction, and the derivation fails before a single
    request is made -- the proof cannot drift away from the file it claims to prove.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = REPO_ROOT / "hearth" / "etc" / "caddy" / "Caddyfile"
LAUNCHER = REPO_ROOT / "fleet" / "funnelproxy" / "serve-funnel-proxy.cmd"

#: The env var the proxy injects.  Must match ``auth_env`` of the omen-arc rung.
AUTH_ENV = "OMEN_ARC_TOKEN"

#: Never a real credential: the value only ever exists inside this process.
DUMMY_TOKEN = "dummy-token-b06-" + uuid.uuid4().hex
DUMMY_HEARTH_KEY = "dummy-hearth-key-b06-" + uuid.uuid4().hex
CLIENT_EVIL_BEARER = "evil-client-bearer-b06-" + uuid.uuid4().hex

#: Ports the proof must never bind or dial, whatever the ephemeral allocator hands out.
LIVE_PORTS = frozenset({8081, 8082, 8083, 8710, 8711, 2019})

#: Every non-/v1 path that must 404 without reaching the upstream.  The llama-swap admin
#: surface (ADR-0045 / hearth/rotation/README.md) plus the bare root.
FORBIDDEN_PATHS = (
    "/",
    "/api/models",
    "/api/models/unload",
    "/api/models/unload/qwen3-30b-a3b",
    "/upstream/qwen3-30b-a3b/health",
    "/running",
    "/unload",
    "/ui",
    "/metrics",
    "/logs",
    "/health",
    "/slots",
    "/completion",
    "/mcp",
    "/v1",  # no trailing slash: NOT matched by /v1/* and must not be forwarded
)


def _find_caddy() -> Optional[str]:
    """Resolve the caddy binary the way the real launcher does, so the test tracks it."""
    if LAUNCHER.is_file():
        m = re.search(r"^set CADDY=(.+)$", LAUNCHER.read_text(encoding="utf-8"), re.M)
        if m:
            candidate = m.group(1).strip().strip('"')
            if Path(candidate).is_file():
                return candidate
    which = shutil.which("caddy")
    return which if which else None


CADDY_BIN = _find_caddy()


def _free_port() -> int:
    """An ephemeral loopback port, asserted clear of every live HEARTH listener."""
    for _ in range(50):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        finally:
            s.close()
        if port not in LIVE_PORTS:
            return port
    raise RuntimeError("could not allocate a free ephemeral port")


def _sub_exactly(text: str, pattern: str, replacement: str, expected: int, label: str) -> str:
    """Substitute and assert the match count -- the tripwire that keeps the proof honest."""
    new_text, n = re.subn(pattern, lambda _m: replacement, text, flags=re.M)
    if n != expected:
        raise AssertionError(
            f"derivation of the test Caddyfile drifted from {CADDYFILE}: "
            f"expected {expected} match(es) for {label} ({pattern!r}), found {n}. "
            "The real config changed shape -- update this proof deliberately, do not relax it."
        )
    return new_text


# --------------------------------------------------------------------------------------
# fake upstream
# --------------------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: List[Dict[str, object]] = []

    def add(self, entry: Dict[str, object]) -> None:
        with self._lock:
            self.requests.append(entry)

    def snapshot(self) -> List[Dict[str, object]]:
        with self._lock:
            return list(self.requests)

    def count(self) -> int:
        with self._lock:
            return len(self.requests)


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    recorder: _Recorder  # set on the subclass created per server

    def log_message(self, fmt: str, *args) -> None:  # silence stderr noise
        return

    def _record(self, body: bytes) -> None:
        self.recorder.add(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body.decode("utf-8", "replace"),
            }
        )

    def _respond(self, payload: Dict[str, object], status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._record(b"")
        if self.path.startswith("/v1/models"):
            self._respond({"object": "list", "data": [{"id": "qwen3-30b-a3b"}]})
        else:
            # Loud on purpose: if this body ever reaches a client, a non-/v1 path leaked.
            self._respond({"leak": "UPSTREAM-REACHED", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record(body)
        self._respond({"object": "chat.completion", "echo_bytes": len(body)})


class _QuietServer(ThreadingHTTPServer):
    """Caddy drops its keep-alive sockets at shutdown; that is not a test signal."""

    def handle_error(self, request, client_address) -> None:  # noqa: D102
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def _start_fake_upstream() -> Tuple[ThreadingHTTPServer, _Recorder, int]:
    recorder = _Recorder()
    handler = type("_BoundHandler", (_FakeUpstreamHandler,), {"recorder": recorder})
    port = _free_port()
    server = _QuietServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server, recorder, port


# --------------------------------------------------------------------------------------
# derived config + throwaway caddy
# --------------------------------------------------------------------------------------


class _Harness:
    """Owns one temp dir, one fake upstream and one throwaway Caddy process."""

    def __init__(self, auth_env_value: Optional[str]) -> None:
        self.auth_env_value = auth_env_value
        self.tmp = Path(tempfile.mkdtemp(prefix="b06-vm-proxy-"))
        if " " in str(self.tmp):
            raise RuntimeError(f"temp dir has a space, Caddyfile paths are unquoted: {self.tmp}")
        self.logdir = self.tmp / "logs"
        self.logdir.mkdir()
        self.server: Optional[ThreadingHTTPServer] = None
        self.recorder = _Recorder()
        self.proc: Optional[subprocess.Popen] = None
        self.upstream_port = 0
        self.proxy_port = 0
        self.funnel_port = 0
        self.dead_port = 0
        self.derived_path = self.tmp / "Caddyfile.derived"
        self.stdio_log = self.tmp / "caddy-stdio.log"
        self._stdio_handle = None

    # -- derivation ---------------------------------------------------------------

    def derive(self) -> str:
        text = CADDYFILE.read_text(encoding="utf-8")

        # 1. Disable the admin API: the live Caddy already owns 127.0.0.1:2019 and a
        #    second binder would abort at startup.
        text = _sub_exactly(text, r"^\{$", "{\n\tadmin off", 1, "global options opener")

        # 2. Point every log at the temp dir (global default + :8711 access + :8083 access).
        text = _sub_exactly(
            text,
            r"output file C:\\work\\commandcenter\\hearth\\var\\",
            f"output file {self.logdir}\\",
            3,
            "rolling log output paths",
        )

        # 3. Move BOTH listeners off the live ports.
        text = _sub_exactly(text, r"^:8711 \{$", f":{self.funnel_port} {{", 1, "the :8711 site address")
        text = _sub_exactly(text, r"^:8083 \{$", f":{self.proxy_port} {{", 1, "the :8083 site address")

        # 4. Move the Funnel block's upstream to a closed port -- the live gateway is
        #    never dialled, and /mcp still has to route to its OWN upstream (proving the
        #    two site blocks in this one process are not cross-wired).
        text = _sub_exactly(
            text, r"reverse_proxy 127\.0\.0\.1:8710", f"reverse_proxy 127.0.0.1:{self.dead_port}",
            1, "the :8711 reverse_proxy upstream",
        )
        text = _sub_exactly(
            text, r"header_up Host 127\.0\.0\.1:8710", f"header_up Host 127.0.0.1:{self.dead_port}",
            1, "the :8711 upstream Host rewrite",
        )

        # 5. Move the VM proxy's upstream to the fake llama-swap.  Everything else about
        #    the block -- the /v1 narrowing, the catch-all 404, the bearer injection, the
        #    503 guard, the body cap and the redaction -- stays byte-identical.
        # the :8711 block's /v1 goes to the FRIEND GATE (ADR-0046), never to llama-swap: point it at
        # a second closed port so the proof shows it dials its own upstream and not the inference one
        text = _sub_exactly(
            text, r"reverse_proxy 127\.0\.0\.1:8791", f"reverse_proxy 127.0.0.1:{self.gate_dead_port}",
            1, "the :8711 /v1 (friend gate) upstream",
        )
        text = _sub_exactly(
            text, r"header_up Host 127\.0\.0\.1:8791", f"header_up Host 127.0.0.1:{self.gate_dead_port}",
            1, "the :8711 /v1 upstream Host rewrite",
        )
        text = _sub_exactly(
            text, r"reverse_proxy 127\.0\.0\.1:8081", f"reverse_proxy 127.0.0.1:{self.upstream_port}",
            1, "the :8083 reverse_proxy upstream",
        )
        text = _sub_exactly(
            text, r"header_up Host 127\.0\.0\.1:8081", f"header_up Host 127.0.0.1:{self.upstream_port}",
            1, "the :8083 upstream Host rewrite",
        )

        # The narrowing and the redaction must survive derivation verbatim.
        for needle in (
            "handle /v1/* {",
            "handle {\n\t\t\trespond 404",
            'header_up Authorization "Bearer {env.%s}"' % AUTH_ENV,
            "@noauth expression {env.%s} == \"\"" % AUTH_ENV,
            "request>headers>Authorization delete",
            "request>headers>X-Hearth-Key delete",
        ):
            if needle not in text:
                raise AssertionError(f"derivation lost {needle!r} -- the proof would be vacuous")
        return text

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        self.server, self.recorder, self.upstream_port = _start_fake_upstream()
        self.proxy_port = _free_port()
        self.funnel_port = _free_port()
        self.dead_port = _free_port()  # allocated then left closed on purpose
        self.gate_dead_port = _free_port()  # the friend gate's stand-in: also closed on purpose

        self.derived_path.write_text(self.derive(), encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if k != AUTH_ENV}
        if self.auth_env_value is not None:
            env[AUTH_ENV] = self.auth_env_value

        self._stdio_handle = self.stdio_log.open("wb")
        self.proc = subprocess.Popen(
            [CADDY_BIN, "run", "--config", str(self.derived_path), "--adapter", "caddyfile"],
            stdout=self._stdio_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(self.tmp),
        )
        self._await_listening()

    def _await_listening(self) -> None:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    "throwaway caddy exited during startup (rc=%s):\n%s"
                    % (self.proc.returncode, self._read_stdio())
                )
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.4)
            try:
                s.connect(("127.0.0.1", self.proxy_port))
                return
            except OSError:
                time.sleep(0.15)
            finally:
                s.close()
        raise AssertionError(
            "throwaway caddy never listened on 127.0.0.1:%d:\n%s" % (self.proxy_port, self._read_stdio())
        )

    def _read_stdio(self) -> str:
        try:
            return self.stdio_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "<no stdio captured>"

    def stop(self) -> None:
        # Only ever the process THIS harness started, by handle.  Never taskkill /IM.
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.proc = None
        if self._stdio_handle is not None:
            self._stdio_handle.close()
            self._stdio_handle = None
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- client -------------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        port: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", port or self.proxy_port, timeout=15)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def log_blob(self) -> str:
        """Every byte Caddy wrote: both rolling logs plus its stdout/stderr."""
        parts = []
        for p in sorted(self.logdir.glob("*")) + [self.stdio_log]:
            if p.is_file():
                parts.append(f"### {p.name}\n" + p.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)

    def await_log_marker(self, marker: str, timeout: float = 15.0) -> str:
        """Flush barrier: poll the logs until a distinctive path shows up in them."""
        deadline = time.monotonic() + timeout
        blob = ""
        while time.monotonic() < deadline:
            blob = self.log_blob()
            if marker in blob:
                return blob
            time.sleep(0.2)
        raise AssertionError(
            f"access log never recorded the marker {marker!r} -- the redaction assertions "
            f"below would be vacuous. Logs so far:\n{blob[:4000]}"
        )


REPORT: List[str] = []


def _report(line: str) -> None:
    REPORT.append(line)


def _redact(text: str) -> str:
    return (
        text.replace(DUMMY_TOKEN, "<dummy>")
        .replace(DUMMY_HEARTH_KEY, "<dummy-hearth-key>")
        .replace(CLIENT_EVIL_BEARER, "<evil-client-bearer>")
    )


# --------------------------------------------------------------------------------------
# static assertions on the real file (no binary needed)
# --------------------------------------------------------------------------------------


class RealCaddyfileShapeTests(unittest.TestCase):
    """Properties of the committed Caddyfile that the live proof cannot observe."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CADDYFILE.read_text(encoding="utf-8")
        # The :8083 block runs to end of file; slice it so assertions are scoped.
        start = cls.text.index("\n:8083 {")
        cls.block = cls.text[start:]
        # Directives only: the block's comments legitimately QUOTE the shapes some of
        # these assertions ban, so an assertion about what Caddy executes must not read
        # the prose explaining why it does not.
        cls.directives = "\n".join(
            line for line in cls.block.splitlines() if not line.lstrip().startswith("#")
        )

    def test_site_block_exists_on_8083(self) -> None:
        self.assertRegex(self.text, r"(?m)^:8083 \{$")
        self.assertRegex(self.text, r"(?m)^:8711 \{$", "the Funnel block must survive untouched")

    def test_upstream_is_loopback_llama_swap(self) -> None:
        self.assertIn("reverse_proxy 127.0.0.1:8081", self.directives)
        self.assertIn("header_up Host 127.0.0.1:8081", self.directives)

    def test_bearer_uses_a_runtime_env_placeholder_not_a_literal(self) -> None:
        self.assertIn('header_up Authorization "Bearer {env.%s}"' % AUTH_ENV, self.directives)
        # {$VAR} is the Caddyfile PREPROCESSOR form: it would bake the token into the
        # adapted JSON config.  {env.VAR} is resolved per request and never persisted.
        self.assertNotIn("{$", self.text, "load-time env substitution would materialise the token")

    def test_no_header_delete_that_would_strip_the_injected_bearer(self) -> None:
        # Caddy's header handler applies deletes AFTER sets, so `header_up -Authorization`
        # next to `header_up Authorization ...` silently removes the credential.  The set
        # alone already replaces whatever the client sent (proved live below).
        self.assertNotIn("header_up -Authorization", self.directives)
        self.assertIn("header_up Authorization", self.directives)  # non-vacuity

    def test_fails_closed_without_the_credential(self) -> None:
        self.assertIn('@noauth expression {env.%s} == ""' % AUTH_ENV, self.directives)
        self.assertRegex(self.directives, r"respond @noauth .*503")

    def test_body_size_is_capped(self) -> None:
        self.assertRegex(self.directives, r"request_body \{\s*\n\s*max_size 32MiB")

    def test_access_log_redacts_the_same_three_headers_as_the_funnel_block(self) -> None:
        for field in ("X-Hearth-Key", "Authorization", "Cookie"):
            self.assertIn(f"request>headers>{field} delete", self.directives)
        self.assertIn("caddy-vm-proxy-access.log", self.directives)

    def test_auth_env_name_matches_the_backends_registry(self) -> None:
        backends = (REPO_ROOT / "hearth" / "etc" / "backends.toml").read_text(encoding="utf-8")
        self.assertRegex(
            backends,
            r'(?m)^auth_env\s*=\s*"%s"' % AUTH_ENV,
            "the proxy would inject a bearer the rung does not use",
        )

    def test_launcher_sources_the_gateway_environment(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("with-gateway-env.cmd", launcher)
        # Never `echo`/`type` the secret file, never `set` with no argument.
        self.assertNotRegex(launcher, r"(?im)^\s*(echo|type)\s+.*gateway\.cmd")
        self.assertNotRegex(launcher, r"(?im)^\s*set\s*$")


# --------------------------------------------------------------------------------------
# live proof -- credential present
# --------------------------------------------------------------------------------------


@unittest.skipUnless(CADDY_BIN, "caddy binary not present at the launcher's path")
class VmProxyWithCredentialTests(unittest.TestCase):
    harness: _Harness

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = _Harness(auth_env_value=DUMMY_TOKEN)
        cls.harness.start()
        _report(
            "phase 1 (credential present): caddy pid=%d proxy=127.0.0.1:%d funnel-clone=127.0.0.1:%d "
            "fake-upstream=127.0.0.1:%d dead-upstream=127.0.0.1:%d"
            % (
                cls.harness.proc.pid,
                cls.harness.proxy_port,
                cls.harness.funnel_port,
                cls.harness.upstream_port,
                cls.harness.dead_port,
            )
        )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.harness.stop()
            cls._dump_artifacts()
        finally:
            cls.harness.cleanup()

    @classmethod
    def _dump_artifacts(cls) -> None:
        """Optional: persist the derived config and a log-redaction grep for the record."""
        dest = os.environ.get("B06_PROOF_ARTIFACTS")
        if not dest:
            return
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        (out / "derived-caddyfile.txt").write_text(
            "# Generated by hearth/tests/proxy/test_vm_proxy_caddyfile.py from the real\n"
            "# hearth/etc/caddy/Caddyfile: listeners and upstreams moved to ephemeral\n"
            "# loopback ports, logs moved to a temp dir, admin API off. The /v1 narrowing,\n"
            "# the 404 catch-all, the bearer injection, the 503 guard, the body cap and the\n"
            "# header redaction are byte-identical to the committed file.\n"
            "# The credential is a placeholder here and at runtime: {env.%s}.\n\n" % AUTH_ENV
            + _redact(cls.harness.derived_path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )
        blob = cls.harness.log_blob()
        lines = [
            "grep of every byte the throwaway Caddy wrote, phase 1 (credential present).",
            "The dummy values below never existed outside the test process.",
            "",
            "files: " + ", ".join(sorted(p.name for p in cls.harness.logdir.glob("*")))
            + ", caddy-stdio.log",
            "total bytes: %d" % len(blob),
            "",
            "occurrences of the INJECTED upstream bearer <dummy>:            %d"
            % blob.count(DUMMY_TOKEN),
            "occurrences of the CLIENT-supplied bearer <evil-client-bearer>: %d"
            % blob.count(CLIENT_EVIL_BEARER),
            "occurrences of the CLIENT-supplied X-Hearth-Key value:          %d"
            % blob.count(DUMMY_HEARTH_KEY),
            "occurrences of the header NAME 'Authorization' (field deleted): %d"
            % blob.count("Authorization"),
            "occurrences of the header NAME 'X-Hearth-Key' (field deleted):  %d"
            % blob.count("X-Hearth-Key"),
            "",
            "non-vacuity -- the logger did record those same requests:",
        ]
        samples = [ln for ln in blob.splitlines() if "/v1/models" in ln or "/marker-" in ln]
        for line in samples[:6]:
            lines.append("  " + _redact(line)[:500])
        lines.append("  ... %d logged lines matched in total" % len(samples))
        (out / "redacted-log-grep.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- allowed path -------------------------------------------------------------

    def test_v1_models_is_forwarded_with_the_injected_bearer(self) -> None:
        before = self.harness.recorder.count()
        status, body = self.harness.request("GET", "/v1/models")
        self.assertEqual(200, status)
        self.assertEqual("qwen3-30b-a3b", json.loads(body)["data"][0]["id"])
        seen = self.harness.recorder.snapshot()[before:]
        self.assertEqual(1, len(seen))
        self.assertEqual("/v1/models", seen[0]["path"])
        self.assertEqual(f"Bearer {DUMMY_TOKEN}", seen[0]["headers"]["authorization"])
        self.assertEqual(f"127.0.0.1:{self.harness.upstream_port}", seen[0]["headers"]["host"])
        _report(
            "GET /v1/models -> %d; upstream saw path=%s authorization=%s host=%s"
            % (status, seen[0]["path"], _redact(seen[0]["headers"]["authorization"]), seen[0]["headers"]["host"])
        )

    def test_client_supplied_bearer_is_replaced_not_forwarded(self) -> None:
        before = self.harness.recorder.count()
        status, _ = self.harness.request(
            "GET",
            "/v1/models",
            headers={
                "Authorization": f"Bearer {CLIENT_EVIL_BEARER}",
                "X-Hearth-Key": DUMMY_HEARTH_KEY,
            },
        )
        self.assertEqual(200, status)
        seen = self.harness.recorder.snapshot()[before:]
        self.assertEqual(1, len(seen))
        self.assertEqual(f"Bearer {DUMMY_TOKEN}", seen[0]["headers"]["authorization"])
        # Nothing the client sent as a credential may appear anywhere upstream.
        flat = json.dumps(seen[0])
        self.assertNotIn(CLIENT_EVIL_BEARER, flat)
        _report(
            "GET /v1/models with client Authorization=Bearer <evil-client-bearer> -> %d; "
            "upstream saw authorization=%s (client value absent upstream)"
            % (status, _redact(seen[0]["headers"]["authorization"]))
        )

    def test_post_chat_completions_forwards_the_body(self) -> None:
        payload = json.dumps(
            {"model": "qwen3-30b-a3b", "messages": [{"role": "user", "content": "b06 proof"}]}
        ).encode("utf-8")
        before = self.harness.recorder.count()
        status, body = self.harness.request(
            "POST",
            "/v1/chat/completions",
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
            body=payload,
        )
        self.assertEqual(200, status)
        self.assertEqual(len(payload), json.loads(body)["echo_bytes"])
        seen = self.harness.recorder.snapshot()[before:]
        self.assertEqual(1, len(seen))
        self.assertEqual("/v1/chat/completions", seen[0]["path"])
        self.assertEqual(payload.decode("utf-8"), seen[0]["body"])
        self.assertEqual(f"Bearer {DUMMY_TOKEN}", seen[0]["headers"]["authorization"])
        _report(
            "POST /v1/chat/completions (%d body bytes) -> %d; upstream body byte-identical, "
            "authorization=%s" % (len(payload), status, _redact(seen[0]["headers"]["authorization"]))
        )

    # -- denied paths -------------------------------------------------------------

    def test_every_non_v1_path_404s_without_reaching_the_upstream(self) -> None:
        for path in FORBIDDEN_PATHS:
            with self.subTest(path=path):
                before = self.harness.recorder.count()
                status, body = self.harness.request("GET", path)
                self.assertEqual(404, status, f"{path} must not be served")
                self.assertNotIn(b"UPSTREAM-REACHED", body)
                self.assertEqual(
                    before,
                    self.harness.recorder.count(),
                    f"{path} reached the upstream -- the /v1 narrowing leaks",
                )
                _report("GET %-42s -> %d, upstream requests unchanged (%d)" % (path, status, before))

    def test_admin_unload_is_unreachable_by_every_method(self) -> None:
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                before = self.harness.recorder.count()
                status, _ = self.harness.request(
                    method,
                    "/api/models/unload/qwen3-30b-a3b",
                    headers={"Content-Length": "0"} if method == "POST" else None,
                    body=b"" if method == "POST" else None,
                )
                self.assertEqual(404, status)
                self.assertEqual(before, self.harness.recorder.count())
                _report("%-4s /api/models/unload/qwen3-30b-a3b -> %d, upstream untouched" % (method, status))

    # -- the two site blocks share a process without cross-wiring -----------------

    def test_funnel_block_routes_v1_to_the_gate_and_never_to_llama_swap(self) -> None:
        # ADR-0046: /v1 on the Funnel is the friend gate's, which has ITS OWN upstream (here: a closed
        # port, so >= 500). It must never dial the inference upstream and never carry a stamped bearer.
        before = self.harness.recorder.count()
        status, _ = self.harness.request("GET", "/v1/models", port=self.harness.funnel_port)
        self.assertGreaterEqual(status, 500, "the :8711 /v1 route must dial the friend gate's own (here: closed) upstream")
        self.assertEqual(before, self.harness.recorder.count(), "/v1 on the Funnel reached the inference upstream -- the blocks are cross-wired")
        status, _ = self.harness.request("GET", "/v2/anything", port=self.harness.funnel_port)
        self.assertEqual(404, status, "only /v1/* and /mcp* exist on the Funnel block")

        status_mcp, _ = self.harness.request("GET", "/mcp", port=self.harness.funnel_port)
        self.assertGreaterEqual(status_mcp, 500, "/mcp must still dial its OWN (here: closed) upstream")
        self.assertEqual(
            before,
            self.harness.recorder.count(),
            "/mcp on the Funnel block reached the inference upstream -- the blocks are cross-wired",
        )
        _report(
            "funnel-clone: GET /v1/models -> %d (not proxied); GET /mcp -> %d (dials its own upstream); "
            "inference upstream untouched" % (status, status_mcp)
        )

    # -- logs ---------------------------------------------------------------------

    def test_logs_record_the_requests_but_never_the_credentials(self) -> None:
        marker = "/marker-" + uuid.uuid4().hex
        self.harness.request(
            "GET",
            marker,
            headers={
                "Authorization": f"Bearer {CLIENT_EVIL_BEARER}",
                "X-Hearth-Key": DUMMY_HEARTH_KEY,
                "Cookie": "session=" + DUMMY_HEARTH_KEY,
            },
        )
        blob = self.harness.await_log_marker(marker)

        # Non-vacuity: the logger really is writing these requests.
        self.assertIn(marker, blob)
        self.assertIn("/v1/models", blob)

        for secret, label in (
            (DUMMY_TOKEN, "the injected upstream bearer"),
            (CLIENT_EVIL_BEARER, "a client-supplied bearer"),
            (DUMMY_HEARTH_KEY, "a client-supplied X-Hearth-Key"),
        ):
            self.assertNotIn(secret, blob, f"{label} leaked into a Caddy log")

        _report(
            "log redaction: %d bytes across %s; marker + /v1/models present; "
            "injected bearer, client bearer and X-Hearth-Key all absent"
            % (len(blob), ", ".join(sorted(p.name for p in self.harness.logdir.glob("*"))))
        )


# --------------------------------------------------------------------------------------
# live proof -- credential missing (fail closed)
# --------------------------------------------------------------------------------------


@unittest.skipUnless(CADDY_BIN, "caddy binary not present at the launcher's path")
class VmProxyWithoutCredentialTests(unittest.TestCase):
    """A launcher started without the token must not expose the upstream at all."""

    def _assert_fails_closed(self, harness: _Harness, label: str) -> None:
        for path in ("/v1/models", "/v1/chat/completions", "/", "/api/models"):
            with self.subTest(path=path, env=label):
                status, _ = harness.request("GET", path)
                self.assertEqual(
                    503, status, f"{label}: {path} must be refused by the proxy, not passed through"
                )
        self.assertEqual(0, harness.recorder.count(), f"{label}: the upstream was dialled anyway")
        _report("%s: /v1/models, /v1/chat/completions, /, /api/models all -> 503; upstream never dialled" % label)

    def test_empty_env_var_fails_closed(self) -> None:
        harness = _Harness(auth_env_value="")
        try:
            harness.start()
            self._assert_fails_closed(harness, "%s='' (set but empty)" % AUTH_ENV)
        finally:
            harness.stop()
            harness.cleanup()

    def test_absent_env_var_fails_closed(self) -> None:
        harness = _Harness(auth_env_value=None)
        try:
            harness.start()
            self._assert_fails_closed(harness, "%s unset" % AUTH_ENV)
        finally:
            harness.stop()
            harness.cleanup()


def tearDownModule() -> None:
    if REPORT and os.environ.get("B06_PROOF_REPORT"):
        Path(os.environ["B06_PROOF_REPORT"]).write_text(
            _redact("\n".join(REPORT)) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
