#!/usr/bin/env python3
"""Compile a no-repack, manual-DQ_FULL Qwen3.8 Flash expert bundle.

NPU-21 is an /rnd compile-only admission probe.  It presents ten independent
experts directly to the NPU plugin: thirty runtime i4 weight Parameters,
thirty runtime f16 per-output scale Parameters, one f16 activation, and ten
f16 route scores.  Each projection performs i4-to-f16 conversion, f16 MatMul,
and f16 post-scaling.  The routed nonlinear, merge, score, and reduction path
also remains f16; this is the same no-repack topology and ordered ABI as
NPU-20 with only the internal f32 precision islands removed.  The compiler's
separate dynamic-quantization pass is an independent switch.  The probe never
creates an infer request.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


PROBE = "NPU-21"
TOP_K = 10
HIDDEN = 2560
INTERMEDIATE = 640
PROJECTIONS = ("gate", "up", "down")

WEIGHT_PARAMETER_COUNT = TOP_K * len(PROJECTIONS)
SCALE_PARAMETER_COUNT = TOP_K * len(PROJECTIONS)
SCORE_PARAMETER_COUNT = TOP_K
TOTAL_PARAMETER_COUNT = 1 + WEIGHT_PARAMETER_COUNT + SCALE_PARAMETER_COUNT + SCORE_PARAMETER_COUNT

PACKED_WEIGHT_BYTES_PER_EXPERT = 3 * (HIDDEN * INTERMEDIATE // 2)
SCALE_BYTES_PER_EXPERT = (INTERMEDIATE + INTERMEDIATE + HIDDEN) * 2
PACKED_WEIGHT_BYTES = TOP_K * PACKED_WEIGHT_BYTES_PER_EXPERT
SCALE_BYTES = TOP_K * SCALE_BYTES_PER_EXPERT
ACTIVATION_BYTES = HIDDEN * 2
SCORE_BYTES = TOP_K * 2
RUNTIME_INPUT_BYTES = PACKED_WEIGHT_BYTES + SCALE_BYTES + ACTIVATION_BYTES + SCORE_BYTES


class ProbeEdge(RuntimeError):
    """A first edge that closes this /rnd lap."""

    def __init__(self, stage: str, detail: str):
        super().__init__(detail)
        self.stage = stage
        self.detail = detail


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument(
        "--weight-cache-dir",
        type=Path,
        help="NPU-0 packed-weight cache; defaults to --artifact-dir",
    )
    parser.add_argument("--device", default="NPU", choices=("NPU",))
    parser.add_argument("--compiler-type", default="PLUGIN", choices=("PLUGIN",))
    parser.add_argument("--bf6-render-queue", default="unknown")
    parser.add_argument("--coresident", action="store_true")
    parser.add_argument(
        "--enable-compiler-dq",
        action="store_true",
        help="enable the NPU compiler's i4 dynamic-quantization lowering pass",
    )
    return parser.parse_args(argv)


def rerun_command(args: argparse.Namespace) -> str:
    return (
        f'& "{Path(sys.executable)}" "{Path(__file__).resolve()}" '
        f'--artifact-dir "{args.artifact_dir.resolve()}" '
        f'--receipts "{args.receipts.resolve()}" --device "{args.device}" '
        f'--compiler-type "{args.compiler_type}" '
        f'--bf6-render-queue "{args.bf6_render_queue}"'
        + (
            f' --weight-cache-dir "{args.weight_cache_dir.resolve()}"'
            if args.weight_cache_dir is not None
            else ""
        )
        + (" --coresident" if args.coresident else "")
        + (" --enable-compiler-dq" if args.enable_compiler_dq else "")
    )


def base_receipt(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ts": now_iso(),
        "probe": PROBE,
        "coresident": args.coresident,
        "bf6_render_queue": args.bf6_render_queue,
        "compiler_type_requested": args.compiler_type,
        "compiler_dynamic_quantization": args.enable_compiler_dq,
        "weight_cache_dir": str((args.weight_cache_dir or args.artifact_dir).resolve()),
        "perf_count": False,
        "log_level": "LOG_DEBUG",
        "inference_attempted": False,
    }


def make_named(node: Any, name: str):
    node.set_friendly_name(name)
    return node


def expert_prefix(index: int) -> str:
    return f"npu21.expert{index:02d}"


def abi_expert_prefix(index: int) -> str:
    return f"npu20.expert{index:02d}"


def projection_shapes(role: str) -> tuple[list[int], list[int], list[int]]:
    if role in ("gate", "up"):
        return [1, INTERMEDIATE, HIDDEN], [1, INTERMEDIATE, 1], [1, 1, INTERMEDIATE]
    if role == "down":
        return [1, HIDDEN, INTERMEDIATE], [1, HIDDEN, 1], [1, 1, HIDDEN]
    raise ValueError(f"unknown projection role: {role}")


def build_model(ov: Any, ops: Any):
    import numpy as np
    from openvino.utils.node_factory import NodeFactory

    def parameter(shape: list[int], dtype: Any, name: str):
        node = make_named(ops.parameter(shape, dtype), name)
        node.output(0).set_names({name})
        return node

    def constant(value: list[int], name: str):
        return make_named(ops.constant(np.asarray(value, dtype=np.int64)), name)

    params: list[Any] = []
    activation = parameter([1, HIDDEN], ov.Type.f16, "npu20.activation")
    params.append(activation)
    activation_3d = make_named(
        ops.reshape(
            activation,
            constant([1, 1, HIDDEN], "npu21.activation.reshape.shape"),
            False,
        ),
        "npu21.activation.reshape",
    )

    def project(left_f16: Any, role: str, prefix: str):
        weight_shape, scale_shape, output_shape = projection_shapes(role)
        abi_prefix = prefix.replace("npu21.", "npu20.", 1)
        weight = parameter(weight_shape, ov.Type.i4, f"{abi_prefix}.{role}.weight_i4")
        scale = parameter(scale_shape, ov.Type.f16, f"{abi_prefix}.{role}.scale_f16")
        params.extend((weight, scale))
        weight_f16 = make_named(ops.convert(weight, ov.Type.f16), f"{prefix}.{role}.weight_to_f16")
        matmul = make_named(
            ops.matmul(left_f16, weight_f16, False, True),
            f"{prefix}.{role}.matmul",
        )
        scale_3d = make_named(
            ops.reshape(
                scale,
                constant(output_shape, f"{prefix}.{role}.scale_shape"),
                False,
            ),
            f"{prefix}.{role}.scale_reshape",
        )
        scaled_f16 = make_named(
            ops.multiply(matmul, scale_3d),
            f"{prefix}.{role}.scale_after_matmul",
        )
        return scaled_f16

    gate_outputs: list[Any] = []
    up_outputs: list[Any] = []
    for index in range(TOP_K):
        prefix = expert_prefix(index)
        gate_outputs.append(project(activation_3d, "gate", prefix))
        up_outputs.append(project(activation_3d, "up", prefix))

    gate_concat = make_named(ops.concat(gate_outputs, 0), "npu21.gate.concat")
    up_concat = make_named(ops.concat(up_outputs, 0), "npu21.up.concat")
    gate_swish = make_named(
        NodeFactory("opset4").create("Swish", [gate_concat], {}),
        "npu21.gate.swish",
    )
    if gate_swish.get_input_size() != 1:
        raise ProbeEdge("model-build", "NPU-21 requires a one-input opset4 Swish")
    merged = make_named(ops.multiply(gate_swish, up_concat), "npu21.gate_up.merge")

    weighted_outputs: list[Any] = []
    for index in range(TOP_K):
        prefix = expert_prefix(index)
        branch = make_named(
            ops.slice(
                merged,
                constant([index], f"{prefix}.slice.start"),
                constant([index + 1], f"{prefix}.slice.stop"),
                constant([1], f"{prefix}.slice.step"),
                constant([0], f"{prefix}.slice.axis"),
            ),
            f"{prefix}.down.input_slice",
        )
        down_f16 = project(branch, "down", prefix)
        down_4d = make_named(
            ops.reshape(
                down_f16,
                constant([1, 1, 1, HIDDEN], f"{prefix}.down.output_shape"),
                False,
            ),
            f"{prefix}.down.output_reshape",
        )
        score = parameter(
            [1, 1, 1, 1],
            ov.Type.f16,
            f"{abi_expert_prefix(index)}.score_f16",
        )
        params.append(score)
        weighted_outputs.append(
            make_named(ops.multiply(down_4d, score), f"{prefix}.score_multiply")
        )

    weighted_concat = make_named(ops.concat(weighted_outputs, 0), "npu21.weighted.concat")
    reduced = make_named(
        ops.reduce_sum(
            weighted_concat,
            constant([0], "npu21.reduce.axis"),
            False,
        ),
        "npu21.weighted.reduce",
    )
    reduced.set_friendly_name("npu21.output")
    model = ov.Model([reduced], params, "npu21_direct_manual_dq_full_experts")
    model.validate_nodes_and_infer_types()
    return model



def cpu_emulation_precheck() -> dict[str, Any]:
    """Check the exact ten-branch algebra with synthetic reduced dimensions.

    This is a topology/precision sanity check only.  Logical i4 values are
    represented as int8 values constrained to [-7, 7]; it neither decodes a
    real model nor claims NPU or real-weight correctness.
    """

    import numpy as np

    seed = 210021
    hidden = 16
    intermediate = 8
    rng = np.random.default_rng(seed)
    activation = (rng.standard_normal((1, hidden)) * 0.125).astype(np.float16)
    gate_q = rng.integers(-7, 8, size=(TOP_K, intermediate, hidden), dtype=np.int8)
    up_q = rng.integers(-7, 8, size=(TOP_K, intermediate, hidden), dtype=np.int8)
    down_q = rng.integers(-7, 8, size=(TOP_K, hidden, intermediate), dtype=np.int8)
    gate_scale = rng.uniform(0.01, 0.08, size=(TOP_K, intermediate)).astype(np.float16)
    up_scale = rng.uniform(0.01, 0.08, size=(TOP_K, intermediate)).astype(np.float16)
    down_scale = rng.uniform(0.01, 0.08, size=(TOP_K, hidden)).astype(np.float16)
    scores = rng.uniform(0.01, 0.2, size=(TOP_K, 1, 1, 1)).astype(np.float16)

    def round_f16(value: Any):
        return np.asarray(value, dtype=np.float16)

    def project_one(left: Any, quant: Any, scale: Any):
        raw = np.matmul(
            np.asarray(left, dtype=np.float32),
            np.swapaxes(np.asarray(quant, dtype=np.float32), -1, -2),
        )
        raw_f16 = round_f16(raw)
        return round_f16(
            np.asarray(raw_f16, dtype=np.float32)
            * np.asarray(scale, dtype=np.float32).reshape(1, 1, -1)
        )

    activation_3d = activation.reshape(1, 1, hidden)
    gate_parts = [
        project_one(activation_3d, gate_q[index:index + 1], gate_scale[index])
        for index in range(TOP_K)
    ]
    up_parts = [
        project_one(activation_3d, up_q[index:index + 1], up_scale[index])
        for index in range(TOP_K)
    ]
    gate_concat = np.concatenate(gate_parts, axis=0)
    up_concat = np.concatenate(up_parts, axis=0)
    gate_swish = round_f16(
        np.asarray(gate_concat, dtype=np.float32)
        / (1.0 + np.exp(-np.asarray(gate_concat, dtype=np.float32)))
    )
    merged = round_f16(
        np.asarray(gate_swish, dtype=np.float32)
        * np.asarray(up_concat, dtype=np.float32)
    )
    weighted_parts = []
    for index in range(TOP_K):
        branch = merged[index:index + 1]
        down = project_one(branch, down_q[index:index + 1], down_scale[index])
        weighted_parts.append(
            round_f16(
                np.asarray(down.reshape(1, 1, 1, hidden), dtype=np.float32)
                * np.asarray(scores[index:index + 1], dtype=np.float32)
            )
        )
    branch_output = round_f16(
        np.sum(
            np.asarray(np.concatenate(weighted_parts, axis=0), dtype=np.float32),
            axis=0,
        )
    )

    gate_vector = round_f16(
        np.einsum(
            "bh,koh->kbo",
            np.asarray(activation, dtype=np.float32),
            np.asarray(gate_q, dtype=np.float32),
            optimize=True,
        )
    )
    gate_vector = round_f16(
        np.asarray(gate_vector, dtype=np.float32)
        * np.asarray(gate_scale[:, None, :], dtype=np.float32)
    )
    up_vector = round_f16(
        np.einsum(
            "bh,koh->kbo",
            np.asarray(activation, dtype=np.float32),
            np.asarray(up_q, dtype=np.float32),
            optimize=True,
        )
    )
    up_vector = round_f16(
        np.asarray(up_vector, dtype=np.float32)
        * np.asarray(up_scale[:, None, :], dtype=np.float32)
    )
    swish_vector = round_f16(
        np.asarray(gate_vector, dtype=np.float32)
        / (1.0 + np.exp(-np.asarray(gate_vector, dtype=np.float32)))
    )
    merge_vector = round_f16(
        np.asarray(swish_vector, dtype=np.float32)
        * np.asarray(up_vector, dtype=np.float32)
    )
    down_vector = round_f16(
        np.einsum(
            "kbi,koi->kbo",
            np.asarray(merge_vector, dtype=np.float32),
            np.asarray(down_q, dtype=np.float32),
            optimize=True,
        )
    )
    down_vector = round_f16(
        np.asarray(down_vector, dtype=np.float32)
        * np.asarray(down_scale[:, None, :], dtype=np.float32)
    )
    vector_output = round_f16(
        np.sum(
            np.asarray(down_vector[:, None, :, :], dtype=np.float32)
            * np.asarray(scores, dtype=np.float32),
            axis=0,
        )
    )

    branch_f32 = np.asarray(branch_output, dtype=np.float32).reshape(-1)
    vector_f32 = np.asarray(vector_output, dtype=np.float32).reshape(-1)
    delta = branch_f32 - vector_f32
    denominator = max(float(np.linalg.norm(vector_f32)), 1e-30)
    nrmse = float(np.linalg.norm(delta) / denominator)
    max_abs = float(np.max(np.abs(delta)))
    cosine_denominator = max(
        float(np.linalg.norm(branch_f32) * np.linalg.norm(vector_f32)),
        1e-30,
    )
    cosine = float(np.dot(branch_f32, vector_f32) / cosine_denominator)
    passed = (
        branch_output.shape == (1, 1, hidden)
        and branch_output.dtype == np.float16
        and bool(np.all(np.isfinite(branch_output)))
        and bool(np.allclose(branch_output, vector_output, rtol=0.002, atol=0.002))
    )
    if not passed:
        raise ProbeEdge(
            "cpu-emulation-precheck",
            f"synthetic branch/vector mismatch: cosine={cosine}, nrmse={nrmse}, max_abs={max_abs}",
        )
    return {
        "result": "pass",
        "scope": "synthetic reduced-dimension exact-topology algebra only; not real-weight or NPU correctness",
        "seed": seed,
        "top_k": TOP_K,
        "hidden": hidden,
        "intermediate": intermediate,
        "logical_i4_range": [-7, 7],
        "output_shape": list(branch_output.shape),
        "output_type": str(branch_output.dtype),
        "cosine": cosine,
        "nrmse": nrmse,
        "max_abs": max_abs,
        "rtol": 0.002,
        "atol": 0.002,
    }


def real_cache_precision_precheck(cache_dir: Path) -> dict[str, Any]:
    """Compare NPU-20 f32 islands with NPU-21 all-f16 on the real cache.

    This executes only NumPy CPU arithmetic over the existing packed NPU-0
    cache.  It validates the precision delta for two fixed route sets; it does
    not run OpenVINO inference or establish NPU correctness.
    """

    import hashlib
    import numpy as np

    routes = {
        "A": tuple(range(0, TOP_K)),
        "B": tuple(range(256, 256 + TOP_K)),
    }
    gate = {"cosine_min": 0.99999, "nrmse_max": 0.001}
    manifest_path = cache_dir / "layer0-i4-manifest.json"
    if not manifest_path.is_file():
        raise ProbeEdge("real-cache-precheck", f"cache manifest is missing: {manifest_path}")
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise ProbeEdge("real-cache-precheck", f"cannot read cache manifest: {exc}") from exc
    if (
        manifest.get("format") != "npu0-symmetric-i4-v1"
        or manifest.get("layer") != 0
        or manifest.get("num_experts") != 512
        or manifest.get("hidden") != HIDDEN
        or manifest.get("intermediate") != INTERMEDIATE
    ):
        raise ProbeEdge("real-cache-precheck", "cache manifest identity or dimensions do not match NPU-21")

    specs = {
        "gate": (INTERMEDIATE, HIDDEN),
        "up": (INTERMEDIATE, HIDDEN),
        "down": (HIDDEN, INTERMEDIATE),
    }
    packed_paths: dict[str, Path] = {}
    scale_paths: dict[str, Path] = {}
    scale_maps: dict[str, Any] = {}
    cache_labels: dict[str, Any] = {}
    for role, (rows, cols) in specs.items():
        packed_path = cache_dir / f"layer0-{role}.i4.bin"
        scale_path = cache_dir / f"layer0-{role}.scale.f16.bin"
        artifact = manifest.get("artifacts", {}).get(role, {})
        expected_packed_bytes = 512 * rows * cols // 2
        expected_scale_bytes = 512 * rows * 2
        if not packed_path.is_file() or packed_path.stat().st_size != expected_packed_bytes:
            raise ProbeEdge("real-cache-precheck", f"{role} packed cache size mismatch: {packed_path}")
        if not scale_path.is_file() or scale_path.stat().st_size != expected_scale_bytes:
            raise ProbeEdge("real-cache-precheck", f"{role} scale cache size mismatch: {scale_path}")
        if (
            artifact.get("packed_bytes") != expected_packed_bytes
            or artifact.get("scale_bytes") != expected_scale_bytes
            or artifact.get("logical_shape") != [512, rows, cols]
            or artifact.get("scale_shape") != [512, rows, 1]
        ):
            raise ProbeEdge("real-cache-precheck", f"{role} manifest layout mismatch")
        packed_paths[role] = packed_path
        scale_paths[role] = scale_path
        scale_maps[role] = np.memmap(
            scale_path,
            mode="r",
            dtype=np.float16,
            shape=(512, rows),
        )
        cache_labels[role] = {
            "packed_file": packed_path.name,
            "scale_file": scale_path.name,
            "logical_shape": [512, rows, cols],
            "scale_shape": [512, rows, 1],
            "manifest_declared_packed_sha256": artifact.get("packed_sha256"),
            "manifest_declared_scale_sha256": artifact.get("scale_sha256"),
            "full_file_sha256_reverified_this_run": False,
        }

    def array_digest(value: Any) -> str:
        array = np.ascontiguousarray(value)
        digest = hashlib.sha256()
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(json.dumps(list(array.shape), separators=(",", ":")).encode("ascii"))
        digest.update(array.view(np.uint8).tobytes())
        return digest.hexdigest()

    def load_bundle(selected: tuple[int, ...]):
        selected_digest = hashlib.sha256()
        bundle: list[dict[str, tuple[Any, Any]]] = []
        for expert in selected:
            item: dict[str, tuple[Any, Any]] = {}
            for role, (rows, cols) in specs.items():
                byte_count = rows * cols // 2
                raw = np.array(
                    np.memmap(
                        packed_paths[role],
                        mode="r",
                        dtype=np.uint8,
                        offset=expert * byte_count,
                        shape=(byte_count,),
                    ),
                    copy=True,
                )
                logical = np.empty(byte_count * 2, dtype=np.int8)
                logical[0::2] = (raw & np.uint8(0x0F)).astype(np.int8)
                logical[1::2] = (raw >> np.uint8(4)).astype(np.int8)
                logical[logical >= 8] -= 16
                logical = logical.reshape(rows, cols)
                scale = np.array(scale_maps[role][expert], dtype=np.float16, copy=True)
                label = f"expert={expert};role={role};".encode("ascii")
                selected_digest.update(label)
                selected_digest.update(raw.tobytes())
                selected_digest.update(scale.view(np.uint8).tobytes())
                item[role] = (logical, scale)
            bundle.append(item)
        return bundle, selected_digest.hexdigest()

    def activation_and_scores(selected: tuple[int, ...]):
        activation = np.zeros((1, HIDDEN), dtype=np.float16)
        logits = np.empty(TOP_K, dtype=np.float32)
        for rank, expert in enumerate(selected):
            value = np.float32(4.0 - rank * 0.1)
            activation[0, expert] = np.float16(value)
            logits[rank] = value
        probabilities = np.exp(logits - np.max(logits), dtype=np.float32)
        probabilities /= probabilities.sum(dtype=np.float32)
        return activation, probabilities.astype(np.float16).reshape(TOP_K, 1, 1, 1)

    def project(left: Any, quant: Any, scale: Any):
        left_f16 = np.asarray(left, dtype=np.float16)
        raw_f16 = (
            np.asarray(quant, dtype=np.float32)
            @ np.asarray(left_f16, dtype=np.float32).reshape(-1)
        ).astype(np.float16)
        return (
            np.asarray(raw_f16, dtype=np.float32)
            * np.asarray(scale, dtype=np.float32)
        ).astype(np.float16)

    def evaluate(bundle: list[dict[str, tuple[Any, Any]]], activation: Any, scores: Any, mode: str):
        x = np.asarray(activation, dtype=np.float16).reshape(HIDDEN)
        gate_outputs = []
        up_outputs = []
        for item in bundle:
            gate_outputs.append(project(x, *item["gate"]))
            up_outputs.append(project(x, *item["up"]))
        gate_f16 = np.stack(gate_outputs, axis=0)
        up_f16 = np.stack(up_outputs, axis=0)
        if mode == "faithful-f32-islands":
            gate_work = np.asarray(gate_f16, dtype=np.float32)
            up_work = np.asarray(up_f16, dtype=np.float32)
            activated = gate_work / (1.0 + np.exp(-gate_work))
            merged = activated * up_work
        elif mode == "all-f16":
            gate_work = np.asarray(gate_f16, dtype=np.float32)
            activated = (gate_work / (1.0 + np.exp(-gate_work))).astype(np.float16)
            merged = (
                np.asarray(activated, dtype=np.float32)
                * np.asarray(up_f16, dtype=np.float32)
            ).astype(np.float16)
        else:
            raise ValueError(f"unknown emulation mode: {mode}")

        weighted = []
        for index, item in enumerate(bundle):
            down_f16 = project(merged[index], *item["down"])
            if mode == "faithful-f32-islands":
                weighted.append(
                    np.asarray(down_f16, dtype=np.float32)
                    * np.float32(np.asarray(scores[index], dtype=np.float16).reshape(()))
                )
            else:
                weighted.append(
                    (
                        np.asarray(down_f16, dtype=np.float32)
                        * np.float32(np.asarray(scores[index], dtype=np.float16).reshape(()))
                    ).astype(np.float16)
                )
        if mode == "faithful-f32-islands":
            reduced = np.sum(np.stack(weighted, axis=0), axis=0, dtype=np.float32)
        else:
            reduced = np.sum(np.stack(weighted, axis=0), axis=0, dtype=np.float16)
        return np.asarray(reduced, dtype=np.float16).reshape(1, 1, HIDDEN)

    def metrics(reference: Any, observed: Any):
        ref = np.asarray(reference, dtype=np.float64).reshape(-1)
        got = np.asarray(observed, dtype=np.float64).reshape(-1)
        delta = ref - got
        ref_norm = max(float(np.linalg.norm(ref)), 1e-30)
        cosine_denominator = max(ref_norm * float(np.linalg.norm(got)), 1e-30)
        return {
            "cosine": float(np.dot(ref, got) / cosine_denominator),
            "nrmse": float(np.linalg.norm(delta) / ref_norm),
            "max_abs": float(np.max(np.abs(delta))),
        }

    bundles: dict[str, Any] = {}
    bundle_digests: dict[str, str] = {}
    inputs: dict[str, Any] = {}
    scores: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    comparisons: dict[str, Any] = {}
    for label, selected in routes.items():
        bundles[label], bundle_digests[label] = load_bundle(selected)
        inputs[label], scores[label] = activation_and_scores(selected)
        faithful = evaluate(bundles[label], inputs[label], scores[label], "faithful-f32-islands")
        candidate = evaluate(bundles[label], inputs[label], scores[label], "all-f16")
        outputs[label] = {"faithful": faithful, "candidate": candidate}
        comparisons[label] = metrics(faithful, candidate)

    replay_bundle, replay_bundle_digest = load_bundle(routes["A"])
    replay = evaluate(replay_bundle, inputs["A"], scores["A"], "all-f16")
    replay_metrics = metrics(outputs["A"]["candidate"], replay)
    replay_exact = bool(np.array_equal(outputs["A"]["candidate"], replay))
    separated_l2 = float(
        np.linalg.norm(
            np.asarray(outputs["A"]["candidate"], dtype=np.float64)
            - np.asarray(outputs["B"]["candidate"], dtype=np.float64)
        )
    )
    passed = (
        all(
            comparisons[label]["cosine"] >= gate["cosine_min"]
            and comparisons[label]["nrmse"] <= gate["nrmse_max"]
            for label in routes
        )
        and replay_exact
        and replay_metrics["nrmse"] == 0.0
        and bundle_digests["A"] == replay_bundle_digest
        and separated_l2 > 0.0
        and all(bool(np.all(np.isfinite(outputs[label]["candidate"]))) for label in routes)
    )
    if not passed:
        raise ProbeEdge(
            "real-cache-precheck",
            f"paired precision gate failed: comparisons={comparisons}, replay={replay_metrics}, "
            f"replay_exact={replay_exact}, separated_l2={separated_l2}",
        )

    return {
        "result": "pass",
        "scope": "real NPU-0 packed cache; CPU emulation of NPU-20 f32 islands versus NPU-21 all-f16; no NPU inference",
        "gate": gate,
        "routes": {label: list(selected) for label, selected in routes.items()},
        "input_labels": {
            "activation": "router-forced sparse f16: route coordinates receive 4.0-rank*0.1",
            "scores": "f16 top-10 renormalized softmax of float32 logits 4.0-rank*0.1",
            "matmul": "f16 operands emulated by float32 dot followed by f16 output rounding",
            "faithful": "NPU-20 f32 Swish/merge/score/ReduceSum islands with final f16 boundary",
            "candidate": "NPU-21 all-f16 Swish/merge/score/ReduceSum",
        },
        "cache": {
            "directory": str(cache_dir.resolve()),
            "manifest": manifest_path.name,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "format": manifest.get("format"),
            "layer": manifest.get("layer"),
            "artifacts": cache_labels,
            "selected_bundle_digest_algorithm": "sha256(expert/role label + packed bytes + f16 scale bytes, route order)",
            "selected_bundle_sha256": bundle_digests,
            "a_replay_selected_bundle_sha256": replay_bundle_digest,
        },
        "inputs": {
            label: {
                "activation_sha256": array_digest(inputs[label]),
                "scores_sha256": array_digest(scores[label]),
                "activation_type": str(inputs[label].dtype),
                "activation_shape": list(inputs[label].shape),
                "scores_type": str(scores[label].dtype),
                "scores_shape": list(scores[label].shape),
            }
            for label in routes
        },
        "comparisons": comparisons,
        "candidate_ab_l2": separated_l2,
        "digests": {
            label: {
                "faithful_output_sha256": array_digest(outputs[label]["faithful"]),
                "candidate_output_sha256": array_digest(outputs[label]["candidate"]),
            }
            for label in routes
        },
        "a_replay": {
            "exact": replay_exact,
            "metrics": replay_metrics,
            "candidate_output_sha256": array_digest(outputs["A"]["candidate"]),
            "replay_output_sha256": array_digest(replay),
        },
    }


def graph_contract(model: Any, ov: Any) -> dict[str, Any]:
    parameters = [
        {
            "name": node.get_friendly_name(),
            "type": str(node.get_output_element_type(0)),
            "shape": list(node.get_output_shape(0)),
        }
        for node in model.get_parameters()
    ]
    parameter_by_name = {node.get_friendly_name(): node for node in model.get_parameters()}
    ordered_ops = list(model.get_ordered_ops())
    node_by_name = {node.get_friendly_name(): node for node in ordered_ops}
    op_counts = Counter(node.get_type_name() for node in ordered_ops)

    expected_weights: dict[str, list[int]] = {}
    expected_scales: dict[str, list[int]] = {}
    expected_scores: dict[str, list[int]] = {}
    for index in range(TOP_K):
        prefix = expert_prefix(index)
        abi_prefix = abi_expert_prefix(index)
        for role in PROJECTIONS:
            weight_shape, scale_shape, _ = projection_shapes(role)
            expected_weights[f"{abi_prefix}.{role}.weight_i4"] = weight_shape
            expected_scales[f"{abi_prefix}.{role}.scale_f16"] = scale_shape
        expected_scores[f"{abi_prefix}.score_f16"] = [1, 1, 1, 1]

    expected_parameter_order = ["npu20.activation"]
    for index in range(TOP_K):
        prefix = abi_expert_prefix(index)
        expected_parameter_order.extend(
            [
                f"{prefix}.gate.weight_i4",
                f"{prefix}.gate.scale_f16",
                f"{prefix}.up.weight_i4",
                f"{prefix}.up.scale_f16",
            ]
        )
    for index in range(TOP_K):
        prefix = abi_expert_prefix(index)
        expected_parameter_order.extend(
            [
                f"{prefix}.down.weight_i4",
                f"{prefix}.down.scale_f16",
                f"{prefix}.score_f16",
            ]
        )
    actual_parameter_order = [node.get_friendly_name() for node in model.get_parameters()]
    if actual_parameter_order != expected_parameter_order:
        raise ProbeEdge(
            "model-contract",
            "ordered 71-input ABI differs from NPU-20",
        )

    expected_names = {
        "npu20.activation",
        *expected_weights.keys(),
        *expected_scales.keys(),
        *expected_scores.keys(),
    }
    actual_names = set(parameter_by_name)
    if actual_names != expected_names or len(parameters) != TOTAL_PARAMETER_COUNT:
        raise ProbeEdge(
            "model-contract",
            f"runtime Parameter contract mismatch: count={len(parameters)}, "
            f"missing={sorted(expected_names - actual_names)}, unexpected={sorted(actual_names - expected_names)}",
        )

    activation = parameter_by_name["npu20.activation"]
    if list(activation.get_output_shape(0)) != [1, HIDDEN] or activation.get_output_element_type(0) != ov.Type.f16:
        raise ProbeEdge("model-contract", "activation must be f16 [1,2560]")
    for name, expected_shape in expected_weights.items():
        node = parameter_by_name[name]
        if list(node.get_output_shape(0)) != expected_shape or node.get_output_element_type(0) != ov.Type.i4:
            raise ProbeEdge("model-contract", f"{name} must be i4 {expected_shape}")
    for name, expected_shape in {**expected_scales, **expected_scores}.items():
        node = parameter_by_name[name]
        if list(node.get_output_shape(0)) != expected_shape or node.get_output_element_type(0) != ov.Type.f16:
            raise ProbeEdge("model-contract", f"{name} must be f16 {expected_shape}")

    expected_op_counts = {
        "Parameter": 71,
        "Constant": 82,
        "Convert": 30,
        "Reshape": 41,
        "Multiply": 41,
        "MatMul": 30,
        "Slice": 10,
        "Concat": 3,
        "Swish": 1,
        "ReduceSum": 1,
        "Result": 1,
    }
    if len(ordered_ops) != 311:
        raise ProbeEdge("model-contract", f"expected 311 ordered ops, found {len(ordered_ops)}")
    for op_type, expected_count in expected_op_counts.items():
        actual_count = op_counts.get(op_type, 0)
        if actual_count != expected_count:
            raise ProbeEdge("model-contract", f"expected {expected_count} {op_type} ops, found {actual_count}")

    def input_node(node: Any, index: int):
        return node.input_value(index).get_node()

    for index in range(TOP_K):
        prefix = expert_prefix(index)
        abi_prefix = abi_expert_prefix(index)
        for role in PROJECTIONS:
            matmul = node_by_name[f"{prefix}.{role}.matmul"]
            activation_input = input_node(matmul, 0)
            expected_activation = (
                "npu21.activation.reshape"
                if role in ("gate", "up")
                else f"{prefix}.down.input_slice"
            )
            if activation_input.get_friendly_name() != expected_activation:
                raise ProbeEdge(
                    "model-contract",
                    f"{prefix}.{role} MatMul activation is not the direct f16 branch input",
                )
            if activation_input.get_output_element_type(0) != ov.Type.f16:
                raise ProbeEdge("model-contract", f"{prefix}.{role} MatMul activation must remain f16")
            weight_convert = input_node(matmul, 1)
            if weight_convert.get_friendly_name() != f"{prefix}.{role}.weight_to_f16":
                raise ProbeEdge("model-contract", f"{prefix}.{role} MatMul lacks direct i4-to-f16 weight Convert")
            if input_node(weight_convert, 0).get_friendly_name() != f"{abi_prefix}.{role}.weight_i4":
                raise ProbeEdge("model-contract", f"{prefix}.{role} weight is not a distinct runtime Parameter")
            if weight_convert.get_type_name() != "Convert":
                raise ProbeEdge("model-contract", f"{prefix}.{role} weight input must not be pre-scaled or concatenated")

            scaled = node_by_name[f"{prefix}.{role}.scale_after_matmul"]
            if input_node(scaled, 0).get_friendly_name() != matmul.get_friendly_name():
                raise ProbeEdge("model-contract", f"{prefix}.{role} scale is not applied after MatMul")
            scale_reshape = input_node(scaled, 1)
            if scale_reshape.get_friendly_name() != f"{prefix}.{role}.scale_reshape":
                raise ProbeEdge("model-contract", f"{prefix}.{role} output-scale reshape is missing")
            if input_node(scale_reshape, 0).get_friendly_name() != f"{abi_prefix}.{role}.scale_f16":
                raise ProbeEdge("model-contract", f"{prefix}.{role} scale is not a distinct runtime Parameter")
            if scaled.get_output_element_type(0) != ov.Type.f16:
                raise ProbeEdge("model-contract", f"{prefix}.{role} post-MatMul scale must remain f16")
        scored = node_by_name[f"{prefix}.score_multiply"]
        if input_node(scored, 0).get_friendly_name() != f"{prefix}.down.output_reshape":
            raise ProbeEdge("model-contract", f"{prefix} score must follow f16 down output reshape")
        if input_node(scored, 1).get_friendly_name() != f"{abi_prefix}.score_f16":
            raise ProbeEdge("model-contract", f"{prefix} score must remain a direct f16 Parameter")
        if scored.get_output_element_type(0) != ov.Type.f16:
            raise ProbeEdge("model-contract", f"{prefix} score Multiply must remain f16")

    for concat_name, expected_prefix in (
        ("npu21.gate.concat", ".gate.scale_after_matmul"),
        ("npu21.up.concat", ".up.scale_after_matmul"),
        ("npu21.weighted.concat", ".score_multiply"),
    ):
        concat = node_by_name[concat_name]
        for source in concat.input_values():
            if not source.get_node().get_friendly_name().endswith(expected_prefix):
                raise ProbeEdge("model-contract", f"{concat_name} contains a packed-weight or unexpected input")

    if model.is_dynamic():
        raise ProbeEdge("model-contract", "manual DQ_FULL graph must be fully static")
    output_shape = list(model.output(0).get_shape())
    output_type = model.output(0).get_element_type()
    if output_shape != [1, 1, HIDDEN] or output_type != ov.Type.f16:
        raise ProbeEdge("model-contract", f"output must be f16 [1,1,{HIDDEN}], found {output_type} {output_shape}")

    precheck = cpu_emulation_precheck()
    return {
        "name": model.get_friendly_name(),
        "fully_static": True,
        "manual_post_matmul_scaling": True,
        "parameters": parameters,
        "parameter_count": len(parameters),
        "ordered_parameter_names": actual_parameter_order,
        "abi_compatible_with": "NPU-20 ordered 71-input ABI",
        "activation_parameter_count": 1,
        "i4_weight_parameter_count": len(expected_weights),
        "f16_scale_parameter_count": len(expected_scales),
        "f16_score_parameter_count": len(expected_scores),
        "logical_expert_count": TOP_K,
        "logical_projection_count": WEIGHT_PARAMETER_COUNT,
        "ordered_op_count": len(ordered_ops),
        "op_counts": dict(sorted(op_counts.items())),
        "required_op_counts": expected_op_counts,
        "output_shape": output_shape,
        "output_type": str(output_type),
        "algebra": "all-f16 activation, MatMul, post-scale, shared Swish/merge, score, and ReduceSum; only runtime i4 weights Convert to f16",
        "internal_precision": "f16",
        "cpu_emulation_precheck": precheck,
        "correctness_scope": "synthetic topology sanity in this contract; the separate real-cache paired precision precheck remains CPU emulation and does not claim NPU correctness",
        "layout": {
            "activation": "[1,2560]",
            "gate_weight": "[1,640,2560]",
            "gate_scale": "[1,640,1]",
            "up_weight": "[1,640,2560]",
            "up_scale": "[1,640,1]",
            "down_weight": "[1,2560,640]",
            "down_scale": "[1,2560,1]",
            "score": "[1,1,1,1] per expert",
            "output": "[1,1,2560]",
        },
        "runtime_input_bytes": {
            "packed_i4_weights": PACKED_WEIGHT_BYTES,
            "f16_scales": SCALE_BYTES,
            "f16_activation": ACTIVATION_BYTES,
            "f16_scores": SCORE_BYTES,
            "total": RUNTIME_INPUT_BYTES,
        },
        "no_pre_scale_weight_multiply": True,
        "no_weight_concat": True,
        "no_repack": True,
        "inference_permitted": False,
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if "ZE_AFFINITY_MASK" in os.environ:
        raise ProbeEdge("preflight", "ZE_AFFINITY_MASK is set; refusing known-deadlock configuration")

    os.environ["OV_NPU_LOG_LEVEL"] = "LOG_DEBUG"
    try:
        import openvino as ov
        import openvino.opset13 as ops
    except Exception as exc:
        raise ProbeEdge("preflight", f"OpenVINO import failed: {exc}") from exc
    if not str(ov.__version__).startswith("2026.3"):
        raise ProbeEdge("preflight", f"OpenVINO must be 2026.3.x, found {ov.__version__}")

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    core = ov.Core()
    if args.device not in core.available_devices:
        raise ProbeEdge("preflight", f"{args.device} unavailable; devices={core.available_devices}")

    def get_property(name: str, default: Any = None):
        try:
            return core.get_property(args.device, name)
        except Exception:
            return default

    capability = base_receipt(args)
    capability.update(
        {
            "cell": "capability",
            "openvino": str(ov.__version__),
            "full_device_name": str(get_property("FULL_DEVICE_NAME", "unknown")),
            "arch": str(get_property("DEVICE_ARCHITECTURE", "unknown")),
            "driver": str(get_property("NPU_DRIVER_VERSION", "unknown")),
            "compiler_version": str(get_property("NPU_COMPILER_VERSION", "unknown")),
        }
    )
    append_jsonl(args.receipts, capability)
    print(json.dumps(capability, sort_keys=True), flush=True)
    if capability["arch"] != "3720":
        raise ProbeEdge("preflight", f"NPU-21 is pinned to architecture 3720, found {capability['arch']}")

    cache_dir = (args.weight_cache_dir or args.artifact_dir).resolve()
    try:
        precision_precheck = real_cache_precision_precheck(cache_dir)
    except ProbeEdge:
        raise
    except Exception as exc:
        raise ProbeEdge("real-cache-precheck", f"paired precision precheck failed unexpectedly: {exc}") from exc
    precision_precheck_path = args.artifact_dir / "npu21-precision-precheck.json"
    atomic_json(precision_precheck_path, precision_precheck)
    print(
        json.dumps(
            {"probe": PROBE, "cell": "real-cache-precision-precheck", **precision_precheck},
            sort_keys=True,
        ),
        flush=True,
    )

    try:
        model = build_model(ov, ops)
        contract = graph_contract(model, ov)
    except ProbeEdge:
        raise
    except Exception as exc:
        raise ProbeEdge("model-build", f"cannot build manual DQ_FULL expert graph: {exc}") from exc

    contract_path = args.artifact_dir / "npu21-graph-contract.json"
    atomic_json(contract_path, contract)
    print(json.dumps({"probe": PROBE, "cell": "graph-contract", **contract}, sort_keys=True), flush=True)

    config = {
        "NPU_COMPILER_TYPE": "PLUGIN",
        "NPU_COMPILER_DYNAMIC_QUANTIZATION": args.enable_compiler_dq,
        "PERFORMANCE_HINT": "LATENCY",
        "PERF_COUNT": False,
        "LOG_LEVEL": "LOG_DEBUG",
    }
    config_path = args.artifact_dir / "npu21-config.json"
    atomic_json(config_path, {key: str(value) for key, value in config.items()})
    print(
        "NPU-21: compiling direct no-repack manual-DQ_FULL expert graph; "
        f"compiler_dq={args.enable_compiler_dq}; inference is forbidden",
        flush=True,
    )
    started = time.perf_counter()
    try:
        compiled = core.compile_model(model, "NPU", config)
    except Exception as exc:
        raise ProbeEdge("compile", f"direct NPU PLUGIN compile failed: {exc}") from exc
    compile_ms = (time.perf_counter() - started) * 1000.0

    try:
        actual_compiler_type = str(compiled.get_property("NPU_COMPILER_TYPE"))
    except Exception:
        actual_compiler_type = "unknown"

    expected_inputs = {entry["name"]: entry for entry in contract["parameters"]}
    compiled_inputs: list[dict[str, Any]] = []
    matched_names: set[str] = set()
    for port in compiled.inputs:
        names = sorted(port.get_names())
        matches = sorted(set(names) & set(expected_inputs))
        if len(matches) != 1:
            raise ProbeEdge("compiled-abi", f"compiled input names {names} do not identify exactly one graph Parameter")
        name = matches[0]
        matched_names.add(name)
        compiled_inputs.append(
            {
                "name": name,
                "names": names,
                "type": str(port.get_element_type()),
                "shape": list(port.get_shape()),
            }
        )
        expected = expected_inputs[name]
        if compiled_inputs[-1]["type"] != expected["type"] or compiled_inputs[-1]["shape"] != expected["shape"]:
            raise ProbeEdge(
                "compiled-abi",
                f"compiled input {name} changed from {expected['type']} {expected['shape']} "
                f"to {compiled_inputs[-1]['type']} {compiled_inputs[-1]['shape']}",
            )
    if len(compiled_inputs) != TOTAL_PARAMETER_COUNT or matched_names != set(expected_inputs):
        raise ProbeEdge(
            "compiled-abi",
            f"expected {TOTAL_PARAMETER_COUNT} distinct compiled inputs, found {len(compiled_inputs)}; "
            f"missing={sorted(set(expected_inputs) - matched_names)}",
        )
    compiled_parameter_order = [entry["name"] for entry in compiled_inputs]
    if compiled_parameter_order != contract["ordered_parameter_names"]:
        raise ProbeEdge(
            "compiled-abi",
            "compiled input order differs from the NPU-20-compatible ordered 71-input ABI",
        )

    row = base_receipt(args)
    row.update(
        {
            "cell": "compile-only",
            "result": "compiled-awaiting-lowering-audit",
            "compile_ms": round(compile_ms, 3),
            "compiler_type_actual": actual_compiler_type,
            "graph_contract": contract,
            "contract_path": str(contract_path),
            "config_path": str(config_path),
            "compiled_input_count": len(compiled_inputs),
            "compiled_inputs": compiled_inputs,
            "expected_logical_matmuls": WEIGHT_PARAMETER_COUNT,
            "expected_post_matmul_f16_scales": SCALE_PARAMETER_COUNT,
            "expected_f16_reduce_sum": 1,
            "cpu_emulation_precheck": contract["cpu_emulation_precheck"],
            "real_cache_precision_precheck": precision_precheck,
            "precision_precheck_path": str(precision_precheck_path),
            "admission_estimate_us": 1500.0,
            "rerun": rerun_command(args),
            "uncertainties": [
                "compiler log must prove the lowered compute-op count and estimated latency",
                "compiler may reject runtime i4 Parameters even though the graph contract is valid",
                "compile-only admission does not prove arbitrary-host-USM runtime binding",
                "route-time binding, submit latency, physical DDR traffic, and staging remain unmeasured",
                "the real-cache paired check is CPU emulation of explicit f16 boundaries, not NPU numerical correctness",
                "no NPU inference or NPU numerical-correctness measurement is part of this lap",
            ],
        }
    )
    append_jsonl(args.receipts, row)
    atomic_json(args.artifact_dir / "npu21-summary.json", row)
    print(json.dumps(row, sort_keys=True), flush=True)
    return row


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        execute(args)
        return 0
    except ProbeEdge as edge:
        row = base_receipt(args)
        row.update(
            {
                "cell": "verdict",
                "result": "first-edge",
                "edge": edge.stage,
                "detail": edge.detail,
                "rerun": rerun_command(args),
                "uncertainties": [edge.detail],
            }
        )
        try:
            append_jsonl(args.receipts, row)
            atomic_json(args.artifact_dir / "npu21-edge.json", row)
        except Exception as receipt_exc:
            print(f"NPU-21: could not persist edge receipt: {receipt_exc}", file=sys.stderr)
        print(json.dumps(row, sort_keys=True), flush=True)
        return 2
    except Exception as exc:
        row = base_receipt(args)
        row.update(
            {
                "cell": "verdict",
                "result": "first-edge",
                "edge": "unexpected",
                "detail": str(exc),
                "rerun": rerun_command(args),
                "traceback": traceback.format_exc(),
                "uncertainties": [str(exc)],
            }
        )
        try:
            append_jsonl(args.receipts, row)
            atomic_json(args.artifact_dir / "npu21-edge.json", row)
        except Exception:
            pass
        print(json.dumps(row, sort_keys=True), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
