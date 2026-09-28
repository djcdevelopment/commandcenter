"""Export the pinned MiniLM-L6 encoder to OpenVINO IR at a static window (ADR-0050, lap N1).

    ~/.venvs/npu/bin/python tools/sizer/export.py [--model DIR] [--out DIR] [--seq 256] [--int8]

Writes <out>/<fp16|int8>/openvino_model.{xml,bin} plus the tokenizer, reshaped to [1, seq] (the NPU
compiles static shapes only), and checks the exported embedding against the torch reference on ten
probe sentences (masked mean pooling, L2 norm -- the same recipe retrieval_lap.py:44-58 uses).
Refuses to overwrite a directory whose manifest records a different source sha.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

DEFAULT_MODEL = Path("/home/derek/models/all-MiniLM-L6-v2-1110a243fdf4706b3f48f1d95db1a4f5529b4d41")
DEFAULT_OUT = Path.home() / "models" / "minilm-ov"
PROBES = [
    "In at most 250 words: how does this module decide what to dispatch in one tick?",
    "Rewrite this module with type hints throughout and keep behaviour identical.",
    "List every environment variable this module reads.",
    "Which line defines the retry limit? Answer with just the number.",
    "Translate this README to Spanish and return the whole file.",
    "Summarize the log.",
    "Describe each collect_* function: which files it reads and what rows it emits.",
    "Draft the ADR for the sizer, 800-1200 words, with a Consequences section.",
    "Name every class and method in this module. Count them.",
    "Find every TODO or FIXME line and quote it with its line number.",
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pooled(last_hidden, mask):
    import numpy as np
    m = mask[..., None].astype(last_hidden.dtype)
    emb = (last_hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--int8", action="store_true", help="also write an INT8 weight-compressed variant (nncf)")
    args = ap.parse_args()

    import numpy as np
    import openvino as ov
    import torch
    from transformers import AutoModel, AutoTokenizer

    weights = args.model / "model.safetensors"
    src_sha = sha256_file(weights) if weights.exists() else "unknown"
    tok = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    # eager attention: transformers 5 SDPA mask construction does not trace under torch.jit (IndexError on q_length)
    ref = AutoModel.from_pretrained(args.model, local_files_only=True, attn_implementation="eager").eval().cpu()

    enc = tok(PROBES, padding="max_length", max_length=args.seq, truncation=True, return_tensors="pt")
    with torch.no_grad():
        ref_out = ref(**enc).last_hidden_state.numpy()
    ref_emb = pooled(ref_out, enc["attention_mask"].numpy())

    # optimum's exporter knows transformers 5's mask construction; a bare torch trace does not
    # (IndexError in sdpa_mask, 2026-09-28). Export once at dynamic shape, then pin [1, seq].
    t0 = time.perf_counter()
    from optimum.exporters.openvino import main_export
    staged = args.out / "_optimum_fp32"
    if not (staged / "openvino_model.xml").exists():
        main_export(model_name_or_path=str(args.model), output=staged, task="feature-extraction",
                    library_name="transformers", local_files_only=True)
    core = ov.Core()
    ov_model = core.read_model(str(staged / "openvino_model.xml"))
    ov_model.reshape({inp.get_any_name(): [1, args.seq] for inp in ov_model.inputs})
    convert_s = round(time.perf_counter() - t0, 2)

    variants = {"fp16": ov_model}
    if args.int8:
        import nncf
        variants["int8"] = nncf.compress_weights(ov_model.clone(), mode=nncf.CompressWeightsMode.INT8_ASYM)

    report = {"source": str(args.model), "source_sha256": src_sha, "seq": args.seq, "convert_s": convert_s,
              "inputs": [i.get_any_name() for i in ov_model.inputs],
              "outputs": [sorted(o.get_names()) for o in ov_model.outputs], "variants": {}}
    for name, model in variants.items():
        out = args.out / name
        manifest = out / "manifest.json"
        if manifest.exists():
            prior = json.loads(manifest.read_text())
            if prior.get("source_sha256") != src_sha:
                print(f"refusing to overwrite {out}: manifest sha {prior.get('source_sha256')} != source {src_sha}", file=sys.stderr)
                return 2
        out.mkdir(parents=True, exist_ok=True)
        ov.save_model(model, str(out / "openvino_model.xml"), compress_to_fp16=(name == "fp16"))
        tok.save_pretrained(out)
        compiled = core.compile_model(core.read_model(str(out / "openvino_model.xml")), "CPU")
        names = {i.get_any_name() for i in compiled.inputs}
        embs = []
        for i in range(len(PROBES)):
            feed = {k: enc[k][i:i + 1].numpy() for k in enc if k in names}
            outs = compiled(feed)
            hidden = next((outs[o] for o in compiled.outputs if "last_hidden_state" in o.get_names()), outs[compiled.output(0)])
            embs.append(pooled(hidden, enc["attention_mask"][i:i + 1].numpy())[0])
        cos = [float(np.dot(a, b)) for a, b in zip(np.stack(embs), ref_emb)]
        xml_sha = sha256_file(out / "openvino_model.xml"); bin_sha = sha256_file(out / "openvino_model.bin")
        info = {"dir": str(out), "cosine_min": round(min(cos), 5), "cosine_mean": round(sum(cos) / len(cos), 5),
                "bin_bytes": (out / "openvino_model.bin").stat().st_size, "xml_sha256": xml_sha, "bin_sha256": bin_sha}
        manifest.write_text(json.dumps({"schema": "minilm-ov-export.v1", "variant": name, "source": str(args.model),
                                        "source_sha256": src_sha, "seq": args.seq, "openvino": ov.__version__,
                                        "pooling": "masked-mean-l2", **info}, indent=2))
        report["variants"][name] = info
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
