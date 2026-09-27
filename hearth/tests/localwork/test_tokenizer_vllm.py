"""Exact token counting must speak the serving engine's dialect.

Earned 2026-09-27 on omen-linux: the first Linux submit_local_work failed with
"exact tokenizer endpoint refused: HTTP Error 404" because the counter assumed
llama.cpp's /apply-template + /tokenize{content}; vLLM renders the chat template
inside POST /tokenize{messages} and has no /apply-template.
"""
import io
import json
import unittest
from unittest import mock

from hearth.localwork.service import LocalWorkService
from hearth.toolsurface.backends import Backend


def _backend(engine):
    return Backend(name="b", endpoint="http://127.0.0.1:1", api="openai", auth_env=None,
                   models=["m"], tags=[], settings={"engine": engine} if engine else {})


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class VllmTokenizerTests(unittest.TestCase):
    def test_vllm_engine_posts_messages_to_tokenize_only(self) -> None:
        calls = []
        def fake_urlopen(req, timeout=30):
            calls.append((req.full_url, json.loads(req.data)))
            return _Resp(json.dumps({"count": 7, "tokens": [1, 2, 3, 4, 5, 6, 7]}).encode())
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            n = LocalWorkService._server_token_count(_backend("vllm"), "m", "hello")
        self.assertEqual(n, 7)
        self.assertEqual([c[0] for c in calls], ["http://127.0.0.1:1/tokenize"])
        self.assertIn("messages", calls[0][1]); self.assertNotIn("content", calls[0][1])

    def test_llama_engine_keeps_apply_template_then_tokenize(self) -> None:
        calls = []
        def fake_urlopen(req, timeout=30):
            calls.append(req.full_url.rsplit("/", 1)[-1])
            body = {"prompt": "<rendered>"} if calls[-1] == "apply-template" else {"tokens": [1, 2]}
            return _Resp(json.dumps(body).encode())
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            n = LocalWorkService._server_token_count(_backend("llama.cpp"), "m", "hello")
        self.assertEqual((n, calls), (2, ["apply-template", "tokenize"]))


if __name__ == "__main__":
    unittest.main()
