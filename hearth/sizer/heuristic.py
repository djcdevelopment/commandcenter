"""The heuristic request sizer: expected output class from the request's own shape.

Pure, stdlib-only, sub-millisecond. It answers one question the ledger says the
door never asks: HOW MUCH is this call going to write? Measured on the Linux
ledger (1,039 succeeded text jobs since 2026-09-23): outputs p50 77 tokens, p90
279, p99 604, max 5,690, while inputs run p50 2.9 KB / p90 32 KB. Output is
about a tenth of input on every lane, so "output larger than the prompt" is a
declared-intent case (a word budget on a summary, a rewrite, a code candidate),
never the norm -- and that intent is readable off the request text.

Bins (upper edge in tokens; the edge, not a midpoint, is what admission reserves
so a right-bin guess never under-reserves):

    xs  <  128   one line, a number, a label, a yes/no
    s   <  384   a short list, a paragraph, a grep result
    m   < 1024   a summary or review with a few-hundred-word budget
    l   < 2048   a draft, a long report, a rewrite of a short file
    xl  >= 2048  a whole-file rewrite, a long translation, a code candidate

Precedence of signals, highest first: an explicit budget in the text ("at most
250 words", "max_report_words: 800"), then the verb class of the instruction
scaled by the input size (a rewrite grows with its input; a summary grows with
the log of it), then the declared family's ledger median, then the default (s).
Every decision names the signal it used so a wrong bin can be traced.
"""
from __future__ import annotations

import re
import time
from typing import Optional

BINS: tuple[tuple[str, int], ...] = (("xs", 128), ("s", 384), ("m", 1024), ("l", 2048), ("xl", 4096))
BIN_ORDER = tuple(name for name, _ in BINS)
BIN_EDGE = dict(BINS)
LONG_BINS = frozenset({"l", "xl"})

# Words -> tokens: English prose tokenises at ~1.3-1.5 tokens/word on the Qwen
# tokenizer; 1.4 with 15 % headroom keeps the reserve honest for a full budget.
_TOKENS_PER_WORD = 1.4
_TOKENS_PER_LINE = 14
_TOKENS_PER_SENTENCE = 22
_BUDGET_HEADROOM = 1.15

_UNIT_TOKENS = {
    "word": _TOKENS_PER_WORD, "words": _TOKENS_PER_WORD,
    "token": 1.0, "tokens": 1.0,
    "line": _TOKENS_PER_LINE, "lines": _TOKENS_PER_LINE,
    "sentence": _TOKENS_PER_SENTENCE, "sentences": _TOKENS_PER_SENTENCE,
    "bullet": _TOKENS_PER_LINE, "bullets": _TOKENS_PER_LINE,
    "item": _TOKENS_PER_LINE, "items": _TOKENS_PER_LINE,
    "character": 0.25, "characters": 0.25, "char": 0.25, "chars": 0.25,
    "paragraph": 110, "paragraphs": 110,
}

_BUDGET_RE = re.compile(
    r"(?:at most|no more than|not more than|under|within|up to|max(?:imum)?(?: of)?|≤|<=|limit(?:ed)? to|"
    r"in|about|around|roughly)\s*(\d[\d,]{0,5})\s*(" + "|".join(sorted(_UNIT_TOKENS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)
# No "token" here: "a 2,048-token window" and "the 8192-token prompt" describe inputs, not budgets
# (replay 2026-09-28: 16 rows mis-binned l/xl on exactly that).
_HYPHEN_BUDGET_RE = re.compile(r"\b(\d[\d,]{0,5})[- ](word|line|sentence|bullet|paragraph)s?\b", re.IGNORECASE)
_FIELD_BUDGET_RE = re.compile(r"\b(?:max_report_words|max_words|word_limit|report_word_limit|max_tokens|max_lines)\s*[:=]\s*(\d[\d,]{0,5})",
                              re.IGNORECASE)

_TINY_RE = re.compile(
    r"\b(one[- ]line|one[- ]sentence|one[- ]word|single (?:line|word|sentence|number)|yes or no|yes/no|"
    r"just the (?:number|name|answer|value|path|count)|only the (?:number|name|answer|value|path|count)|"
    r"answer with (?:a|one|the) (?:single )?(?:word|number|label|name)|reply with (?:only )?(?:a|one|the) (?:word|number|label))\b",
    re.IGNORECASE)

# Verb classes, matched in the instruction text (the caller's prompt, never the
# packed file bodies). Order matters: the first class whose verb appears earliest
# in the instruction wins, so "summarize the module, then rewrite it" reads as a
# rewrite only if "rewrite" comes first.
_VERB_CLASSES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # (class, base bin, verbs)
    ("grow", "l", ("rewrite", "re-write", "translate", "convert", "port", "reformat", "refactor", "transcribe",
                   "paraphrase", "expand", "elaborate", "reproduce", "whole file", "entire file", "complete file",
                   "full text", "verbatim copy")),
    # Nouns that name an artefact ("candidate passage", "the proposal") are NOT verbs: the
    # 2026-09-28 replay had "candidate" fire on 92 retrieval-audit prompts whose answers were xs.
    ("produce", "l", ("write a", "write the", "write an", "draft", "compose", "generate a", "generate the",
                      "produce a", "produce the", "implement", "author", "fix the", "fix this", "patch",
                      "propose a fix", "unified diff", "pull request")),
    ("report", "m", ("review", "report", "describe", "explain", "document", "compare", "outline", "analyze",
                     "analyse", "assess", "evaluate", "critique", "walk through", "how does")),
    ("reduce", "xs", ("summarize", "summarise", "summary", "tl;dr", "tldr", "list", "grep", "find", "extract",
                      "count", "classify", "label", "categorize", "categorise", "identify", "which", "name every",
                      "name the", "enumerate", "quote", "cite", "locate", "look up", "lookup", "tag", "rate", "score",
                      "answer", "what is", "is it", "does it", "yes or no")),
)

# Ledger medians by declared family (tokens_out, 2026-09-23..28 window; the
# summarization median is the 512 cap those calls ran under, so it reads as m).
FAMILY_BASE_BIN: dict[str, str] = {
    "extraction": "xs", "classification": "xs", "quote_retrieval": "xs",
    "summarization": "m", "drafting": "m", "reasoning_planning": "m", "code_review": "m",
    "long_review": "m", "document_ocr": "m", "chart_diagram": "s", "screenshot_grounded": "s",
    "utility_text": "s", "tool_execution": "s", "tool_long_output": "l",
    "code_fix": "l", "default": "s",
}

_MEDIA_BY_SUFFIX: dict[str, str] = {
    ".py": "source", ".js": "source", ".mjs": "source", ".ts": "source", ".tsx": "source", ".jsx": "source",
    ".rs": "source", ".go": "source", ".c": "source", ".h": "source", ".cpp": "source", ".hpp": "source",
    ".java": "source", ".cs": "source", ".sh": "source", ".ps1": "source", ".rb": "source", ".php": "source",
    ".sql": "source", ".toml": "config", ".yaml": "config", ".yml": "config", ".ini": "config", ".cfg": "config",
    ".env": "config", ".service": "config", ".conf": "config",
    ".md": "prose", ".rst": "prose", ".txt": "prose", ".adoc": "prose",
    ".json": "data", ".ndjson": "data", ".jsonl": "data", ".csv": "data", ".tsv": "data", ".xml": "data",
    ".log": "log", ".out": "log", ".err": "log",
    ".html": "markup", ".htm": "markup", ".svg": "markup",
    ".png": "binary", ".jpg": "binary", ".jpeg": "binary", ".gif": "binary", ".webp": "binary", ".pdf": "binary",
    ".mp4": "binary", ".wav": "binary", ".mp3": "binary", ".zip": "binary", ".gz": "binary", ".bin": "binary",
    ".safetensors": "binary", ".gguf": "binary", ".sqlite": "binary", ".db": "binary",
}


def bin_for_tokens(tokens: float) -> str:
    """The bin whose upper edge is the first at/above ``tokens`` (xl is open-ended)."""
    for name, edge in BINS:
        if tokens < edge:
            return name
    return "xl"


def _shift(bin_name: str, steps: int) -> str:
    i = BIN_ORDER.index(bin_name)
    return BIN_ORDER[max(0, min(len(BIN_ORDER) - 1, i + steps))]


def media_class(path: str) -> str:
    """Coarse media class from the suffix; 'other' when unknown, 'binary' for anything a text model cannot read."""
    lowered = path.lower().rstrip("/")
    dot = lowered.rfind(".")
    if dot < 0 or "/" in lowered[dot:]:
        return "other"
    return _MEDIA_BY_SUFFIX.get(lowered[dot:], "other")


def _instruction_text(prompt: str) -> str:
    """The caller's instruction with packed <file> blocks removed (the door packs
    files BEFORE the prompt, so the instruction is whatever follows the last block)."""
    if "</file>" in prompt:
        tail = prompt.rsplit("</file>", 1)[1]
        if tail.strip():
            return tail
    return prompt


def _explicit_budget(text: str) -> Optional[tuple[int, str]]:
    """(tokens, signal) for the smallest explicit output budget stated in the text."""
    found: list[tuple[int, str]] = []
    for m in _FIELD_BUDGET_RE.finditer(text):
        n = int(m.group(1).replace(",", ""))
        unit = "tokens" if "token" in m.group(0).lower() else ("lines" if "line" in m.group(0).lower() else "words")
        found.append((int(n * _UNIT_TOKENS[unit] * _BUDGET_HEADROOM), f"field:{m.group(0).strip()}"))
    for m in _BUDGET_RE.finditer(text):
        n = int(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        # "in 2026 words" is not a budget; neither is "within 3 lines of the top".
        if n <= 0 or n > 50_000:
            continue
        found.append((int(n * _UNIT_TOKENS[unit] * _BUDGET_HEADROOM), f"budget:{m.group(0).strip()}"))
    for m in _HYPHEN_BUDGET_RE.finditer(text):
        n = int(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        if 0 < n <= 50_000:
            found.append((int(n * _UNIT_TOKENS[unit] * _BUDGET_HEADROOM), f"budget:{m.group(0).strip()}"))
    if not found:
        return None
    found.sort(key=lambda t: t[0])
    return found[0]


def _verb_class(text: str) -> Optional[tuple[str, str, str]]:
    """(class, base_bin, verb) for the earliest-matching verb in the instruction head."""
    head = text[:1200].lower()
    best: Optional[tuple[int, str, str, str]] = None
    for cls, base, verbs in _VERB_CLASSES:
        for verb in verbs:
            pos = head.find(verb)
            if pos < 0:
                continue
            # Word-boundary check so "listen" does not read as "list" and "cite" not as "recite".
            before = head[pos - 1] if pos > 0 else " "
            after = head[pos + len(verb)] if pos + len(verb) < len(head) else " "
            if before.isalnum() or (after.isalnum() and not verb.endswith(" ")):
                continue
            if best is None or pos < best[0]:
                best = (pos, cls, base, verb)
    if best is None:
        return None
    return best[1], best[2], best[3]


def size_heuristic(prompt: str, *, system: Optional[str] = None,
                   files: Optional[list[dict]] = None,
                   payload_bytes: Optional[int] = None,
                   task_family: Optional[str] = None) -> dict:
    """Size one request. Never raises; never reads the environment or the disk.

    ``prompt`` is the caller's text (with or without packed blocks; blocks are
    stripped for the verb read), ``files`` the door's manifest
    (``[{"path","bytes"}, ...]``), ``payload_bytes`` the post-packing size the
    router decides with (defaults to the prompt's own byte length).
    """
    t0 = time.perf_counter()
    text = _instruction_text(prompt or "")
    manifest = [f for f in (files or []) if isinstance(f, dict)]
    files_bytes = sum(int(f.get("bytes") or 0) for f in manifest)
    if payload_bytes is None:
        payload_bytes = len((prompt or "").encode("utf-8")) + (files_bytes if "</file>" not in (prompt or "") else 0)
    prompt_tokens = max(1, int(payload_bytes) // 4)
    media = sorted({media_class(str(f.get("path", ""))) for f in manifest})
    signals: list[str] = []

    verb = _verb_class(text)
    budget = _explicit_budget(text)
    family_bin = FAMILY_BASE_BIN.get(task_family) if task_family else None

    if budget is not None:
        tokens, signal = budget
        bin_name = bin_for_tokens(tokens)
        confidence = 0.85
        signals.append(signal)
        expected = min(BIN_EDGE[bin_name], max(tokens, 32))
    elif _TINY_RE.search(text[:600]):
        bin_name, confidence, expected = "xs", 0.8, 64
        signals.append("tiny-answer-phrase")
    elif verb is not None:
        cls, base, word = verb
        signals.append(f"verb:{cls}:{word}")
        if cls == "grow":
            # Output tracks input: a rewrite of a 6K-token file is a 6K-token answer.
            bin_name = bin_for_tokens(prompt_tokens * 0.9)
            confidence = 0.6
        elif cls == "produce":
            bin_name = base
            if prompt_tokens > 6000:
                bin_name = _shift(bin_name, 1)
                signals.append("input>6K:+1")
            confidence = 0.5
        elif cls == "report":
            bin_name = base
            if prompt_tokens > 12000:
                bin_name = _shift(bin_name, 1)
                signals.append("input>12K:+1")
            confidence = 0.5
        else:  # reduce
            bin_name = base
            if prompt_tokens > 8000:
                bin_name = _shift(bin_name, 1)
                signals.append("input>8K:+1")
            if prompt_tokens > 24000:
                bin_name = _shift(bin_name, 1)
                signals.append("input>24K:+1")
            confidence = 0.55
        expected = BIN_EDGE[bin_name]
    elif family_bin is not None:
        bin_name, confidence, expected = family_bin, 0.45, BIN_EDGE[family_bin]
        signals.append(f"family-median:{task_family}")
    else:
        bin_name, confidence, expected = "s", 0.3, BIN_EDGE["s"]
        signals.append("default")

    if family_bin is not None and budget is None and verb is not None:
        # The declared family's ledger median is a MEASURED witness; a verb is a
        # guess. Agreement raises confidence; a gap of two bins or more hands the
        # decision to the median and says so (replay 2026-09-28: every verb-vs-
        # median gap>=2 row was the median's, 92/92).
        gap = abs(BIN_ORDER.index(family_bin) - BIN_ORDER.index(bin_name))
        if gap >= 2:
            signals.append(f"family-median:{task_family}:{family_bin}:overrides-verb:{bin_name}")
            bin_name, expected, confidence = family_bin, BIN_EDGE[family_bin], 0.5
        else:
            confidence = min(0.95, confidence + 0.15) if gap == 0 else confidence
            signals.append(f"family-median:{task_family}:{family_bin}:{'agree' if gap == 0 else 'gap1'}")

    if "binary" in media:
        signals.append("binary-input")

    proposed_family = task_family
    if task_family == "tool_execution" and bin_name in LONG_BINS:
        # The one refinement the sizer makes to a declared family: long tool
        # output belongs on the decode seat (tool-long), not the prefill seat.
        proposed_family = "tool_long_output"
        signals.append("tool_execution->tool_long_output")

    return {
        "output_class": bin_name,
        "expected_output_tokens": int(expected),
        "task_family": proposed_family,
        "declared_family": task_family,
        "confidence": round(float(confidence), 2),
        "source": "heuristic",
        "signals": signals,
        "prompt_tokens": prompt_tokens,
        "files": len(manifest),
        "files_bytes": files_bytes,
        "media": media,
        "ms": round((time.perf_counter() - t0) * 1000, 3),
    }


__all__ = ["BINS", "BIN_ORDER", "BIN_EDGE", "LONG_BINS", "FAMILY_BASE_BIN", "bin_for_tokens",
           "media_class", "size_heuristic"]
