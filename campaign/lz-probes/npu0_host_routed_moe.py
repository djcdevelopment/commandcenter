"""NPU-0: one real, Flash-shaped, host-routed MoE layer on Intel NPU.

This is an /rnd probe, not a benchmark matrix.  It compiles one Qwen3-style
decode MoE graph through OpenVINO NPUW, routes two deterministic top-10 expert
sets, and stops at the first edge.  Real layer-0 Flash expert weights are
stream-dequantized from GGUF and requantized once into OpenVINO symmetric i4.

The generated weight cache and raw evidence live outside the repository.  The
only persistent in-repo artifact is this probe program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


PROBE = "NPU-0"
NUM_EXPERTS = 512
TOP_K = 10
HIDDEN = 2560
INTERMEDIATE = 640
WARMUPS = 8
TIMED_ITERS = 64
SET_A = tuple(range(0, 10))
SET_B = tuple(range(256, 266))

# Exact bytes read from the source GGUF blocks for one selected expert bundle.
SOURCE_BYTES_PER_EXPERT = 704_000 + 704_000 + 921_600
SOURCE_ACTIVE_BYTES = TOP_K * SOURCE_BYTES_PER_EXPERT

# Routed-expert bytes presented after symmetric-i4 repacking, including f16
# scales.  The synthetic compressed router is accounted separately below.
I4_BYTES_PER_MATRIX = (INTERMEDIATE * HIDDEN) // 2
I4_BYTES_PER_EXPERT = 3 * I4_BYTES_PER_MATRIX
SCALE_BYTES_PER_EXPERT = (INTERMEDIATE + INTERMEDIATE + HIDDEN) * 2
NPU_ACTIVE_BYTES = TOP_K * (I4_BYTES_PER_EXPERT + SCALE_BYTES_PER_EXPERT)
ROUTER_ACTIVE_BYTES = (NUM_EXPERTS * HIDDEN) // 2 + NUM_EXPERTS * 2
MODELED_GRAPH_ACTIVE_BYTES = NPU_ACTIVE_BYTES + ROUTER_ACTIVE_BYTES

TENSOR_SPECS = {
    "gate": {
        "suffix": "ffn_gate_exps.weight",
        "shape": (NUM_EXPERTS, INTERMEDIATE, HIDDEN),
        "matrix_shape": (INTERMEDIATE, HIDDEN),
        "expected_qtype": "IQ3_S",
    },
    "up": {
        "suffix": "ffn_up_exps.weight",
        "shape": (NUM_EXPERTS, INTERMEDIATE, HIDDEN),
        "matrix_shape": (INTERMEDIATE, HIDDEN),
        "expected_qtype": "IQ3_S",
    },
    "down": {
        "suffix": "ffn_down_exps.weight",
        "shape": (NUM_EXPERTS, HIDDEN, INTERMEDIATE),
        "matrix_shape": (HIDDEN, INTERMEDIATE),
        "expected_qtype": "IQ4_NL",
    },
}


class ProbeEdge(RuntimeError):
    """A first edge that must close this /rnd lap."""

    def __init__(self, stage: str, detail: str, *, unexplained: bool = False):
        super().__init__(detail)
        self.stage = stage
        self.detail = detail
        self.unexplained = unexplained


@dataclass
class PackedWeights:
    manifest: dict[str, Any]
    packed_paths: dict[str, Path]
    scale_paths: dict[str, Path]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def atomic_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def sha256_file(path: Path, chunk_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def percentile_ms(samples_s: Iterable[float], percentile: float) -> float:
    values = sorted(float(v) for v in samples_s)
    if not values:
        return math.nan
    pos = (len(values) - 1) * percentile
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo] * 1000.0
    frac = pos - lo
    return (values[lo] * (1.0 - frac) + values[hi] * frac) * 1000.0


def metric_pair(reference: Any, observed: Any) -> tuple[float, float]:
    import numpy as np

    ref = np.asarray(reference, dtype=np.float64).reshape(-1)
    got = np.asarray(observed, dtype=np.float64).reshape(-1)
    denom = float(np.linalg.norm(ref))
    got_norm = float(np.linalg.norm(got))
    cosine = float(np.dot(ref, got) / max(denom * got_norm, 1e-30))
    nrmse = float(np.linalg.norm(ref - got) / max(denom, 1e-30))
    return cosine, nrmse


def expand_gguf_shards(first: Path) -> list[Path]:
    match = re.search(r"-(\d{5})-of-(\d{5})\.gguf$", first.name, re.IGNORECASE)
    if not match:
        if not first.is_file():
            raise ProbeEdge("source", f"GGUF does not exist: {first}")
        return [first]
    count = int(match.group(2))
    prefix = first.name[: match.start()]
    shards = [first.with_name(f"{prefix}-{index:05d}-of-{count:05d}.gguf") for index in range(1, count + 1)]
    missing = [str(path) for path in shards if not path.is_file()]
    if missing:
        raise ProbeEdge("source", "missing GGUF shards: " + ", ".join(missing))
    return shards


def load_gguf_tensors(shards: list[Path], gguf_py: Path, layer: int):
    if not gguf_py.is_dir():
        raise ProbeEdge("source", f"gguf-py directory does not exist: {gguf_py}")
    sys.path.insert(0, str(gguf_py))
    try:
        from gguf import GGUFReader, dequantize
    except Exception as exc:
        raise ProbeEdge("source", f"cannot import local gguf-py: {exc}") from exc

    readers = [GGUFReader(str(path), mode="r") for path in shards]
    tensors: dict[str, Any] = {}
    for reader in readers:
        for tensor in reader.tensors:
            tensors[str(tensor.name)] = tensor

    selected: dict[str, Any] = {}
    for role, spec in TENSOR_SPECS.items():
        name = f"blk.{layer}.{spec['suffix']}"
        tensor = tensors.get(name)
        if tensor is None:
            raise ProbeEdge("source", f"required tensor is missing: {name}")
        qtype = getattr(tensor.tensor_type, "name", str(tensor.tensor_type).split(".")[-1])
        if qtype != spec["expected_qtype"]:
            raise ProbeEdge("source", f"{name} has {qtype}, expected {spec['expected_qtype']}")
        sample = dequantize(tensor.data[0], tensor.tensor_type)
        if tuple(sample.shape) != tuple(spec["matrix_shape"]):
            raise ProbeEdge(
                "source",
                f"{name} dequantizes to {tuple(sample.shape)}, expected {spec['matrix_shape']}",
            )
        del sample
        selected[role] = tensor
    return readers, selected, dequantize


def quantize_and_pack(matrix: Any):
    import numpy as np

    matrix = np.asarray(matrix, dtype=np.float32)
    max_abs = np.max(np.abs(matrix), axis=1)
    scale = max_abs / 7.0
    scale = np.where(scale == 0.0, 1.0, scale).astype(np.float32)
    quant = np.rint(matrix / scale[:, None])
    quant = np.clip(quant, -7, 7).astype(np.int8)
    low = quant[:, 0::2].astype(np.uint8) & np.uint8(0x0F)
    high = (quant[:, 1::2].astype(np.uint8) & np.uint8(0x0F)) << np.uint8(4)
    packed = np.bitwise_or(low, high).reshape(-1)
    return packed, scale.astype(np.float16)


def expected_artifact_sizes() -> tuple[dict[str, int], dict[str, int]]:
    packed: dict[str, int] = {}
    scales: dict[str, int] = {}
    for role, spec in TENSOR_SPECS.items():
        rows, cols = spec["matrix_shape"]
        packed[role] = NUM_EXPERTS * rows * cols // 2
        scales[role] = NUM_EXPERTS * rows * 2
    return packed, scales


def cache_is_valid(manifest: dict[str, Any], shards: list[Path], artifact_dir: Path, layer: int) -> bool:
    if manifest.get("format") != "npu0-symmetric-i4-v1" or manifest.get("layer") != layer:
        return False
    source = manifest.get("source_shards", [])
    if len(source) != len(shards):
        return False
    for entry, shard in zip(source, shards):
        stat = shard.stat()
        if entry.get("path") != str(shard) or entry.get("size_bytes") != stat.st_size:
            return False
        if entry.get("mtime_ns") != stat.st_mtime_ns:
            return False
    packed_sizes, scale_sizes = expected_artifact_sizes()
    for role in TENSOR_SPECS:
        packed = artifact_dir / f"layer{layer}-{role}.i4.bin"
        scale = artifact_dir / f"layer{layer}-{role}.scale.f16.bin"
        if not packed.is_file() or packed.stat().st_size != packed_sizes[role]:
            return False
        if not scale.is_file() or scale.stat().st_size != scale_sizes[role]:
            return False
        record = manifest.get("artifacts", {}).get(role, {})
        if record.get("packed_sha256") is None or record.get("scale_sha256") is None:
            return False
        if sha256_file(packed) != record["packed_sha256"]:
            return False
        if sha256_file(scale) != record["scale_sha256"]:
            return False
    return True


def prepare_weights(
    shards: list[Path],
    artifact_dir: Path,
    gguf_py: Path,
    layer: int,
) -> PackedWeights:
    import numpy as np

    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifact_dir / f"layer{layer}-i4-manifest.json"
    if manifest_path.is_file():
        try:
            cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
        if cache_is_valid(cached, shards, artifact_dir, layer):
            print(f"NPU-0: using validated i4 cache {manifest_path}", flush=True)
            return PackedWeights(
                cached,
                {role: artifact_dir / f"layer{layer}-{role}.i4.bin" for role in TENSOR_SPECS},
                {role: artifact_dir / f"layer{layer}-{role}.scale.f16.bin" for role in TENSOR_SPECS},
            )

    print("NPU-0: hashing source shards", flush=True)
    source_rows = []
    for shard in shards:
        stat = shard.stat()
        digest = sha256_file(shard)
        source_rows.append(
            {
                "path": str(shard),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest,
            }
        )
        print(f"  {shard.name}: {digest}", flush=True)

    readers, tensors, dequantize = load_gguf_tensors(shards, gguf_py, layer)

    packed_paths: dict[str, Path] = {}
    scale_paths: dict[str, Path] = {}
    artifact_rows: dict[str, Any] = {}
    packed_sizes, scale_sizes = expected_artifact_sizes()

    for role, spec in TENSOR_SPECS.items():
        rows, cols = spec["matrix_shape"]
        packed_path = artifact_dir / f"layer{layer}-{role}.i4.bin"
        scale_path = artifact_dir / f"layer{layer}-{role}.scale.f16.bin"
        packed_tmp = packed_path.with_suffix(packed_path.suffix + ".tmp")
        scale_tmp = scale_path.with_suffix(scale_path.suffix + ".tmp")
        packed_map = np.memmap(packed_tmp, mode="w+", dtype=np.uint8, shape=(packed_sizes[role],))
        scale_map = np.memmap(scale_tmp, mode="w+", dtype=np.float16, shape=(NUM_EXPERTS, rows))
        bytes_per_expert = rows * cols // 2
        tensor = tensors[role]
        print(f"NPU-0: repacking {role} {spec['expected_qtype']} -> symmetric i4", flush=True)
        for expert in range(NUM_EXPERTS):
            matrix = dequantize(tensor.data[expert], tensor.tensor_type)
            packed, scale = quantize_and_pack(matrix)
            start = expert * bytes_per_expert
            packed_map[start : start + bytes_per_expert] = packed
            scale_map[expert, :] = scale
            del matrix, packed, scale
            if (expert + 1) % 32 == 0:
                print(f"  {role}: {expert + 1}/{NUM_EXPERTS}", flush=True)
        packed_map.flush()
        scale_map.flush()
        del packed_map, scale_map
        os.replace(packed_tmp, packed_path)
        os.replace(scale_tmp, scale_path)
        artifact_rows[role] = {
            "logical_shape": list(spec["shape"]),
            "scale_shape": [NUM_EXPERTS, rows, 1],
            "packed_bytes": packed_path.stat().st_size,
            "scale_bytes": scale_path.stat().st_size,
            "packed_sha256": sha256_file(packed_path),
            "scale_sha256": sha256_file(scale_path),
            "source_quant": spec["expected_qtype"],
            "target_quant": "symmetric_i4_per_output_channel[-7,7]",
        }
        packed_paths[role] = packed_path
        scale_paths[role] = scale_path

    manifest = {
        "format": "npu0-symmetric-i4-v1",
        "created_at": now_iso(),
        "layer": layer,
        "num_experts": NUM_EXPERTS,
        "hidden": HIDDEN,
        "intermediate": INTERMEDIATE,
        "source_shards": source_rows,
        "artifacts": artifact_rows,
        "source_active_bytes_top10": SOURCE_ACTIVE_BYTES,
        "npu_active_bytes_top10_including_scales": NPU_ACTIVE_BYTES,
    }
    atomic_json(manifest_path, manifest)
    # Keep the GGUFReader owners alive through the final mmap-backed tensor read.
    _ = readers
    return PackedWeights(manifest, packed_paths, scale_paths)


def make_named(node: Any, name: str):
    node.set_friendly_name(name)
    return node


def build_model(ov: Any, ops: Any, weights: PackedWeights):
    import numpy as np

    keepalive: list[Any] = []

    def constant(value: Any, dtype: Any | None = None, name: str | None = None):
        node = ops.constant(value, dtype=dtype)
        if name:
            node.set_friendly_name(name)
        return node

    def compressed_weight(role: str):
        spec = TENSOR_SPECS[role]
        tensor = ov.Tensor(ov.Type.i4, list(spec["shape"]))
        raw = np.memmap(weights.packed_paths[role], mode="r", dtype=np.uint8)
        target = tensor.data.view(np.uint8)
        if target.size != raw.size:
            raise ProbeEdge("model-build", f"{role} packed size mismatch: OV={target.size}, file={raw.size}")
        np.copyto(target, raw)
        packed_const = ov.op.Constant(tensor, True)
        packed_const.set_friendly_name(f"npu0.expert.{role}_proj.weight")
        rows, _ = spec["matrix_shape"]
        # shared_memory=True requires writable backing even though the graph treats
        # this as a Constant.  r+ keeps the 1.26 GB cache file-backed without a copy.
        scale = np.memmap(
            weights.scale_paths[role],
            mode="r+",
            dtype=np.float16,
            shape=(NUM_EXPERTS, rows, 1),
        )
        scale_const = ov.op.Constant(scale, True)
        scale_const.set_friendly_name(f"npu0.expert.{role}_proj.weight_scale")
        keepalive.extend((tensor, raw, scale))
        converted = make_named(
            ops.convert(packed_const, ov.Type.f16),
            f"npu0.expert.{role}_proj.weight_convert",
        )
        decompressed = make_named(
            ops.multiply(converted, scale_const),
            f"npu0.expert.{role}_proj.weight_decompress",
        )
        # Qwen3Expert deliberately matches Multiply -> Convert -> MatMul.
        return make_named(
            ops.convert(decompressed, ov.Type.f32),
            f"npu0.expert.{role}_proj.weight_to_compute",
        )

    model_input = make_named(ops.parameter([1, 1, HIDDEN], ov.Type.f32), "npu0.hidden_states")
    input_2d = make_named(
        ops.reshape(model_input, constant([1, HIDDEN], np.int64), False),
        "npu0.expert.input_2d",
    )

    # Deterministic router: the first 512 hidden coordinates map one-to-one to
    # experts.  Keep the exact i4 -> f16 -> scale -> f32 chain required by the
    # released Qwen3Router matcher; a plain f32 Constant silently misses it.
    router_tensor = ov.Tensor(ov.Type.i4, [NUM_EXPERTS, HIDDEN])
    router_packed = router_tensor.data.view(np.uint8)
    router_packed.fill(0)
    diagonal = np.arange(NUM_EXPERTS, dtype=np.int64) * HIDDEN + np.arange(NUM_EXPERTS, dtype=np.int64)
    low_nibbles = diagonal[diagonal % 2 == 0] // 2
    high_nibbles = diagonal[diagonal % 2 == 1] // 2
    router_packed[low_nibbles] |= np.uint8(0x01)
    router_packed[high_nibbles] |= np.uint8(0x10)
    router_const = ov.op.Constant(router_tensor, True)
    router_const.set_friendly_name("npu0.expert.router.weight")
    router_converted = make_named(
        ops.convert(router_const, ov.Type.f16),
        "npu0.expert.router.weight_convert",
    )
    router_scale = np.ones((NUM_EXPERTS, 1), dtype=np.float16)
    router_scale_const = ov.op.Constant(router_scale, True)
    router_scale_const.set_friendly_name("npu0.expert.router.weight_scale")
    router_decompressed = make_named(
        ops.multiply(router_converted, router_scale_const),
        "npu0.expert.router.weight_decompress",
    )
    router_compute = make_named(
        ops.convert(router_decompressed, ov.Type.f32),
        "npu0.expert.router.weight_to_compute",
    )
    keepalive.extend((router_tensor, router_packed, router_scale))
    router_mm = make_named(ops.matmul(input_2d, router_compute, False, True), "npu0.expert.router.matmul")
    router_softmax = make_named(ops.softmax(router_mm, 1), "npu0.expert.router.softmax")
    topk = make_named(
        ops.topk(router_softmax, constant(TOP_K, np.int32), 1, "max", "value", "i64", False),
        "npu0.expert.router.topk",
    )
    # Online partitioning normally writes this key from Qwen3Router.  NPU-7
    # supplies a deliberate offline plan, so carry the exact released key on
    # the original TopK for the partition-stage lookup instead.
    topk.get_rt_info()["npuw_moe_k"] = TOP_K
    router_sum = make_named(
        ops.reduce_sum(topk.output(0), constant([1], np.int64), True),
        "npu0.expert.router.reduce",
    )
    normalized = make_named(ops.divide(topk.output(0), router_sum), "npu0.expert.router.divide")
    router_shape = make_named(ops.shape_of(router_mm, "i64"), "npu0.expert.router.shape")
    zeros = make_named(
        ops.broadcast(constant(0.0, np.float32), router_shape),
        "npu0.expert.router.zeros",
    )
    scattered = make_named(
        ops.scatter_elements_update(zeros, topk.output(1), normalized, constant(1, np.int64)),
        "npu0.expert.router.scatter",
    )
    transposed = make_named(
        ops.transpose(scattered, constant([1, 0], np.int64)),
        "npu0.expert.router.transpose",
    )
    router_reshape = make_named(
        ops.reshape(transposed, constant([NUM_EXPERTS, 1, -1], np.int64), False),
        "npu0.expert.router.reshape",
    )
    router_scores = make_named(
        ops.unsqueeze(router_reshape, constant([3], np.int64)),
        "npu0.expert.router.unsqueeze",
    )

    tiled = make_named(
        ops.tile(input_2d, constant([NUM_EXPERTS, 1], np.int64)),
        "npu0.expert.tile",
    )
    expert_input = make_named(
        ops.reshape(tiled, constant([NUM_EXPERTS, -1, HIDDEN], np.int64), False),
        "npu0.expert.reshape_in",
    )
    gate_mm = make_named(
        ops.matmul(expert_input, compressed_weight("gate"), False, True),
        "npu0.expert.gate_matmul",
    )
    # The Python opset4+ convenience wrapper materializes beta=1 as a second
    # input.  NPUW's Qwen3Expert pattern is deliberately the one-input v4
    # Swish used by OpenVINO's C++ MoE builder, so construct that exact node.
    from openvino.utils.node_factory import NodeFactory

    gate_swish = make_named(
        NodeFactory("opset4").create("Swish", [gate_mm], {}),
        "npu0.expert.swish",
    )
    if gate_swish.get_input_size() != 1:
        raise ProbeEdge("model-build", "Qwen3Expert requires a one-input opset4 Swish")
    up_mm = make_named(
        ops.matmul(expert_input, compressed_weight("up"), False, True),
        "npu0.expert.up_matmul",
    )
    merged = make_named(ops.multiply(gate_swish, up_mm), "npu0.expert.merge")
    down_mm = make_named(
        ops.matmul(merged, compressed_weight("down"), False, True),
        "npu0.expert.down_matmul",
    )
    expert_output = make_named(
        ops.reshape(down_mm, constant([NUM_EXPERTS, 1, -1, HIDDEN], np.int64), False),
        "npu0.expert.reshape_out",
    )
    weighted = make_named(ops.multiply(expert_output, router_scores), "npu0.expert.weighted")
    reduced = make_named(
        ops.reduce_sum(weighted, constant([0], np.int64), False),
        "npu0.expert.reduced",
    )
    output = make_named(
        ops.reshape(reduced, constant([1, 1, HIDDEN], np.int64), False),
        "npu0.output",
    )
    model = ov.Model([output], [model_input], "npu0_qwen38_flash_layer0")
    return model, keepalive


def write_host_routed_plan(model: Any, path: Path) -> None:
    """Write the smallest offline partition that can reach MoEExperts::from.

    The released transform requires one expert-tagged function containing both
    Tile and the final router-score Multiply.  The online REP plan fragmented
    that chain into four functions, so NPU-7 keeps the router upstream and
    makes its score a true expert-function Parameter.  NPU-9 also keeps the
    reduction in the expert function: EXPERT_BATCH emits through the normal
    output link, while OpenVINO 2026.3's separate MoEDownstream prologue
    incorrectly requires the iterative-only expert_output_accumulator.
    """

    groups = [
        {
            "tag": "",
            "inputs": ["npu0.expert.input_2d"],
            "outputs": ["npu0.expert.input_2d", "npu0.expert.router.unsqueeze"],
            "layers": [
                "npu0.expert.input_2d",
                "npu0.expert.router.weight_convert",
                "npu0.expert.router.weight_decompress",
                "npu0.expert.router.weight_to_compute",
                "npu0.expert.router.matmul",
                "npu0.expert.router.shape",
                "npu0.expert.router.zeros",
                "npu0.expert.router.softmax",
                "npu0.expert.router.topk",
                "npu0.expert.router.reduce",
                "npu0.expert.router.divide",
                "npu0.expert.router.scatter",
                "npu0.expert.router.transpose",
                "npu0.expert.router.reshape",
                "npu0.expert.router.unsqueeze",
            ],
        },
        {
            "tag": "expert",
            "inputs": ["npu0.expert.tile", "npu0.expert.weighted"],
            "outputs": ["npu0.expert.reduced"],
            "layers": [
                "npu0.expert.tile",
                "npu0.expert.reshape_in",
                "npu0.expert.gate_proj.weight_convert",
                "npu0.expert.gate_proj.weight_decompress",
                "npu0.expert.gate_proj.weight_to_compute",
                "npu0.expert.gate_matmul",
                "npu0.expert.swish",
                "npu0.expert.up_proj.weight_convert",
                "npu0.expert.up_proj.weight_decompress",
                "npu0.expert.up_proj.weight_to_compute",
                "npu0.expert.up_matmul",
                "npu0.expert.merge",
                "npu0.expert.down_proj.weight_convert",
                "npu0.expert.down_proj.weight_decompress",
                "npu0.expert.down_proj.weight_to_compute",
                "npu0.expert.down_matmul",
                "npu0.expert.reshape_out",
                "npu0.expert.weighted",
                "npu0.expert.reduced",
            ],
        },
        {
            "tag": "",
            "inputs": ["npu0.output"],
            "outputs": ["npu0.output"],
            "layers": ["npu0.output"],
        },
    ]

    assigned = [name for group in groups for name in group["layers"]]
    if len(assigned) != len(set(assigned)):
        raise ProbeEdge("plan-build", "offline HOST_ROUTED plan assigns a layer more than once")
    computational = {
        node.get_friendly_name()
        for node in model.get_ordered_ops()
        if node.get_type_name() not in {"Constant", "Parameter", "Result"}
    }
    assigned_set = set(assigned)
    if assigned_set != computational:
        missing = sorted(computational - assigned_set)
        extra = sorted(assigned_set - computational)
        raise ProbeEdge("plan-build", f"offline plan mismatch: missing={missing} extra={extra}")

    root = ET.Element("ensemble", {"gflops": "1.000000", "irregular_io": "0"})
    partitioning = ET.SubElement(root, "partitioning")
    for index, group in enumerate(groups):
        attributes = {"id": str(index), "gflops": "0.000100"}
        if group["tag"]:
            attributes["tag"] = group["tag"]
        element = ET.SubElement(partitioning, "group", attributes)
        for name in group["inputs"]:
            ET.SubElement(element, "input", {"name": name})
        for name in group["outputs"]:
            ET.SubElement(element, "output", {"name": name})
        for name in group["layers"]:
            ET.SubElement(element, "layer", {"name": name})
    atomic_xml(path, root)


def unpack_expert(path: Path, shape: tuple[int, int], expert: int):
    import numpy as np

    rows, cols = shape
    byte_count = rows * cols // 2
    raw = np.memmap(path, mode="r", dtype=np.uint8, offset=expert * byte_count, shape=(byte_count,))
    logical = np.empty(byte_count * 2, dtype=np.int8)
    low = raw & np.uint8(0x0F)
    high = raw >> np.uint8(4)
    logical[0::2] = low.astype(np.int8)
    logical[1::2] = high.astype(np.int8)
    logical[logical >= 8] -= 16
    return logical.reshape(rows, cols)


def input_for(experts: tuple[int, ...]):
    import numpy as np

    value = np.zeros((1, 1, HIDDEN), dtype=np.float32)
    for rank, expert in enumerate(experts):
        value[0, 0, expert] = 4.0 - rank * 0.1
    return value


def routed_reference(weights: PackedWeights, value: Any):
    import numpy as np

    x = np.asarray(value, dtype=np.float32).reshape(HIDDEN)
    logits = x[:NUM_EXPERTS].copy()
    order = np.argsort(-logits, kind="stable")[:TOP_K]
    shifted = logits - np.max(logits)
    probabilities = np.exp(shifted, dtype=np.float32)
    probabilities /= probabilities.sum(dtype=np.float32)
    selected_scores = probabilities[order]
    selected_scores /= selected_scores.sum(dtype=np.float32)

    scale_maps = {}
    for role, spec in TENSOR_SPECS.items():
        rows, _ = spec["matrix_shape"]
        scale_maps[role] = np.memmap(
            weights.scale_paths[role], mode="r", dtype=np.float16, shape=(NUM_EXPERTS, rows)
        )

    output = np.zeros(HIDDEN, dtype=np.float32)
    for score, expert in zip(selected_scores, order):
        gate_q = unpack_expert(weights.packed_paths["gate"], (INTERMEDIATE, HIDDEN), int(expert))
        up_q = unpack_expert(weights.packed_paths["up"], (INTERMEDIATE, HIDDEN), int(expert))
        down_q = unpack_expert(weights.packed_paths["down"], (HIDDEN, INTERMEDIATE), int(expert))
        gate_weight = (
            gate_q.astype(np.float16) * scale_maps["gate"][expert, :, None]
        ).astype(np.float32)
        up_weight = (
            up_q.astype(np.float16) * scale_maps["up"][expert, :, None]
        ).astype(np.float32)
        gate = gate_weight @ x
        up = up_weight @ x
        activated = gate / (1.0 + np.exp(-gate))
        hidden = activated * up
        down_weight = (
            down_q.astype(np.float16) * scale_maps["down"][expert, :, None]
        ).astype(np.float32)
        down = down_weight @ hidden
        output += np.float32(score) * down
    return order.astype(np.int64), output.reshape(1, 1, HIDDEN)


def partition_has_moe(path: Path) -> tuple[bool, list[str]]:
    if not path.is_file():
        return False, []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return False, []
    tags = [node.attrib.get("tag", "") for node in root.findall(".//group")]
    return any(tag.strip().lower() == "expert" for tag in tags), tags


def infer_once(request: Any, ov: Any, value: Any):
    import numpy as np

    request.set_input_tensor(ov.Tensor(np.asarray(value, dtype=np.float32)))
    start = time.perf_counter()
    request.infer()
    elapsed = time.perf_counter() - start
    output = np.array(request.get_output_tensor(0).data, dtype=np.float32, copy=True)
    return elapsed, output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gguf", required=True, type=Path, help="first Flash GGUF shard")
    parser.add_argument("--layer", type=int, default=0)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--device", default="NPU")
    parser.add_argument(
        "--compiler-type",
        choices=("PLUGIN", "DRIVER", "PREFER_PLUGIN"),
        default="PLUGIN",
        help="NPU compiler venue; PLUGIN bypasses driver-compiler IR ABI skew",
    )
    parser.add_argument("--gguf-py", type=Path, default=Path(r"E:\work\llamacpp-qwen38\gguf-py"))
    parser.add_argument("--bf6-render-queue", default="unknown")
    parser.add_argument(
        "--coresident",
        action="store_true",
        help="stamp that production stayed live for this hardware lap",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="stop after compile so the runtime log can prove /moe_chunk_10 before inference",
    )
    parser.add_argument(
        "--disable-perf-count",
        action="store_true",
        help="compile without NPU profiling instrumentation; keeps all other probe settings fixed",
    )
    parser.add_argument(
        "--enable-npuw-dq",
        action="store_true",
        help="enable NPUW's full dynamic-dequant rewrite before expert unrolling",
    )
    parser.add_argument(
        "--enable-compiler-dq",
        action="store_true",
        help="enable the NPU compiler's weight dynamic-dequantization pass",
    )
    parser.add_argument(
        "--moe-pool-size",
        type=int,
        default=1,
        help="NPUW HOST_ROUTED request-cache entries; 0 exercises the small-tensor copy path",
    )
    parser.add_argument(
        "--disable-runtime-log",
        action="store_true",
        help="disable NPU driver debug logging while leaving NPUW profiling and graph settings unchanged",
    )
    parser.add_argument(
        "--disable-npuw-prof",
        action="store_true",
        help="disable NPUW host-side profiling while leaving PERF_COUNT and runtime logging unchanged",
    )
    parser.add_argument(
        "--enable-npu-turbo",
        action="store_true",
        help="enable the NPU driver's turbo execution mode",
    )
    return parser.parse_args(argv)


def rerun_command(args: argparse.Namespace) -> str:
    return (
        f'& "{Path(sys.executable)}" "{Path(__file__).resolve()}" '
        f'--gguf "{args.gguf.resolve()}" --layer {args.layer} '
        f'--artifact-dir "{args.artifact_dir.resolve()}" '
        f'--receipts "{args.receipts.resolve()}" --device "{args.device}" '
        f'--compiler-type "{args.compiler_type}" '
        f'--gguf-py "{args.gguf_py.resolve()}" '
        f'--bf6-render-queue "{args.bf6_render_queue}"'
        + (" --coresident" if args.coresident else "")
        + (" --compile-only" if args.compile_only else "")
        + (" --disable-perf-count" if args.disable_perf_count else "")
        + (" --enable-npuw-dq" if args.enable_npuw_dq else "")
        + (" --enable-compiler-dq" if args.enable_compiler_dq else "")
        + (f" --moe-pool-size {args.moe_pool_size}" if args.moe_pool_size != 1 else "")
        + (" --disable-runtime-log" if args.disable_runtime_log else "")
        + (" --disable-npuw-prof" if args.disable_npuw_prof else "")
        + (" --enable-npu-turbo" if args.enable_npu_turbo else "")
    )


def base_receipt(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ts": now_iso(),
        "probe": PROBE,
        "coresident": args.coresident,
        "bf6_render_queue": args.bf6_render_queue,
        "perf_count": not args.disable_perf_count,
        "npuw_dq": args.enable_npuw_dq,
        "compiler_dq": args.enable_compiler_dq,
        "moe_pool_size": args.moe_pool_size,
        "runtime_debug_log": not args.disable_runtime_log,
        "npuw_prof": not args.disable_npuw_prof,
        "npu_turbo": args.enable_npu_turbo,
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if "ZE_AFFINITY_MASK" in os.environ:
        raise ProbeEdge("preflight", "ZE_AFFINITY_MASK is set; refusing known-deadlock configuration")
    if args.layer != 0:
        raise ProbeEdge("preflight", "NPU-0 is intentionally pinned to layer 0")
    if args.moe_pool_size < 0:
        raise ProbeEdge("preflight", "--moe-pool-size must be non-negative")

    # NPUW's own logger reads these at library initialization.  Production
    # wheels may compile developer logging out, but setting them is harmless
    # and preserves evidence when it is available.
    os.environ.setdefault("OPENVINO_NPUW_LOG_LEVEL", "DEBUG")
    if args.disable_npuw_prof:
        os.environ["OPENVINO_NPUW_PROF"] = "NO"
    else:
        os.environ.setdefault("OPENVINO_NPUW_PROF", "YES")
    if args.disable_runtime_log:
        os.environ["OV_NPU_LOG_LEVEL"] = "LOG_NONE"
    else:
        os.environ.setdefault("OV_NPU_LOG_LEVEL", "LOG_DEBUG")

    try:
        import numpy as np
        import openvino as ov
        import openvino.opset13 as ops
    except Exception as exc:
        raise ProbeEdge("preflight", f"OpenVINO/Numpy import failed: {exc}") from exc

    if not str(ov.__version__).startswith("2026.3"):
        raise ProbeEdge("preflight", f"OpenVINO must be 2026.3.x, found {ov.__version__}")

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    shards = expand_gguf_shards(args.gguf.resolve())
    core = ov.Core()
    if args.device not in core.available_devices:
        raise ProbeEdge("preflight", f"{args.device} unavailable; devices={core.available_devices}")

    prop_names = [str(item) for item in core.get_property(args.device, "SUPPORTED_PROPERTIES")]

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
            "compiler_type": str(get_property("NPU_COMPILER_TYPE", "unknown")),
            "compiler_version": str(get_property("NPU_COMPILER_VERSION", "unknown")),
            "npu_total_mem_bytes": int(get_property("NPU_DEVICE_TOTAL_MEM_SIZE", 0) or 0),
            "optimization_capabilities": [str(v) for v in (get_property("OPTIMIZATION_CAPABILITIES", []) or [])],
            "npuw_property_surface": "NPUW_MOE_POOL_SIZE" in prop_names,
            "requested_compiler_type": args.compiler_type,
            "source_active_bytes": SOURCE_ACTIVE_BYTES,
            "npu_active_bytes": NPU_ACTIVE_BYTES,
            "npu_active_bytes_scope": "top10_routed_experts_only",
            "synthetic_router_active_bytes": ROUTER_ACTIVE_BYTES,
            "modeled_graph_active_bytes": MODELED_GRAPH_ACTIVE_BYTES,
        }
    )
    append_jsonl(args.receipts, capability)
    print(json.dumps(capability, sort_keys=True), flush=True)
    if capability["arch"] != "3720":
        raise ProbeEdge(
            "preflight",
            f"NPU-0 is pinned to architecture 3720, found {capability['arch']}",
        )

    weights = prepare_weights(shards, args.artifact_dir, args.gguf_py.resolve(), args.layer)
    try:
        model, keepalive = build_model(ov, ops, weights)
    except ProbeEdge:
        raise
    except Exception as exc:
        raise ProbeEdge("model-build", f"cannot build matcher-compatible Qwen3 graph: {exc}") from exc

    plan_id = f"{os.getpid()}-{time.time_ns()}"
    partition_plan = args.artifact_dir / f"npu0-host-routed-plan-{plan_id}.xml"
    write_host_routed_plan(model, partition_plan)
    config = {
        "NPU_USE_NPUW": "YES",
        "NPUW_DEVICES": args.device,
        "NPU_COMPILER_TYPE": args.compiler_type,
        "NPUW_PLAN": str(partition_plan),
        "NPUW_FOLD": True,
        "NPUW_UNFOLD_IREQS": "NO",
        "NPUW_MOE_POOL_SIZE": args.moe_pool_size,
        "NPUW_FUNCALL_FOR_ALL": "YES",
        "NPUW_FALLBACK_EXEC": "NO",
        "PERFORMANCE_HINT": "LATENCY",
        "PERF_COUNT": not args.disable_perf_count,
        "LOG_LEVEL": "LOG_NONE" if args.disable_runtime_log else "LOG_DEBUG",
    }
    if args.enable_npuw_dq:
        config["NPUW_DQ"] = True
    if args.enable_compiler_dq:
        config["NPU_COMPILER_DYNAMIC_QUANTIZATION"] = True
    if args.enable_npu_turbo:
        config["NPU_TURBO"] = True
    config_path = args.artifact_dir / "npu0-config.json"
    atomic_json(config_path, {key: str(value) for key, value in config.items()})
    print("NPU-0: compiling exact Flash-shaped graph through NPUW HOST_ROUTED", flush=True)
    compile_start = time.perf_counter()
    try:
        compiled = core.compile_model(model, args.device, config)
    except Exception as exc:
        raise ProbeEdge("compile", f"OpenVINO/NPUW compile failed: {exc}") from exc
    compile_ms = (time.perf_counter() - compile_start) * 1000.0
    try:
        actual_compiler_type = str(compiled.get_property("NPU_COMPILER_TYPE"))
    except Exception:
        actual_compiler_type = "unknown"

    moe_partition, partition_tags = partition_has_moe(partition_plan)
    if not moe_partition:
        raise ProbeEdge(
            "matcher",
            "NPUW compiled without an exact expert-tagged partition; "
            f"tags={partition_tags}, plan={partition_plan}",
        )

    if args.compile_only:
        compile_row = base_receipt(args)
        compile_row.update(
            {
                "cell": "compile-only",
                "result": "compiled-awaiting-transform-audit",
                "compile_ms": round(compile_ms, 3),
                "compiler_type": actual_compiler_type,
                "partition_plan": str(partition_plan),
                "partition_tags": partition_tags,
                "expected_transform_artifact": "/moe_chunk_10",
                "expected_expert_convolutions_total": 3 * TOP_K,
                "expected_expert_convolutions_per_projection": TOP_K,
                "inference_attempted": False,
                "rerun": rerun_command(args),
                "uncertainties": [
                    "runtime log must prove /moe_chunk_10 before inference",
                    "compiler lowering must contain 10 rather than 512 expert convolutions",
                    "runtime expert tensor import versus shadow copy remains unproven",
                ],
            }
        )
        append_jsonl(args.receipts, compile_row)
        atomic_json(args.artifact_dir / "npu0-summary.json", compile_row)
        print(json.dumps(compile_row, sort_keys=True), flush=True)
        return compile_row

    value_a = input_for(SET_A)
    value_b = input_for(SET_B)
    ids_a, reference_a = routed_reference(weights, value_a)
    ids_b, reference_b = routed_reference(weights, value_b)
    if tuple(ids_a.tolist()) != SET_A or tuple(ids_b.tolist()) != SET_B:
        raise ProbeEdge(
            "router-control",
            f"deterministic CPU router selected A={ids_a.tolist()} B={ids_b.tolist()}",
            unexplained=True,
        )

    request = compiled.create_infer_request()
    try:
        first_s, first_output = infer_once(request, ov, value_a)
        for _ in range(WARMUPS):
            infer_once(request, ov, value_a)
        fixed_times: list[float] = []
        fixed_output = first_output
        for _ in range(TIMED_ITERS):
            elapsed, fixed_output = infer_once(request, ov, value_a)
            fixed_times.append(elapsed)
        alternating_times: list[float] = []
        alternating_a = fixed_output
        alternating_b = None
        for index in range(TIMED_ITERS):
            value = value_a if index % 2 == 0 else value_b
            elapsed, output = infer_once(request, ov, value)
            alternating_times.append(elapsed)
            if index % 2 == 0:
                alternating_a = output
            else:
                alternating_b = output
        _, replay_a = infer_once(request, ov, value_a)
    except Exception as exc:
        raise ProbeEdge("infer", f"NPUW inference/rebind failed: {exc}") from exc
    finally:
        # NPUW retains these host tensors for expert slicing; keep them live through infer.
        _ = keepalive

    if alternating_b is None:
        raise ProbeEdge("infer", "alternating sample did not produce a B output", unexplained=True)

    cosine_a, nrmse_a = metric_pair(reference_a, alternating_a)
    cosine_b, nrmse_b = metric_pair(reference_b, alternating_b)
    _, replay_nrmse = metric_pair(alternating_a, replay_a)
    ab_delta = float(np.linalg.norm(alternating_a.astype(np.float64) - alternating_b.astype(np.float64)))
    aa_delta = float(np.linalg.norm(alternating_a.astype(np.float64) - replay_a.astype(np.float64)))
    separated = ab_delta > 10.0 * max(aa_delta, 1e-12)
    fixed_p50 = percentile_ms(fixed_times, 0.50)
    fixed_p95 = percentile_ms(fixed_times, 0.95)
    alternating_p50 = percentile_ms(alternating_times, 0.50)
    alternating_p95 = percentile_ms(alternating_times, 0.95)
    rebind_delta_ms = max(0.0, alternating_p95 - fixed_p95)
    output_correct = (
        cosine_a >= 0.999
        and cosine_b >= 0.999
        and nrmse_a <= 0.01
        and nrmse_b <= 0.01
        and replay_nrmse <= 0.001
        and separated
    )

    # Persist raw inference evidence before the correctness gate.  A failed
    # oracle remains a first edge and admits no performance verdict, but its
    # one permitted sample must not disappear as NPU-5's timing did.
    diagnostic_row = base_receipt(args)
    diagnostic_row.update(
        {
            "cell": "inference-diagnostic",
            "result": "raw-sample-no-performance-verdict",
            "compile_ms": round(compile_ms, 3),
            "first_ms": round(first_s * 1000.0, 6),
            "fixed_times_ms": [round(value * 1000.0, 6) for value in fixed_times],
            "alternating_times_ms": [round(value * 1000.0, 6) for value in alternating_times],
            "fixed_p50_ms": round(fixed_p50, 6),
            "fixed_p95_ms": round(fixed_p95, 6),
            "alternating_p50_ms": round(alternating_p50, 6),
            "alternating_p95_ms": round(alternating_p95, 6),
            "alternating_minus_fixed_p95_us": round(rebind_delta_ms * 1000.0, 3),
            "cosine_a": round(cosine_a, 9),
            "cosine_b": round(cosine_b, 9),
            "nrmse_a": round(nrmse_a, 9),
            "nrmse_b": round(nrmse_b, 9),
            "replay_nrmse": round(replay_nrmse, 9),
            "ab_delta_l2": ab_delta,
            "aa_replay_delta_l2": aa_delta,
            "ab_separated": separated,
            "correctness_gate_passed": output_correct,
            "physical_ddr_bytes_proven": False,
            "performance_verdict_admitted": False,
            "partition_plan": str(partition_plan),
        }
    )
    append_jsonl(args.receipts, diagnostic_row)
    atomic_json(args.artifact_dir / f"npu0-inference-diagnostic-{plan_id}.json", diagnostic_row)
    print(json.dumps(diagnostic_row, sort_keys=True), flush=True)

    if not output_correct:
        raise ProbeEdge(
            "correctness",
            "NPU output failed the predeclared correctness gate: "
            f"cosA={cosine_a:.6g} nrmseA={nrmse_a:.6g} "
            f"cosB={cosine_b:.6g} nrmseB={nrmse_b:.6g} "
            f"replay={replay_nrmse:.6g} separated={separated}",
            unexplained=True,
        )

    # Contract goodput is source-format weight bytes per median inference.  Keep
    # p95 and OpenVINO-repacked traffic as separate fields so neither is hidden.
    effective_gbps = SOURCE_ACTIVE_BYTES / (alternating_p50 / 1000.0) / 1e9
    source_effective_gbps_p95 = SOURCE_ACTIVE_BYTES / (alternating_p95 / 1000.0) / 1e9
    ov_effective_gbps_p50 = NPU_ACTIVE_BYTES / (alternating_p50 / 1000.0) / 1e9
    ov_effective_gbps_p95 = NPU_ACTIVE_BYTES / (alternating_p95 / 1000.0) / 1e9
    modeled_graph_gbps_p50 = MODELED_GRAPH_ACTIVE_BYTES / (alternating_p50 / 1000.0) / 1e9
    modeled_graph_gbps_p95 = MODELED_GRAPH_ACTIVE_BYTES / (alternating_p95 / 1000.0) / 1e9
    projected_48_layer_ms = alternating_p95 * 48.0

    fixed_row = base_receipt(args)
    fixed_row.update(
        {
            "cell": "fixed",
            "iterations": TIMED_ITERS,
            "warmups": WARMUPS,
            "compile_ms": round(compile_ms, 3),
            "compiler_type": actual_compiler_type,
            "first_ms": round(first_s * 1000.0, 3),
            "p50_ms": round(fixed_p50, 6),
            "p95_ms": round(fixed_p95, 6),
            "output_correct": True,
            "cosine": round(cosine_a, 9),
            "nrmse": round(nrmse_a, 9),
        }
    )
    alternating_row = base_receipt(args)
    alternating_row.update(
        {
            "cell": "alternating",
            "iterations": TIMED_ITERS,
            "sets": [list(SET_A), list(SET_B)],
            "p50_ms": round(alternating_p50, 6),
            "p95_ms": round(alternating_p95, 6),
            "rebind_delta_us": round(rebind_delta_ms * 1000.0, 3),
            "weight_bytes": SOURCE_ACTIVE_BYTES,
            "source_weight_bytes": SOURCE_ACTIVE_BYTES,
            "routed_expert_repacked_bytes": NPU_ACTIVE_BYTES,
            "synthetic_router_active_bytes": ROUTER_ACTIVE_BYTES,
            "modeled_graph_active_bytes": MODELED_GRAPH_ACTIVE_BYTES,
            "effective_gbps": round(effective_gbps, 6),
            "effective_gbps_basis": "modeled_source_routed_expert_bytes/alternating_p50",
            "physical_ddr_bytes_proven": False,
            "source_effective_gbps_p95": round(source_effective_gbps_p95, 6),
            "ov_effective_gbps_p50": round(ov_effective_gbps_p50, 6),
            "ov_effective_gbps_p95": round(ov_effective_gbps_p95, 6),
            "modeled_graph_gbps_p50": round(modeled_graph_gbps_p50, 6),
            "modeled_graph_gbps_p95": round(modeled_graph_gbps_p95, 6),
            "projected_48_layer_ms": round(projected_48_layer_ms, 6),
            "output_correct": True,
            "cosine_a": round(cosine_a, 9),
            "cosine_b": round(cosine_b, 9),
            "nrmse_a": round(nrmse_a, 9),
            "nrmse_b": round(nrmse_b, 9),
            "replay_nrmse": round(replay_nrmse, 9),
            "ab_separated": separated,
        }
    )
    append_jsonl(args.receipts, fixed_row)
    append_jsonl(args.receipts, alternating_row)
    print(json.dumps(fixed_row, sort_keys=True), flush=True)
    print(json.dumps(alternating_row, sort_keys=True), flush=True)

    if alternating_p95 <= 0.50 and rebind_delta_ms <= 0.20 and source_effective_gbps_p95 >= 45.0:
        performance_class = "green"
    elif alternating_p95 >= 0.75 or source_effective_gbps_p95 < 30.0:
        performance_class = "retire-solo-path"
    else:
        performance_class = "amber"

    summary = {
        "probe": PROBE,
        "completed_at": now_iso(),
        "result": "host-routed-candidate-expert-isolated",
        "performance_class": performance_class,
        "compile_ms": compile_ms,
        "compiler_type": actual_compiler_type,
        "first_ms": first_s * 1000.0,
        "fixed_p50_ms": fixed_p50,
        "fixed_p95_ms": fixed_p95,
        "alternating_p50_ms": alternating_p50,
        "alternating_p95_ms": alternating_p95,
        "rebind_delta_us": rebind_delta_ms * 1000.0,
        "effective_gbps": effective_gbps,
        "effective_gbps_basis": "modeled_source_routed_expert_bytes/alternating_p50",
        "physical_ddr_bytes_proven": False,
        "source_effective_gbps_p95": source_effective_gbps_p95,
        "ov_effective_gbps_p50": ov_effective_gbps_p50,
        "ov_effective_gbps_p95": ov_effective_gbps_p95,
        "modeled_graph_gbps_p50": modeled_graph_gbps_p50,
        "modeled_graph_gbps_p95": modeled_graph_gbps_p95,
        "projected_48_layer_ms": projected_48_layer_ms,
        "partition_plan": str(partition_plan),
        "partition_tags": partition_tags,
        "source_manifest": weights.manifest,
        "rerun": rerun_command(args),
        "uncertainties": [
            "runtime log must still prove no expert-weight shadow copy",
            "one-layer timing may not hold across 48 layers on shared DDR",
            "full qwen4_exp export topology and llama.cpp integration remain untested",
        ],
    }
    atomic_json(args.artifact_dir / "npu0-summary.json", summary)
    verdict = base_receipt(args)
    verdict.update(
        {
            "cell": "verdict",
            "result": summary["result"],
            "detail": (
                f"{performance_class}; alt_p95={alternating_p95:.6f}ms; "
                f"rebind_delta={rebind_delta_ms * 1000.0:.3f}us; "
                f"source_p50_effective={effective_gbps:.3f}GB/s; "
                f"source_p95_effective={source_effective_gbps_p95:.3f}GB/s; "
                "inspect runtime log before promotion"
            ),
            "rerun": summary["rerun"],
            "uncertainties": summary["uncertainties"],
        }
    )
    append_jsonl(args.receipts, verdict)
    print(json.dumps(verdict, sort_keys=True), flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = rerun_command(args)
    try:
        execute(args)
        return 0
    except ProbeEdge as edge:
        prefix = "can't answer why: " if edge.unexplained else ""
        detail = prefix + edge.detail
        payload = base_receipt(args)
        payload.update(
            {
                "cell": "verdict",
                "result": "first-edge",
                "edge": edge.stage,
                "detail": detail,
                "rerun": command,
                "uncertainties": [detail],
            }
        )
        try:
            append_jsonl(args.receipts, payload)
            atomic_json(args.artifact_dir / "npu0-edge.json", payload)
        except Exception as receipt_exc:
            print(f"NPU-0: could not persist edge receipt: {receipt_exc}", file=sys.stderr)
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 2
    except Exception as exc:
        detail = f"can't answer why: unhandled {type(exc).__name__}: {exc}"
        payload = base_receipt(args)
        payload.update(
            {
                "cell": "verdict",
                "result": "first-edge",
                "edge": "unhandled",
                "detail": detail,
                "rerun": command,
                "uncertainties": [detail],
                "traceback": traceback.format_exc(),
            }
        )
        try:
            append_jsonl(args.receipts, payload)
            atomic_json(args.artifact_dir / "npu0-edge.json", payload)
        except Exception as receipt_exc:
            print(f"NPU-0: could not persist edge receipt: {receipt_exc}", file=sys.stderr)
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
