"""Itemized delivery (ADR-0058, lap 20): code lists the items of a kind, two readers answer one item a call, a third
settles the disagreements, code settles each row by agreement and writes the report and every count. Pure: no model call,
no service. Source text comes from `git show <commit>:<path>`, never the working tree. One registered kind: env_reads.
Moved from delivery-plan/evidence/itemized (make_items.env_reads, make_env_corpus naming, run_itemized prompt and schema,
score_envmap normaliser) with the same behaviour; the prompt's kind label is the kind name (the script printed the task name).
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from functools import lru_cache

from . import contract

__all__ = ["ItemsError", "MAX_ITEMS", "enumerate_items", "prompt", "schema", "parse", "same", "settle", "needs_third", "assemble"]


class ItemsError(ValueError):
    pass


MAX_ITEMS = contract.MAX_SECTIONS * contract.MAX_PARAGRAPHS  # one delivered paragraph per item
CAP = 300  # shown source lines per item

_FIELDS = {"env_reads": [
    {"name": "variable", "type": "string", "ask": "the environment variable name"},
    {"name": "default", "type": "string", "ask": "its default exactly as written, or 'none' if this read has no default"},
    {"name": "controls", "type": "string", "ask": "one line on what it controls"}]}
_DESC = {"env_reads": "one environment variable read (os.environ.get, os.environ[...] or os.getenv)"}
_QUESTION = {"env_reads": "Name the environment variable, its default if any, and in one line what it controls."}
_NOUN = {"env_reads": "environment-variable reads"}
_COMPARED = {"env_reads": ("default",)}
_NONE = {"none", "null", "nodefault", "", "n/a"}
_FPROMPT = """{question}

Kind of item: {kind} ({desc}).

{items}
Rules:
1. Use only the lines shown. Do not guess beyond them.
2. Fill each field for the item named in its header:
{fields}
3. Where the lines show none, write 'none' (or an empty list).
4. "lines" are 1 to 6 line numbers (the numbers before the | sign) that your answer rests on.
"""


def _kind(kind: str) -> str:
    if kind not in _FIELDS:
        raise ItemsError(f"unknown item kind {kind!r}; one of {tuple(_FIELDS)}")
    return kind


assert tuple(_FIELDS) == contract.ITEM_KINDS, "items.py kinds differ from contract.ITEM_KINDS"


@lru_cache(maxsize=256)
def _src(repo: str, commit: str, path: str) -> str:
    r = subprocess.run(["git", "-C", repo, "show", f"{commit}:{path}"], capture_output=True)
    if r.returncode:
        raise ItemsError(f"{path}: not readable at {commit[:12]} in {repo}: {r.stderr.decode(errors='replace').strip()[:160]}")
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ItemsError(f"{path}: not UTF-8 at {commit[:12]}: {e.reason} at byte {e.start}") from None


def _env_reads(text: str, path: str) -> list:
    tree = ast.parse(text)
    n_lines = len(text.splitlines())
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def is_env(n):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "get" and ast.unparse(f.value) == "os.environ":
                return True
            if ast.unparse(f) == "os.getenv":
                return True
        return isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ"

    consts = {x.targets[0].id: x.value.value for x in tree.body if isinstance(x, ast.Assign) and isinstance(x.targets[0], ast.Name)
              and isinstance(x.value, ast.Constant) and isinstance(x.value.value, str)}
    reads = []
    for n in ast.walk(tree):
        if not is_env(n):
            continue
        arg = n.args[0] if isinstance(n, ast.Call) and n.args else (n.slice if isinstance(n, ast.Subscript) else None)
        if isinstance(arg, ast.Constant):
            nm = arg.value
        elif isinstance(arg, ast.Name) and arg.id in consts:
            nm = consts[arg.id]
        else:
            cs = [c.value for c in ast.walk(arg) if isinstance(c, ast.Constant) and isinstance(c.value, str)] if arg is not None else []
            nm = cs[-1] if cs else ast.unparse(arg)
        st = n
        while not isinstance(st, ast.stmt):
            st = parents[st]
        s, e = (n.lineno,) * 2 if isinstance(st, (ast.If, ast.For, ast.While, ast.With, ast.Try)) else (st.lineno, st.end_lineno)
        fn = n
        while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = parents[fn]
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.end_lineno - fn.lineno + 1 <= 31:
            show = [[fn.lineno, fn.end_lineno]]
        else:
            show = [[max(1, n.lineno - 15), min(n_lines, n.lineno + 15)]]
        reads.append({"name": nm, "path": path, "start": s, "end": e, "show": show, "_l": n.lineno, "_c": n.col_offset})
    reads.sort(key=lambda r: (r["_l"], r["_c"]))
    cnt: dict = {}
    for r in reads:
        cnt[r["name"]] = cnt.get(r["name"], 0) + 1
    for r in reads:
        if cnt[r["name"]] > 1:
            r["name"] = f'{r["name"]}@{r["_l"]}'
        del r["_l"], r["_c"]
    return reads


def enumerate_items(kind: str, repo: str, commit: str, paths: list) -> list:
    """Every item of the kind in the files at the commit, ordered by path as given, then line, then column."""
    _kind(kind)
    out: list = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p.endswith(".py"):
            raise ItemsError(f"{p!r}: not a .py file; the {kind} enumerator reads Python only")
        if p in paths[:i]:
            raise ItemsError(f"{p}: declared twice; every item would be read and delivered twice")
        text = _src(repo, commit, p)
        try:
            reads = _env_reads(text, p)
        except SyntaxError as e:
            raise ItemsError(f"{p}: does not parse at {commit[:12]}: {e.msg} (line {e.lineno})") from None
        for r in reads:
            var = r["name"].split("@")[0]
            out.append({"id": f"i{len(out) + 1:04d}", "name": f"{var} @ {p}:{r['start']}", "path": p, "start": r["start"],
                        "end": r["end"], "show": r["show"], "var": var})
    if not out:
        raise ItemsError(f"no {kind} item in {len(paths)} file(s) at {commit[:12]}")
    if len(out) > MAX_ITEMS:
        raise ItemsError(f"{len(out)} {kind} items exceed MAX_ITEMS {MAX_ITEMS}; declare fewer files")
    return out


def prompt(kind: str, item: dict, repo: str, commit: str) -> str:
    _kind(kind)
    lines = _src(repo, commit, item["path"]).splitlines()
    nums = [n for s, e in (item.get("show") or [[item["start"], item["end"]]]) for n in range(max(s, 1), min(e, len(lines)) + 1)]
    cut = ""
    if len(nums) > CAP:
        cut = f" (shown text cut: first {CAP} of {len(nums)} lines)"
        nums = nums[:CAP]
    shown = "\n".join(f"{n}| {lines[n - 1]}" for n in nums)
    head = f"Item under study: {item['name']} ({item['path']}, lines {item['start']}-{item['end']})"
    fields = "\n".join(f'   - {f["name"]} (string): {f["ask"]}' for f in _FIELDS[kind])
    return _FPROMPT.format(question=_QUESTION[kind], kind=kind, desc=_DESC[kind], fields=fields,
                           items=f"{head}\nSource: {item['path']}{cut}\n{shown}\n")


def schema(kind: str) -> dict:
    props = {f["name"]: {"type": "string"} for f in _FIELDS[_kind(kind)]}
    props["lines"] = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 6}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def parse(kind: str, text: str) -> dict:
    names = [f["name"] for f in _FIELDS[_kind(kind)]]
    try:
        d = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ItemsError(f"answer is not JSON: {e}") from None
    if not isinstance(d, dict):
        raise ItemsError(f"answer is {type(d).__name__}, not an object")
    for k in names:
        if not isinstance(d.get(k), str):
            raise ItemsError(f"field {k} missing or not a string")
    if set(d) - {*names, "lines"}:
        raise ItemsError(f"unexpected field(s) {sorted(set(d) - {*names, 'lines'})}")
    ls = d.get("lines")
    if not (isinstance(ls, list) and 1 <= len(ls) <= 6 and all(isinstance(n, int) and not isinstance(n, bool) for n in ls)):
        raise ItemsError(f"lines must be 1 to 6 integers, got {json.dumps(ls)[:80]}")
    return {"fields": {k: d[k] for k in names}, "lines": ls}


def _norm(s) -> str:
    return re.sub(r"[\s'\"`]+", "", str(s).lower())


def same(kind: str, a: dict, b: dict) -> bool:
    for k in _COMPARED[_kind(kind)]:
        x, y = _norm(a["fields"][k]), _norm(b["fields"][k])
        if not (x == y or (x in _NONE and y in _NONE)):
            return False
    return True


def needs_third(kind: str, readings: list) -> bool:
    return not (readings[0] is not None and readings[1] is not None and same(kind, readings[0], readings[1]))


def settle(kind: str, readings: list) -> dict:
    r = list(readings) + [None] * (3 - len(readings))
    if r[0] is not None and r[1] is not None and same(kind, r[0], r[1]):
        return {"state": "agreed", "fields": r[0]["fields"], "by": [0, 1]}
    if r[2] is not None:
        for i in (0, 1):
            if r[i] is not None and same(kind, r[i], r[2]):
                return {"state": "settled", "fields": r[i]["fields"], "by": [i, 2]}
    return {"state": "unverified", "fields": None, "by": []}


def _clip(v, n: int) -> str:
    s = " ".join(str(v).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def assemble(kind: str, items: list, rows: list, readers: list) -> tuple:
    """-> (delivery-output.v1 document for quote_mode line_reference, report)."""
    _kind(kind)
    if len(items) != len(rows):
        raise ItemsError(f"{len(items)} items but {len(rows)} rows")
    if not 0 < len(items) <= MAX_ITEMS:
        raise ItemsError(f"{len(items)} items outside 1..{MAX_ITEMS}")
    paras, count, failures = [], {"agreed": 0, "settled": 0, "unverified": 0}, 0
    for it, row in zip(items, rows):
        count[row["state"]] += 1
        readings = row.get("readings") or []
        failures += sum(1 for x in readings if x is None)
        head = f"`{it['var']}` at {it['path']}:"
        if row["state"] == "unverified":
            seen = " | ".join("no answer" if x is None else _clip(x["fields"]["default"], 200) or "(empty)" for x in readings)
            text = f"{head} NOT VERIFIED. Readings of the default: {seen}."
        else:
            f = row["fields"]
            text = f"{head} default {_clip(f['default'], 300) or '(empty)'}." + (f" {_clip(f['controls'], 600)}" if f["controls"].strip() else "")
        paras.append({"text": text, "quotes": [f"{it['path']}:{it['start']}"]})
    n = len(items)
    files = len({it["path"] for it in items})
    summary = (f"Delivery of {n} {_NOUN[kind]} in {files} files. {count['agreed']} were agreed by the first two readers, "
               f"{count['settled']} were settled by a third reading, and {count['unverified']} are marked NOT VERIFIED because no two "
               f"readings agree. {failures} reader calls failed. Every row cites the line of its read.")
    secs = [{"heading": f"Items {i + 1} to {min(i + contract.MAX_PARAGRAPHS, n)}", "paragraphs": paras[i:i + contract.MAX_PARAGRAPHS]}
            for i in range(0, n, contract.MAX_PARAGRAPHS)]
    report = {"kind": kind, "items": n, **count, "reader_failures": failures, "files": files}
    return {"summary": summary, "sections": secs}, report
