"""The friend gate, end to end against a fake llama-swap (httpx MockTransport): every rule in the plan.

    auth (401/403) -> eligibility (>=64k resident, 503 off hours) -> per-model budget refusal ->
    per-key / global concurrency (429 + Retry-After) -> daily budget -> forward (JSON and SSE
    pass-through, bearer injected, friend's knobs stripped) -> one usage row per request ->
    admin paths never reachable -> bearer never logged.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from hearth.friendgate.app import GateConfig, build_app
from hearth.friendgate.budget import slot_context_from_yaml
from hearth.friendgate.keys import KeyStore

YAML = '''
models:
  "qwen3-30b-a3b":
    cmd: >
      llama-server.exe -m x.gguf -c 131072 -np 8 --port ${PORT}
  "qwen38-27b-mtp":
    cmd: >
      llama-server.exe -m y.gguf
      -c 262144 -np 2 --kv-unified -ub 1024
      --port ${PORT}
  "qwen14b-night":
    cmd: >
      llama-server.exe -c 16384 -np 1
  groups:
    guest:
      swap: true
'''

TOOL_CALL = {"id": "cc", "object": "chat.completion", "model": "qwen38-27b-mtp",
             "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "write_note", "arguments": json.dumps({"text": "ok"})}}]}}],
             "usage": {"prompt_tokens": 120, "completion_tokens": 9, "total_tokens": 129}}
SSE = (b'data: {"id":"cc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":"Hal"}}]}\n\n'
       b'data: {"id":"cc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"lo"}}]}\n\n'
       b'data: {"id":"cc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":77,"completion_tokens":2}}\n\n'
       b'data: [DONE]\n\n')


class Upstream:
    """Records what llama-swap would have seen."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.running = [{"model": "qwen38-27b-mtp", "state": "ready"}, {"model": "qwen14b-night", "state": "ready"}]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/running":
            return httpx.Response(200, json={"running": self.running})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if body.get("stream"):
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE)
            return httpx.Response(200, json=TOOL_CALL)
        return httpx.Response(500, json={"error": "unexpected"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OMEN_ARC_TOKEN", "rung-secret-token")
    (tmp_path / "shape.yaml").write_text(YAML, encoding="utf-8")
    up = Upstream()
    cfg = GateConfig(upstream="http://llama-swap.test", keys_path=tmp_path / "keys.json", usage_path=tmp_path / "usage.ndjson",
                     yaml_paths=[tmp_path / "shape.yaml"], transport=httpx.MockTransport(up.handler), running_fetch=lambda: up.running)
    store = KeyStore(cfg.keys_path)
    rec, secret = store.mint("klaus", "Klaus", tokens_per_day=100_000)
    app = build_app(cfg)
    with TestClient(app) as client:
        yield client, up, store, rec, secret, cfg


def hdr(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def chat(model="qwen38-27b-mtp", **extra):
    return {"model": model, "messages": [{"role": "user", "content": "Call write_note with ok."}], "max_tokens": 64, **extra}


def test_slot_context_is_parsed_from_the_yaml():
    assert slot_context_from_yaml(YAML) == {"qwen3-30b-a3b": 16384, "qwen38-27b-mtp": 131072, "qwen14b-night": 16384}


def test_auth_unknown_revoked_and_expired(world):
    client, up, store, rec, secret, cfg = world
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers=hdr("fg_nope")).status_code == 401
    assert client.get("/v1/models", headers=hdr(secret)).status_code == 200
    store.revoke(rec.key_id)
    r = client.get("/v1/models", headers=hdr(secret))
    assert r.status_code == 403 and r.json()["error"]["code"] == "revoked"
    old, old_secret = store.mint("old", expires_days=-1)
    assert client.get("/v1/models", headers=hdr(old_secret)).json()["error"]["code"] == "expired"


def test_models_lists_only_resident_entries_with_enough_context(world):
    client, up, store, rec, secret, cfg = world
    r = client.get("/v1/models", headers=hdr(secret))
    assert [m["id"] for m in r.json()["data"]] == ["qwen38-27b-mtp"]  # the 14B (16k) is resident but ineligible
    assert r.json()["data"][0]["context_window"] == 65536 and r.json()["data"][0]["slot_context"] == 131072
    up.running = [{"model": "qwen3-30b-a3b", "state": "ready"}]  # day shape: 8 x 16k
    r = client.get("/v1/models", headers=hdr(secret))
    assert r.status_code == 503 and r.json()["error"]["code"] == "off_hours" and r.headers["retry-after"] == "1800"
    assert client.post("/v1/chat/completions", headers=hdr(secret), json=chat()).status_code == 503
    assert client.get("/healthz").json()["eligible_models"] == {}


def test_budget_refusal_before_anything_reaches_the_rung(world):
    client, up, store, rec, secret, cfg = world
    huge = chat(max_tokens=1000)
    huge["messages"] = [{"role": "user", "content": "x" * 400_000}]  # ~125k tokens > 65k key limit
    r = client.post("/v1/chat/completions", headers=hdr(secret), json=huge)
    assert r.status_code == 400
    e = r.json()["error"]
    assert e["code"] == "payload_over_budget_for_model" and e["limit_tokens"] == 65536 and e["estimated_prompt_tokens"] > 100_000
    assert not [q for q in up.requests if q.url.path.startswith("/v1")]
    r = client.post("/v1/chat/completions", headers=hdr(secret), json=chat(model="qwen14b-night"))
    assert r.status_code == 404 and r.json()["error"]["available"] == ["qwen38-27b-mtp"]


def test_forward_injects_the_bearer_strips_knobs_and_passes_tool_calls_through(world):
    client, up, store, rec, secret, cfg = world
    r = client.post("/v1/chat/completions", headers=hdr(secret), json=chat(**{"speculative.n_max": 8, "slot_id": 1, "chat_template_kwargs": {"enable_thinking": False}, "temperature": 0.2}))
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "write_note"
    sent = [q for q in up.requests if q.url.path == "/v1/chat/completions"][-1]
    assert sent.headers["authorization"] == "Bearer rung-secret-token"  # the friend's key never goes upstream; the rung's never comes down
    body = json.loads(sent.content)
    assert "speculative.n_max" not in body and "slot_id" not in body and body["temperature"] == 0.2
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # the friend asked for it off; also the default
    client.post("/v1/chat/completions", headers=hdr(secret), json=chat())
    assert json.loads(up.requests[-1].content)["chat_template_kwargs"] == {"enable_thinking": False}
    client.post("/v1/chat/completions", headers=hdr(secret), json=chat(chat_template_kwargs={"enable_thinking": True}))
    assert json.loads(up.requests[-1].content)["chat_template_kwargs"] == {"enable_thinking": True}  # explicit opt-in survives
    rows = [json.loads(l) for l in cfg.usage_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["irc_account"] == "klaus" and rows[-1]["tokens_in"] == 120 and rows[-1]["tokens_out"] == 9 and rows[-1]["status"] == 200
    assert rows[-1]["principal"] == {"type": "irc_account", "id": "klaus"} and rows[-1]["source"]["adapter"] == "friend-gate"


def test_streaming_passes_sse_through_untouched_and_records_usage(world):
    client, up, store, rec, secret, cfg = world
    with client.stream("POST", "/v1/chat/completions", headers=hdr(secret), json=chat(stream=True)) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        raw = b"".join(r.iter_bytes())
    assert raw == SSE
    sent = json.loads([q for q in up.requests if q.url.path == "/v1/chat/completions"][-1].content)
    assert sent["stream"] is True and sent["stream_options"]["include_usage"] is True
    rows = [json.loads(l) for l in cfg.usage_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["stream"] is True and rows[-1]["tokens_in"] == 77 and rows[-1]["tokens_out"] == 2


def test_daily_budget_and_concurrency_limits(world):
    client, up, store, rec, secret, cfg = world
    # daily budget: pre-load today's usage past the key's limit
    gate = client.app.state.gate
    gate.usage.record(request_id="x", key_id=rec.key_id, irc_account="klaus", model="m", stream=False, status=200, tokens_in=99_000, tokens_out=2_000, duration_ms=1)
    r = client.post("/v1/chat/completions", headers=hdr(secret), json=chat())
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_budget_exhausted" and r.headers["retry-after"] == "3600"
    # concurrency: a second friend while the global slot is held
    other, other_secret = store.mint("sam")
    gate._total._value = 0  # simulate an in-flight request holding the one shared slot
    r = client.post("/v1/chat/completions", headers=hdr(other_secret), json=chat())
    assert r.status_code == 429 and r.json()["error"]["code"] == "busy" and r.headers["retry-after"] == "30"
    gate._total._value = 1
    assert client.post("/v1/chat/completions", headers=hdr(other_secret), json=chat()).status_code == 200


def test_gate_refuses_when_not_armed(world, monkeypatch):
    client, up, store, rec, secret, cfg = world
    monkeypatch.delenv("OMEN_ARC_TOKEN")
    r = client.post("/v1/chat/completions", headers=hdr(secret), json=chat())
    assert r.status_code == 503 and r.json()["error"]["code"] == "gate_unarmed"


def test_admin_and_other_paths_do_not_exist_here(world):
    client, up, store, rec, secret, cfg = world
    for path in ("/running", "/unload", "/upstream/qwen38-27b-mtp/slots", "/api/models/unload", "/ui", "/logs", "/metrics", "/v1/completions", "/v1/embeddings"):
        assert client.get(path, headers=hdr(secret)).status_code == 404, path
        assert client.post(path, headers=hdr(secret)).status_code == 404, path
    assert not [q for q in up.requests if q.url.path not in ("/running", "/v1/chat/completions")]


def test_no_bearer_ever_reaches_a_log_line(world, caplog):
    client, up, store, rec, secret, cfg = world
    with caplog.at_level(logging.DEBUG):
        client.post("/v1/chat/completions", headers=hdr(secret), json=chat())
        client.get("/v1/models", headers=hdr("fg_wrong-key-value"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert secret not in text and "rung-secret-token" not in text and "fg_wrong-key-value" not in text
    assert secret not in cfg.usage_path.read_text(encoding="utf-8")
    assert "rung-secret-token" not in cfg.keys_path.read_text(encoding="utf-8")


def test_friendctl_round_trip(tmp_path, monkeypatch, capsys):
    from hearth.friendgate import friendctl
    monkeypatch.setenv("FRIENDGATE_VAR", str(tmp_path))
    assert friendctl.main(["mint", "--account", "klaus", "--display", "Klaus", "--expires-days", "30"]) == 0
    out = capsys.readouterr().out
    secret = out.strip().splitlines()[-1]
    assert secret.startswith("fg_") and "klaus-" in out
    store = KeyStore(tmp_path / "keys.json")
    assert store.lookup(secret).irc_account == "klaus" and store.lookup(secret).status() == "active"
    assert friendctl.main(["list"]) == 0 and "klaus" in capsys.readouterr().out
    key_id = store.lookup(secret).key_id
    assert friendctl.main(["revoke", key_id]) == 0 and store.lookup(secret).status() == "revoked"
    assert friendctl.main(["usage"]) == 0 and "no usage yet" in capsys.readouterr().out


def test_bad_bodies_are_400_never_500(world):
    client, up, store, rec, secret, cfg = world
    h = {**hdr(secret), "Content-Type": "application/json"}
    assert client.post("/v1/chat/completions", headers=h, content="{\"model\":\"qwen38-27b-mtp\",\"messages\":[{\"role\":\"user\",\"content\":\"Wofür\"}]}".encode("latin-1")).status_code == 400  # Latin-1 bytes, not UTF-8
    assert client.post("/v1/chat/completions", headers=h, content=b"not json").status_code == 400
    assert client.post("/v1/chat/completions", headers=h, content=b'{"model":"qwen38-27b-mtp"}').status_code == 400  # no messages
    assert client.post("/v1/chat/completions", headers=h, content=b'[1,2]').status_code == 400
    assert not [q for q in up.requests if q.url.path == "/v1/chat/completions"]
