"""Unit tests for Turn-Boundary Mamba-2 State Checkpointing in OpenAIServerAdapter."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from hearth.adapters.openai_server import (
    TURN_CHECKPOINT_ACK,
    OpenAIServerAdapter,
    build_turn_checkpointed_messages,
    is_mamba_hybrid_target,
    split_shared_context_pack,
)


class TestOpenAIServerTurnCheckpointing(unittest.TestCase):
    def test_is_mamba_hybrid_target_detection(self) -> None:
        self.assertTrue(is_mamba_hybrid_target(model_name="nemotron-3.5-bolt-74b-a9b"))
        self.assertTrue(is_mamba_hybrid_target(model_name="NVIDIA-Nemotron-3.5-Bolt-74B-A9B"))
        self.assertTrue(is_mamba_hybrid_target(backend_id="omen-nemotron-74b"))
        self.assertTrue(is_mamba_hybrid_target(backend_id="omen-vllm"))
        self.assertFalse(is_mamba_hybrid_target(model_name="qwen2.5-27b-instruct", backend_id="am4-vllm"))

    def test_split_repository_source_pack(self) -> None:
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "FILE: hearth/localwork/service.py\n"
            "def dispatch(): pass\n"
            "=== END REPOSITORY SOURCE PACK ===\n\n"
            "Task: Audit dispatch invariants and edge cases."
        )
        split = split_shared_context_pack(prompt)
        self.assertIsNotNone(split)
        shared_pack, task_instruction = split  # type: ignore[misc]
        self.assertTrue(shared_pack.startswith("=== BEGIN REPOSITORY SOURCE PACK ==="))
        self.assertTrue(shared_pack.endswith("=== END REPOSITORY SOURCE PACK ==="))
        self.assertEqual(task_instruction, "Task: Audit dispatch invariants and edge cases.")

    def test_split_arch_pack_and_shared_context_end(self) -> None:
        arch_prompt = (
            "[ARCH_PACK]\n"
            "module Router: ports=[18090, 18091]\n"
            "[/ARCH_PACK]\n"
            "Verify router maxconn settings."
        )
        split_arch = split_shared_context_pack(arch_prompt)
        self.assertIsNotNone(split_arch)
        self.assertEqual(split_arch[0], "[ARCH_PACK]\nmodule Router: ports=[18090, 18091]\n[/ARCH_PACK]")
        self.assertEqual(split_arch[1], "Verify router maxconn settings.")

        html_marker_prompt = (
            "Shared AST Slice Header\n"
            "<!-- SHARED_CONTEXT_END -->\n"
            "Subtask 4: Check token ceiling."
        )
        split_marker = split_shared_context_pack(html_marker_prompt)
        self.assertIsNotNone(split_marker)
        self.assertEqual(split_marker[0], "Shared AST Slice Header\n<!-- SHARED_CONTEXT_END -->")
        self.assertEqual(split_marker[1], "Subtask 4: Check token ceiling.")

    def test_build_payload_splits_source_pack_for_nemotron(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "def foo(x): return x + 1\n"
            "=== END REPOSITORY SOURCE PACK ===\n"
            "Explain foo(x)."
        )
        payload = adapter.build_payload(
            prompt,
            system="You are a code auditor.",
            max_tokens=160,
            temperature=0.0,
        )
        self.assertTrue(payload.get("cache_prompt"))
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "You are a code auditor."},
                {
                    "role": "user",
                    "content": "=== BEGIN REPOSITORY SOURCE PACK ===\ndef foo(x): return x + 1\n=== END REPOSITORY SOURCE PACK ===",
                },
                {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                {"role": "user", "content": "Explain foo(x)."},
            ],
        )

    def test_build_payload_splits_arch_pack_without_system_prompt(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-vllm",
            api_key="test-token",
        )
        prompt = "[ARCH_PACK]\nclass Engine: pass\n[/ARCH_PACK]\nList methods on Engine."
        payload = adapter.build_payload(prompt, max_tokens=128)
        self.assertTrue(payload.get("cache_prompt"))
        self.assertEqual(
            payload["messages"],
            [
                {"role": "user", "content": "[ARCH_PACK]\nclass Engine: pass\n[/ARCH_PACK]"},
                {"role": "assistant", "content": TURN_CHECKPOINT_ACK},
                {"role": "user", "content": "List methods on Engine."},
            ],
        )

    def test_single_block_prompt_left_untouched(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = "Write a single-paragraph summary of PCIe peer-to-peer transfers."
        payload = adapter.build_payload(prompt, system="System instructions.", max_tokens=100)
        self.assertNotIn("cache_prompt", payload)
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "System instructions."},
                {"role": "user", "content": prompt},
            ],
        )

    def test_non_mamba_model_leaves_source_pack_single_turn(self) -> None:
        messages, cache_flag = build_turn_checkpointed_messages(
            "=== BEGIN REPOSITORY SOURCE PACK ===\ncode\n=== END REPOSITORY SOURCE PACK ===\nAudit.",
            system_prompt="System",
            model_name="qwen2.5-27b-instruct",
            backend_id="am4-vllm",
        )
        self.assertFalse(cache_flag)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

    def test_generate_wire_and_response_parsing(self) -> None:
        adapter = OpenAIServerAdapter(
            endpoint="http://127.0.0.1:18091",
            model="nemotron-3.5-bolt-74b-a9b",
            backend_id="omen-nemotron-74b",
            api_key="test-token",
        )
        prompt = (
            "=== BEGIN REPOSITORY SOURCE PACK ===\n"
            "def bar(): return 42\n"
            "=== END REPOSITORY SOURCE PACK ===\n"
            "What does bar return?"
        )
        fake_response = {
            "id": "chatcmpl-test",
            "model": "nemotron-3.5-bolt-74b-a9b",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "bar returns 42."},
                }
            ],
            "usage": {
                "prompt_tokens": 2400,
                "completion_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 2200},
            },
            "timings": {
                "cache_n": 2200,
                "prompt_n": 200,
                "predicted_n": 12,
            },
        }

        class _FakeHTTPResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with patch("urllib.request.urlopen") as mocked_urlopen:
            mocked_urlopen.return_value = _FakeHTTPResponse(
                json.dumps(fake_response).encode("utf-8")
            )
            res = adapter.generate(prompt, max_tokens=64, temperature=0.0)

        self.assertTrue(res["ok"])
        self.assertTrue(res["turn_checkpointed"])
        self.assertEqual(res["cached_tokens"], 2200)
        self.assertEqual(res["prompt_eval_tokens"], 200)
        self.assertTrue(res["wire_request"]["cache_prompt"])
        self.assertEqual(len(res["wire_request"]["messages"]), 3)
        self.assertEqual(res["wire_request"]["messages"][1]["content"], TURN_CHECKPOINT_ACK)


if __name__ == "__main__":
    unittest.main()
