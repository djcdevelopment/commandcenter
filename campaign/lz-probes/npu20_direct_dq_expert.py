#!/usr/bin/env python3
"""Compile a no-repack, manual-DQ_FULL Qwen3.8 Flash expert bundle.

NPU-20 is an /rnd compile-only admission probe.  It presents ten independent
experts directly to the NPU plugin: thirty runtime i4 weight Parameters,
thirty runtime f16 per-output scale Parameters, one f16 activation, and ten
f16 route scores.  Each projection performs i4-to-f16 conversion, f16 MatMul,
and f16 post-scaling.  The routed nonlinear, merge, score, and reduction path
remains f32, with a final f16 boundary output.  The compiler's separate
dynamic-quantization pass is an independent switch.  The probe never creates
an infer request.
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


PROBE = "NPU-20"
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
        "perf_count": False,
        "log_level": "LOG_DEBUG",
        "inference_attempted": False,
    }


def make_named(node: Any, name: str):
    node.set_friendly_name(name)
    return node


def expert_prefix(index: int) -> str:
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
    activation_f32 = make_named(ops.convert(activation, ov.Type.f32), "npu20.activation.to_f32")
    activation_3d = make_named(
        ops.reshape(
            activation_f32,
            constant([1, 1, HIDDEN], "npu20.activation.reshape.shape"),
            False,
        ),
        "npu20.activation.reshape",
    )

    def project(left_f32: Any, role: str, prefix: str):
        weight_shape, scale_shape, output_shape = projection_shapes(role)
        weight = parameter(weight_shape, ov.Type.i4, f"{prefix}.{role}.weight_i4")
        scale = parameter(scale_shape, ov.Type.f16, f"{prefix}.{role}.scale_f16")
        params.extend((weight, scale))
        left_f16 = make_named(ops.convert(left_f32, ov.Type.f16), f"{prefix}.{role}.activation_to_f16")
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
        return make_named(ops.convert(scaled_f16, ov.Type.f32), f"{prefix}.{role}.output_to_f32")

    gate_outputs: list[Any] = []
    up_outputs: list[Any] = []
    for index in range(TOP_K):
        prefix = expert_prefix(index)
        gate_outputs.append(project(activation_3d, "gate", prefix))
        up_outputs.append(project(activation_3d, "up", prefix))

    gate_concat = make_named(ops.concat(gate_outputs, 0), "npu20.gate.concat")
    up_concat = make_named(ops.concat(up_outputs, 0), "npu20.up.concat")
    gate_swish = make_named(
        NodeFactory("opset4").create("Swish", [gate_concat], {}),
        "npu20.gate.swish",
    )
    if gate_swish.get_input_size() != 1:
        raise ProbeEdge("model-build", "NPU-20 requires a one-input opset4 Swish")
    merged = make_named(ops.multiply(gate_swish, up_concat), "npu20.gate_up.merge")

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
        down_f32 = project(branch, "down", prefix)
        down_4d = make_named(
            ops.reshape(
                down_f32,
                constant([1, 1, 1, HIDDEN], f"{prefix}.down.output_shape"),
                False,
            ),
            f"{prefix}.down.output_reshape",
        )
        score = parameter([1, 1, 1, 1], ov.Type.f16, f"{prefix}.score_f16")
        params.append(score)
        score_f32 = make_named(ops.convert(score, ov.Type.f32), f"{prefix}.score_to_f32")
        weighted_outputs.append(
            make_named(ops.multiply(down_4d, score_f32), f"{prefix}.score_multiply")
        )

    weighted_concat = make_named(ops.concat(weighted_outputs, 0), "npu20.weighted.concat")
    reduced = make_named(
        ops.reduce_sum(
            weighted_concat,
            constant([0], "npu20.reduce.axis"),
            False,
        ),
        "npu20.weighted.reduce",
    )
    output = make_named(ops.convert(reduced, ov.Type.f16), "npu20.output")
    model = ov.Model([output], params, "npu20_direct_manual_dq_full_experts")
    model.validate_nodes_and_infer_types()
    return model


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
        for role in PROJECTIONS:
            weight_shape, scale_shape, _ = projection_shapes(role)
            expected_weights[f"{prefix}.{role}.weight_i4"] = weight_shape
            expected_scales[f"{prefix}.{role}.scale_f16"] = scale_shape
        expected_scores[f"{prefix}.score_f16"] = [1, 1, 1, 1]

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
        "Convert": 102,
        "Reshape": 41,
        "Multiply": 41,
        "MatMul": 30,
        "Slice": 10,
        "Concat": 3,
        "Swish": 1,
        "ReduceSum": 1,
        "Result": 1,
    }
    if len(ordered_ops) != 383:
        raise ProbeEdge("model-contract", f"expected 383 ordered ops, found {len(ordered_ops)}")
    for op_type, expected_count in expected_op_counts.items():
        actual_count = op_counts.get(op_type, 0)
        if actual_count != expected_count:
            raise ProbeEdge("model-contract", f"expected {expected_count} {op_type} ops, found {actual_count}")

    def input_node(node: Any, index: int):
        return node.input_value(index).get_node()

    for index in range(TOP_K):
        prefix = expert_prefix(index)
        for role in PROJECTIONS:
            matmul = node_by_name[f"{prefix}.{role}.matmul"]
            activation_convert = input_node(matmul, 0)
            if activation_convert.get_friendly_name() != f"{prefix}.{role}.activation_to_f16":
                raise ProbeEdge("model-contract", f"{prefix}.{role} activation must narrow directly before MatMul")
            weight_convert = input_node(matmul, 1)
            if weight_convert.get_friendly_name() != f"{prefix}.{role}.weight_to_f16":
                raise ProbeEdge("model-contract", f"{prefix}.{role} MatMul lacks direct i4-to-f16 weight Convert")
            if input_node(weight_convert, 0).get_friendly_name() != f"{prefix}.{role}.weight_i4":
                raise ProbeEdge("model-contract", f"{prefix}.{role} weight is not a distinct runtime Parameter")
            if weight_convert.get_type_name() != "Convert":
                raise ProbeEdge("model-contract", f"{prefix}.{role} weight input must not be pre-scaled or concatenated")

            scaled = node_by_name[f"{prefix}.{role}.scale_after_matmul"]
            if input_node(scaled, 0).get_friendly_name() != matmul.get_friendly_name():
                raise ProbeEdge("model-contract", f"{prefix}.{role} scale is not applied after MatMul")
            scale_reshape = input_node(scaled, 1)
            if scale_reshape.get_friendly_name() != f"{prefix}.{role}.scale_reshape":
                raise ProbeEdge("model-contract", f"{prefix}.{role} output-scale reshape is missing")
            if input_node(scale_reshape, 0).get_friendly_name() != f"{prefix}.{role}.scale_f16":
                raise ProbeEdge("model-contract", f"{prefix}.{role} scale is not a distinct runtime Parameter")
            if scaled.get_output_element_type(0) != ov.Type.f16:
                raise ProbeEdge("model-contract", f"{prefix}.{role} post-MatMul scale must remain f16")
            output_convert = node_by_name[f"{prefix}.{role}.output_to_f32"]
            if input_node(output_convert, 0).get_friendly_name() != scaled.get_friendly_name():
                raise ProbeEdge("model-contract", f"{prefix}.{role} must preserve post-scale f16-to-f32 Convert")

        scored = node_by_name[f"{prefix}.score_multiply"]
        if input_node(scored, 0).get_friendly_name() != f"{prefix}.down.output_reshape":
            raise ProbeEdge("model-contract", f"{prefix} score must follow f32 down output reshape")
        if input_node(scored, 1).get_friendly_name() != f"{prefix}.score_to_f32":
            raise ProbeEdge("model-contract", f"{prefix} score must convert to f32 before weighting")

    for concat_name, expected_prefix in (
        ("npu20.gate.concat", ".gate.output_to_f32"),
        ("npu20.up.concat", ".up.output_to_f32"),
        ("npu20.weighted.concat", ".score_multiply"),
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

    return {
        "name": model.get_friendly_name(),
        "fully_static": True,
        "manual_post_matmul_scaling": True,
        "parameters": parameters,
        "parameter_count": len(parameters),
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
        "algebra": "f16 MatMul over raw i4 values; f16 post-MatMul per-output scale; preserved f32 Swish/merge/score/reduction; f16 boundary output",
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
        raise ProbeEdge("preflight", f"NPU-20 is pinned to architecture 3720, found {capability['arch']}")

    try:
        model = build_model(ov, ops)
        contract = graph_contract(model, ov)
    except ProbeEdge:
        raise
    except Exception as exc:
        raise ProbeEdge("model-build", f"cannot build manual DQ_FULL expert graph: {exc}") from exc

    contract_path = args.artifact_dir / "npu20-graph-contract.json"
    atomic_json(contract_path, contract)
    print(json.dumps({"probe": PROBE, "cell": "graph-contract", **contract}, sort_keys=True), flush=True)

    config = {
        "NPU_COMPILER_TYPE": "PLUGIN",
        "NPU_COMPILER_DYNAMIC_QUANTIZATION": args.enable_compiler_dq,
        "PERFORMANCE_HINT": "LATENCY",
        "PERF_COUNT": False,
        "LOG_LEVEL": "LOG_DEBUG",
    }
    config_path = args.artifact_dir / "npu20-config.json"
    atomic_json(config_path, {key: str(value) for key, value in config.items()})
    print(
        "NPU-20: compiling direct no-repack manual-DQ_FULL expert graph; "
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
            "expected_f32_reduce_sum": 1,
            "admission_estimate_us": 1500.0,
            "rerun": rerun_command(args),
            "uncertainties": [
                "compiler log must prove the lowered compute-op count and estimated latency",
                "compiler may reject runtime i4 Parameters even though the graph contract is valid",
                "compile-only admission does not prove arbitrary-host-USM runtime binding",
                "route-time binding, submit latency, physical DDR traffic, and staging remain unmeasured",
                "no inference or numerical-correctness measurement is part of this lap",
            ],
        }
    )
    append_jsonl(args.receipts, row)
    atomic_json(args.artifact_dir / "npu20-summary.json", row)
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
            atomic_json(args.artifact_dir / "npu20-edge.json", row)
        except Exception as receipt_exc:
            print(f"NPU-20: could not persist edge receipt: {receipt_exc}", file=sys.stderr)
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
            atomic_json(args.artifact_dir / "npu20-edge.json", row)
        except Exception:
            pass
        print(json.dumps(row, sort_keys=True), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
