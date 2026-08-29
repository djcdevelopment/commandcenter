#!/usr/bin/env python3
"""Compile the three-GEMM form of one Qwen3.8 Flash routed expert layer.

This is an /rnd compile-only admission probe.  It replaces K independent
gate/up/down expert GEMMs with two output-concatenated GEMMs and one
input-concatenated down GEMM.  All expert bundles remain runtime Parameters;
the lap stops after the NPU compiler so lowering and estimated latency can
decide whether a real packed-buffer runtime is worth building.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


PROBE = "NPU-11"
TOP_K = 10
HIDDEN = 2560
INTERMEDIATE = 640
PACKED_WEIGHT_BYTES = TOP_K * 3 * (HIDDEN * INTERMEDIATE // 2)
SCALE_BYTES = TOP_K * (INTERMEDIATE + INTERMEDIATE + HIDDEN) * 2
BUNDLE_BYTES = PACKED_WEIGHT_BYTES + SCALE_BYTES


class ProbeEdge(RuntimeError):
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
    parser.add_argument("--device", default="NPU")
    parser.add_argument("--compiler-type", default="PLUGIN", choices=("PLUGIN", "DRIVER"))
    parser.add_argument("--bf6-render-queue", default="unknown")
    parser.add_argument("--coresident", action="store_true")
    parser.add_argument(
        "--enable-compiler-dq",
        action="store_true",
        help="enable the NPU compiler's weight dynamic-dequantization pass",
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
        "compiler_dq": args.enable_compiler_dq,
        "perf_count": False,
    }


def make_named(node: Any, name: str):
    node.set_friendly_name(name)
    return node


def build_model(ov: Any, ops: Any):
    import numpy as np
    from openvino.utils.node_factory import NodeFactory

    def parameter(shape: list[int], dtype: Any, name: str):
        return make_named(ops.parameter(shape, dtype), name)

    def constant(value: Any):
        return ops.constant(np.asarray(value, dtype=np.int64))

    def decompress(packed: Any, scale: Any, prefix: str):
        converted = make_named(ops.convert(packed, ov.Type.f16), f"{prefix}.convert")
        scaled = make_named(ops.multiply(converted, scale), f"{prefix}.scale")
        return make_named(ops.convert(scaled, ov.Type.f32), f"{prefix}.compute")

    activation = parameter([1, HIDDEN], ov.Type.f32, "npu11.activation")
    gate_packed = parameter(
        [TOP_K * INTERMEDIATE, HIDDEN],
        ov.Type.i4,
        "npu11.gate.packed",
    )
    gate_scale = parameter(
        [TOP_K * INTERMEDIATE, 1],
        ov.Type.f16,
        "npu11.gate.scale",
    )
    up_packed = parameter(
        [TOP_K * INTERMEDIATE, HIDDEN],
        ov.Type.i4,
        "npu11.up.packed",
    )
    up_scale = parameter(
        [TOP_K * INTERMEDIATE, 1],
        ov.Type.f16,
        "npu11.up.scale",
    )
    router_scores = parameter([TOP_K, 1], ov.Type.f32, "npu11.router_scores")
    down_packed = parameter(
        [HIDDEN, TOP_K, INTERMEDIATE],
        ov.Type.i4,
        "npu11.down.packed",
    )
    down_scale = parameter(
        [HIDDEN, TOP_K, 1],
        ov.Type.f16,
        "npu11.down.scale",
    )

    gate_weight = decompress(gate_packed, gate_scale, "npu11.gate")
    up_weight = decompress(up_packed, up_scale, "npu11.up")
    down_weight_3d = decompress(down_packed, down_scale, "npu11.down")

    gate = make_named(
        ops.matmul(activation, gate_weight, False, True),
        "npu11.gate.matmul",
    )
    up = make_named(
        ops.matmul(activation, up_weight, False, True),
        "npu11.up.matmul",
    )
    gate_2d = make_named(
        ops.reshape(gate, constant([TOP_K, INTERMEDIATE]), False),
        "npu11.gate.reshape",
    )
    up_2d = make_named(
        ops.reshape(up, constant([TOP_K, INTERMEDIATE]), False),
        "npu11.up.reshape",
    )
    swish = make_named(
        NodeFactory("opset4").create("Swish", [gate_2d], {}),
        "npu11.swish",
    )
    if swish.get_input_size() != 1:
        raise ProbeEdge("model-build", "collapsed graph requires one-input opset4 Swish")
    merged = make_named(ops.multiply(swish, up_2d), "npu11.merge")
    weighted = make_named(ops.multiply(merged, router_scores), "npu11.weighted")
    flattened = make_named(
        ops.reshape(weighted, constant([1, TOP_K * INTERMEDIATE]), False),
        "npu11.weighted.flatten",
    )
    down_weight = make_named(
        ops.reshape(down_weight_3d, constant([HIDDEN, TOP_K * INTERMEDIATE]), False),
        "npu11.down.reshape",
    )
    down = make_named(
        ops.matmul(flattened, down_weight, False, True),
        "npu11.down.matmul",
    )
    output = make_named(
        ops.reshape(down, constant([1, 1, HIDDEN]), False),
        "npu11.output",
    )
    params = [
        activation,
        gate_packed,
        gate_scale,
        up_packed,
        up_scale,
        router_scores,
        down_packed,
        down_scale,
    ]
    model = ov.Model([output], params, "npu11_collapsed_qwen38_flash_expert")
    model.validate_nodes_and_infer_types()
    return model


def graph_contract(model: Any) -> dict[str, Any]:
    parameters = [
        {
            "name": node.get_friendly_name(),
            "type": str(node.get_output_element_type(0)),
            "shape": list(node.get_output_shape(0)),
        }
        for node in model.get_parameters()
    ]
    matmuls = [
        node.get_friendly_name()
        for node in model.get_ordered_ops()
        if node.get_type_name() == "MatMul"
    ]
    expert_weight_params = {
        "npu11.gate.packed",
        "npu11.gate.scale",
        "npu11.up.packed",
        "npu11.up.scale",
        "npu11.down.packed",
        "npu11.down.scale",
    }
    parameter_names = {row["name"] for row in parameters}
    if len(parameters) != 8:
        raise ProbeEdge("model-contract", f"expected 8 Parameters, found {len(parameters)}")
    if len(matmuls) != 3:
        raise ProbeEdge("model-contract", f"expected 3 MatMuls, found {matmuls}")
    if not expert_weight_params.issubset(parameter_names):
        raise ProbeEdge(
            "model-contract",
            f"expert weights are not all runtime Parameters: {sorted(expert_weight_params - parameter_names)}",
        )
    if model.is_dynamic():
        raise ProbeEdge("model-contract", "collapsed graph must be fully static")
    output_shape = list(model.output(0).get_shape())
    if output_shape != [1, 1, HIDDEN]:
        raise ProbeEdge("model-contract", f"unexpected output shape: {output_shape}")
    return {
        "parameters": parameters,
        "parameter_count": len(parameters),
        "matmuls": matmuls,
        "matmul_count": len(matmuls),
        "output_shape": output_shape,
        "fully_static": True,
        "packed_weight_bytes": PACKED_WEIGHT_BYTES,
        "scale_bytes": SCALE_BYTES,
        "bundle_bytes": BUNDLE_BYTES,
        "host_bundle_layout": {
            "gate": "[K*I,H]",
            "up": "[K*I,H]",
            "down": "[H,K,I]",
            "down_scale": "[H,K,1]",
        },
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if "ZE_AFFINITY_MASK" in os.environ:
        raise ProbeEdge("preflight", "ZE_AFFINITY_MASK is set; refusing known-deadlock configuration")

    os.environ.setdefault("OV_NPU_LOG_LEVEL", "LOG_DEBUG")
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
            "requested_compiler_type": args.compiler_type,
        }
    )
    append_jsonl(args.receipts, capability)
    print(json.dumps(capability, sort_keys=True), flush=True)
    if capability["arch"] != "3720":
        raise ProbeEdge("preflight", f"NPU-11 is pinned to architecture 3720, found {capability['arch']}")

    try:
        model = build_model(ov, ops)
        contract = graph_contract(model)
    except ProbeEdge:
        raise
    except Exception as exc:
        raise ProbeEdge("model-build", f"cannot build collapsed expert graph: {exc}") from exc

    contract_path = args.artifact_dir / "npu11-graph-contract.json"
    atomic_json(contract_path, contract)
    print(json.dumps({"probe": PROBE, "cell": "graph-contract", **contract}, sort_keys=True), flush=True)

    config = {
        "NPU_COMPILER_TYPE": args.compiler_type,
        "PERFORMANCE_HINT": "LATENCY",
        "PERF_COUNT": False,
        "LOG_LEVEL": "LOG_DEBUG",
    }
    if args.enable_compiler_dq:
        config["NPU_COMPILER_DYNAMIC_QUANTIZATION"] = True
    atomic_json(args.artifact_dir / "npu11-config.json", {key: str(value) for key, value in config.items()})
    print("NPU-11: compiling direct three-MatMul compressed expert graph", flush=True)
    started = time.perf_counter()
    try:
        compiled = core.compile_model(model, args.device, config)
    except Exception as exc:
        raise ProbeEdge("compile", f"direct NPU compile failed: {exc}") from exc
    compile_ms = (time.perf_counter() - started) * 1000.0
    try:
        actual_compiler_type = str(compiled.get_property("NPU_COMPILER_TYPE"))
    except Exception:
        actual_compiler_type = "unknown"

    row = base_receipt(args)
    row.update(
        {
            "cell": "compile-only",
            "result": "compiled-awaiting-lowering-audit",
            "compile_ms": round(compile_ms, 3),
            "compiler_type": actual_compiler_type,
            "graph_contract": contract,
            "contract_path": str(contract_path),
            "inference_attempted": False,
            "expected_logical_matmuls": 3,
            "expected_lowered_convolutions": 3,
            "rerun": rerun_command(args),
            "uncertainties": [
                "compiler log must prove whether three logical MatMuls remain three lowered compute operations",
                "compiler-estimated latency and CMX/tiling must justify a packed runtime",
                "host bundle gather/copy traffic is not measured",
                "physical device-side DMA/staging remains unmeasured",
            ],
        }
    )
    append_jsonl(args.receipts, row)
    atomic_json(args.artifact_dir / "npu11-summary.json", row)
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
            atomic_json(args.artifact_dir / "npu11-edge.json", row)
        except Exception as receipt_exc:
            print(f"NPU-11: could not persist edge receipt: {receipt_exc}", file=sys.stderr)
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
            atomic_json(args.artifact_dir / "npu11-edge.json", row)
        except Exception:
            pass
        print(json.dumps(row, sort_keys=True), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
