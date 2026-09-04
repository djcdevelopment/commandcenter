"""KV hydration across swaps, with the naming manifest (ADR-0040 §3, ADR-0045 P3).

Measured on this stack (W0 P2/P3): a 29,320-token slot saved 2.68 GB in 1.74 s and restored in
1.19 s across a full restart, after which the identical prompt re-evaluated ONE token (0.84 s vs
102.8 s cold prefill). Save+restore ~3 s against ~100 s re-prefill is what makes rotation cheap.

The slot file format carries NO model identity (P4: the server refuses the common cross-model
case with HTTP 400, structurally, but that is a guard of last resort). Identity therefore lives
in the NAME, ``{model_id}.{slot}.{prompt_hash}.bin``, and in this manifest; ``restore_slot``
refuses a cross-model restore BEFORE any HTTP call.

Pure except for the manifest file and the injected client.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_MANIFEST = Path("C:/work/commandcenter/hearth/var/kv-manifest.json")
DEFAULT_SLOT_DIR = Path("E:/work/battlemage/kv")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def prompt_hash(prompt: str) -> str:
    """Stable 16-hex prefix of the prompt's SHA-256 (the naming key, not a secret)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def sanitize_model_id(model_id: str) -> str:
    return _SAFE.sub("_", model_id.strip()) or "model"


def kv_filename(model_id: str, slot: int, digest: str) -> str:
    return f"{sanitize_model_id(model_id)}.{int(slot)}.{digest}.bin"


@dataclass(frozen=True)
class KvEntry:
    filename: str
    model_id: str
    slot: int
    prompt_hash: str
    n_tokens: Optional[int]
    bytes: Optional[int]
    saved_at: str


class KvManifest:
    """``{filename: KvEntry}`` persisted as JSON; writes are atomic (temp + replace)."""

    def __init__(self, path: Path | str = DEFAULT_MANIFEST) -> None:
        self.path = Path(path)
        self.entries: dict[str, KvEntry] = {}
        self.load()

    def load(self) -> None:
        self.entries = {}
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            return
        for name, item in (raw.get("entries") or {}).items():
            try:
                self.entries[name] = KvEntry(**item)
            except TypeError:
                continue

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"contract_version": "kv-manifest.v1",
               "entries": {k: asdict(v) for k, v in sorted(self.entries.items())}}
        fd, tmp = tempfile.mkstemp(prefix=".kv-manifest-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(doc, handle, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def record(self, entry: KvEntry) -> None:
        self.entries[entry.filename] = entry
        self.save()

    def lookup(self, model_id: str, digest: str, slot: Optional[int] = None) -> Optional[KvEntry]:
        for entry in self.entries.values():
            if entry.model_id == model_id and entry.prompt_hash == digest \
                    and (slot is None or entry.slot == slot):
                return entry
        return None

    def by_filename(self, filename: str) -> Optional[KvEntry]:
        return self.entries.get(filename)


class CrossModelRestore(ValueError):
    """A slot file recorded for one model was asked to hydrate another."""


def save_slot(client, model_id: str, slot: int, prompt: str,
              manifest: KvManifest, timeout_s: float = 120.0) -> KvEntry:
    """Process ``prompt`` on the model (``cache_prompt``), then ``/slots/{slot}?action=save``
    under the manifest name; records the entry on success.

    The save alone captures whatever the slot last held (2026-09-03: one canary token, 205 KB);
    the prompt has to be prefilled first for the file to carry its state.
    """
    digest = prompt_hash(prompt)
    filename = kv_filename(model_id, slot, digest)
    pre = client.completion(model_id, prompt, n_predict=1, cache_prompt=True, timeout_s=timeout_s)
    timings = pre.get("timings") if pre.get("ok") else None
    if not isinstance(timings, dict) or not timings:
        raise RuntimeError(f"prefill before save failed for {model_id}: {pre.get('error') or 'no timings'}")
    result = client.slot_action(model_id, slot, "save", filename, timeout_s=timeout_s)
    if not result.get("ok", False) or result.get("error"):
        raise RuntimeError(f"slot save failed for {model_id} slot {slot}: {result.get('error')}")
    if result.get("filename") not in (None, filename):
        raise RuntimeError(f"server saved under {result.get('filename')!r}, expected {filename!r}")
    entry = KvEntry(filename, model_id, int(slot), digest,
                    result.get("n_saved"), result.get("n_written"),
                    datetime.now(timezone.utc).isoformat())
    manifest.record(entry)
    return entry


def restore_slot(client, model_id: str, slot: int, prompt: str,
                 manifest: KvManifest, timeout_s: float = 120.0) -> dict:
    """Hydrate ``slot`` of ``model_id`` from the manifest entry for this prompt.

    Refuses -- before any HTTP -- when the manifest holds no entry for (model_id, prompt),
    or when the only entries for that prompt belong to a different model (the P4 case).
    """
    digest = prompt_hash(prompt)
    entry = manifest.lookup(model_id, digest)
    if entry is None:
        others = [e for e in manifest.entries.values() if e.prompt_hash == digest]
        if others:
            raise CrossModelRestore(
                f"slot state for this prompt belongs to {sorted({e.model_id for e in others})}, "
                f"not {model_id!r}; the file format carries no identity, so this is refused here")
        raise LookupError(f"no saved slot for {model_id!r} with prompt hash {digest}")
    result = client.slot_action(model_id, slot, "restore", entry.filename, timeout_s=timeout_s)
    if not result.get("ok", False) or result.get("error"):
        raise RuntimeError(f"slot restore failed for {model_id} slot {slot}: {result.get('error')}")
    # The proof of hydration: the same prompt again costs ~1 prompt token, not a re-prefill.
    post = client.completion(model_id, prompt, n_predict=1, cache_prompt=True, timeout_s=timeout_s)
    verify = post.get("timings") if post.get("ok") else None
    return {"ok": True, "entry": asdict(entry), "n_restored": result.get("n_restored"),
            "n_read": result.get("n_read"), "timings": result.get("timings"),
            "verify_timings": verify,
            "prompt_n_after_restore": (verify or {}).get("prompt_n"),
            "cache_n_after_restore": (verify or {}).get("cache_n")}
