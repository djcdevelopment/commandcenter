"""Itemized delivery (ADR-0058, lap 20): code lists the items of a kind, two readers answer one item a call, a third
settles the disagreements, code settles each row by agreement and writes the report and every count. Pure: no model call,
no service. Source text comes from `git show <commit>:<path>`, never the working tree. Kinds are entries of KINDS: env_reads, param_defaults.
Moved from delivery-plan/evidence/itemized (make_items.env_reads, make_env_corpus naming, run_itemized prompt and schema,
score_envmap normaliser) with the same behaviour; the prompt's kind label is the kind name (the script printed the task name).
Changed since (2026-10-04, measured): writes to os.environ are not items; `same` reads r"x", "x" and x as one default; the
`default` ask names the `or <value>` idiom, so the prompt no longer equals run_itemized's byte for byte. A dispute is settled by
the 27B as judge with thinking on (lab-rnd research/settle_probe.py PROMPT + TAIL_ON); its own default decides, not its verdict.
"""
from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import tokenize
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable

from . import contract

__all__ = ["ItemsError", "MAX_ITEMS", "JUDGE_TOKENS", "enumerate_items", "prompt", "schema", "parse", "same", "needs_judge",
           "judge_prompt", "parse_judgment", "settle", "assemble"]


class ItemsError(ValueError):
    pass


MAX_ITEMS = contract.MAX_SECTIONS * contract.MAX_PARAGRAPHS  # one delivered paragraph per item
CAP = 300  # shown source lines per item
JUDGE_TOKENS = 8000  # one of 57 measured thinking calls ran out at 4,000; the median answer used 511

@dataclass(frozen=True)
class Kind:
    """One item kind: what code lists, what a reader is asked, what is compared, and the words of every prompt and row."""
    name: str
    enumerate: Callable  # (text, path) -> [item dict without `id`]
    fields: tuple  # the fields asked of a reader: {"name", "type", "ask"}
    compared: tuple  # the fields two readings must agree on
    desc: str
    question: str
    noun: str  # the summary's count sentence: "Delivery of n <noun> in k files."
    unit: str  # one item, as the summary and judge prompts call it
    what: str  # a judge prompt: "the default of <what> in source code"
    judge_ask: str  # a judge prompt rule 2: `"default" is <judge_ask>`
    subject: Callable  # item -> how a row names it
    cannot_see: str
    none: frozenset = frozenset()  # answers that mean "no value" and are one value
    or_note: str = ""  # appended to an item's block when item["or"]
    judge_notes: dict = field(default_factory=dict)  # item["judge"] mark -> note in the agreed-row judge prompt
    norm: Callable | None = None  # answer -> comparison key; None: _norm (env_reads)


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
_JPROMPT = """Two readers were asked for the default of {what} in source code. They disagree. Decide from the code.

{block}
Reading A says the default is: {a}
Reading B says the default is: {b}

Rules:
1. Use only the lines shown. The {under} under study is the one named in the item header, on the item's own lines.
2. "default" is {ask}.
3. "verdict" is "A" if reading A is what the code shows, "B" if reading B is, "neither" if neither is.
4. "lines" are 1 to 4 line numbers (the numbers before the | sign) that your answer rests on.
Answer with one JSON object on the last line: {{"default": "...", "verdict": "A" | "B" | "neither", "lines": [n, ...]}}
"""
_JPROMPT_AGREED = """Two readers were asked for the default of {what} in source code. Code marks this {under} as unusual. Work out the default from the code yourself first; then compare it with the readers' answer, given after the rules.

{block}{note}
Rules:
1. Use only the lines shown. The {under} under study is the one named in the item header, on the item's own lines.
2. Find "default" in the code yourself: it is {ask}.
3. "verdict" is "A" if the readers' answer is what the code shows, "neither" if it is not.
4. "lines" are 1 to 4 line numbers (the numbers before the | sign) that your answer rests on.

Both readers say the default is: {a}. Check it against the code.
Answer with one JSON object on the last line: {{"default": "...", "verdict": "A" | "neither", "lines": [n, ...]}}
"""


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
        return isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and ast.unparse(n.value) == "os.environ"  # not a write

    consts = {(x.targets[0] if isinstance(x, ast.Assign) else x.target).id: x.value.value for x in tree.body
              if (isinstance(x, ast.Assign) and isinstance(x.targets[0], ast.Name) or isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name))
              and isinstance(x.value, ast.Constant) and isinstance(x.value.value, str)}
    def loop_names(n, var):
        """The string constants a loop variable ranges over, when its `for` body or comprehension iterates a literal tuple or list
        and nothing else there rebinds it; a function or class boundary ends the search."""
        prev, cur = n, parents.get(n)
        while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            gens = [(cur.target, cur.iter)] if isinstance(cur, ast.For) and prev in cur.body else \
                   [(g.target, g.iter) for g in cur.generators] if isinstance(cur, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)) else \
                   [(cur.target, cur.iter)] if isinstance(cur, ast.comprehension) and prev is not cur.iter else []
            for tg, it in gens:
                if isinstance(tg, ast.Name) and tg.id == var:
                    if (isinstance(it, (ast.Tuple, ast.List)) and it.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in it.elts)
                            and not any(isinstance(x, ast.Name) and x.id == var and isinstance(x.ctx, ast.Store) and x is not tg for x in ast.walk(cur))):
                        return [e.value for e in it.elts]
                    return None
            prev, cur = cur, parents.get(cur)
        return None

    def tests_unset(t, name):
        return any((isinstance(x, ast.UnaryOp) and isinstance(x.op, ast.Not) and isinstance(x.operand, ast.Name) and x.operand.id == name)
                   or (isinstance(x, ast.Compare) and isinstance(x.left, ast.Name) and x.left.id == name and len(x.ops) == 1
                       and isinstance(x.ops[0], ast.Is) and isinstance(x.comparators[0], ast.Constant) and x.comparators[0].value is None)
                   for x in ast.walk(t))

    def later_fallback(st):
        tg = st.targets[0] if isinstance(st, ast.Assign) and len(st.targets) == 1 else st.target if isinstance(st, ast.AnnAssign) else None
        if not isinstance(tg, ast.Name):
            return False
        for field in ("body", "orelse", "finalbody"):
            sibs = getattr(parents.get(st), field, None)
            if isinstance(sibs, list) and st in sibs:
                i = sibs.index(st)
                return any(isinstance(x, ast.If) and tests_unset(x.test, tg.id)
                           and any(isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == tg.id for t in a.targets)
                                   for b in x.body for a in ast.walk(b)) for x in sibs[i + 1:i + 4])
        return False

    reads = []
    for n in ast.walk(tree):
        if not is_env(n):
            continue
        arg = n.args[0] if isinstance(n, ast.Call) and n.args else (n.slice if isinstance(n, ast.Subscript) else None)
        computed, names = False, None
        if isinstance(arg, ast.Constant):
            nm = arg.value
        elif isinstance(arg, ast.Name) and arg.id in consts:
            nm = consts[arg.id]
        else:
            names = loop_names(n, arg.id) if isinstance(arg, ast.Name) else None
            cs = [c.value for c in ast.walk(arg) if isinstance(c, ast.Constant) and isinstance(c.value, str)] if arg is not None else []
            nm = names[0] if names else cs[-1] if cs else ast.unparse(arg)
            computed = not cs and not names
        p = parents.get(n)
        has_or = (isinstance(n, ast.Call) and len(n.args) == 1 and not n.keywords and isinstance(p, ast.BoolOp)
                  and isinstance(p.op, ast.Or) and any(v is n for v in p.values[:-1]))  # any operand but the last
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
        dflt = n.args[1] if isinstance(n, ast.Call) and len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "default"), None) if isinstance(n, ast.Call) else None
        dflt = next(p.values[i + 1] for i, v in enumerate(p.values) if v is n) if has_or else dflt  # the `or` value is the default
        cond = dflt is not None and any(isinstance(x, (ast.IfExp, ast.BoolOp)) or is_env(x) for x in ast.walk(dflt))  # a condition or a read with its own default
        judge = ("computed" if arg is not None and not names and not isinstance(arg, ast.Constant) and not (isinstance(arg, ast.Name) and arg.id in consts)
                 else "conditional_default" if cond else "later_fallback" if later_fallback(st) else None)
        for nm in names or [nm]:
            reads.append({"name": nm, "path": path, "start": s, "end": e, "show": show, "line": n.lineno, "computed": computed, "or": has_or,
                          "judge": judge, "_l": n.lineno, "_c": n.col_offset})
    reads.sort(key=lambda r: (r["_l"], r["_c"]))
    cnt: dict = {}
    for r in reads:
        cnt[r["name"]] = cnt.get(r["name"], 0) + 1
    for r in reads:
        if cnt[r["name"]] > 1:
            r["name"] = f'{r["name"]}@{r["_l"]}'
        del r["_l"], r["_c"]
    return reads


def _env_items(text: str, path: str) -> list:
    return [{"name": f"{r['name'].split('@')[0]} @ {path}:{r['start']}", "path": path, "start": r["start"], "end": r["end"], "show": r["show"],
             "var": r["name"].split("@")[0], "line": r["line"], **({"computed": True} if r["computed"] else {}),
             **({"or": True} if r["or"] else {}), **({"judge": r["judge"]} if r["judge"] else {})} for r in _env_reads(text, path)]


def _signature_end(lines: list, fn) -> int:
    """The line of the colon that closes the signature of `fn` (the first `:` outside brackets after its `def`)."""
    depth = 0
    for t in tokenize.generate_tokens(_line_feed(lines, fn.lineno - 1)):
        if t.type == tokenize.OP:
            if t.string in "([{":
                depth += 1
            elif t.string in ")]}":
                depth -= 1
            elif t.string == ":" and depth == 0:
                return fn.lineno + t.start[0] - 1
    raise ItemsError(f"line {fn.lineno}: the signature of {fn.name} has no closing colon")


def _line_feed(lines: list, start: int):
    it = iter(lines[start:])
    return lambda: next(it, "")


def _param_defaults(text: str, path: str) -> list:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    out: list = []

    def visit(node, scope):
        for c in ast.iter_child_nodes(node):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = ".".join([*scope, c.name])
                a = c.args
                pos = [*a.posonlyargs, *a.args]
                pairs = [*zip(pos[len(pos) - len(a.defaults):], a.defaults), *((p, d) for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None)]
                if pairs:
                    s, e = c.lineno, _signature_end(lines, c)
                    show = [[s, min(e + 3, c.end_lineno)]]
                    for p, d in pairs:
                        simple = isinstance(d, (ast.Constant, ast.Name)) or (isinstance(d, ast.UnaryOp) and isinstance(d.op, ast.USub)
                                                                          and isinstance(d.operand, ast.Constant) and isinstance(d.operand.value, (int, float, complex)))
                        num = d.operand if isinstance(d, ast.UnaryOp) else d
                        literal = (isinstance(num, ast.Constant) and type(num.value) in (int, float, complex)
                                   and _expr_key(ast.get_source_segment(text, num)) != _expr_key(repr(num.value)))  # 0o644, 1e-3, 200_000
                        judge = ("expression_default" if not simple else "multiline" if d.end_lineno > d.lineno
                                 else "literal_form" if literal else None)
                        out.append(((p.lineno, p.col_offset), {"name": f"{qual}({p.arg}) @ {path}:{p.lineno}", "path": path, "start": s, "end": e,
                                    "show": show, "line": d.lineno, "func": qual, "param": p.arg, **({"judge": judge} if judge else {})}))
                visit(c, [*scope, c.name])
            elif isinstance(c, ast.ClassDef):
                visit(c, [*scope, c.name])
            else:
                visit(c, scope)
    visit(tree, [])
    return [d for _, d in sorted(out, key=lambda x: x[0])]


_ENV_FIELDS = (
    {"name": "variable", "type": "string", "ask": "the environment variable name"},
    {"name": "default", "type": "string", "ask": "the value used when the variable is unset, exactly as written in the code: the second argument of the get "
     "or getenv call; if there is none and the call is directly followed by `or <value>`, that value; 'none' if neither"},
    {"name": "controls", "type": "string", "ask": "one line on what it controls"})
_PARAM_AS_WRITTEN = ("the text after the parameter's `=` in the signature, character for character as written: quotes, prefixes, "
                     "names and calls as they stand, not its value; a default over several lines in full")
_PARAM_FIELDS = (
    {"name": "parameter", "type": "string", "ask": "the parameter's name"},
    {"name": "default", "type": "string", "ask": _PARAM_AS_WRITTEN})

_LAYOUT = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)


def _expr_key(s) -> tuple:
    """param_defaults: a default compared as Python tokens. Layout, comments, enclosing backticks and a string's quote style or
    prefix do not count (r"x", 'x' and "x" are one default); all else does: case, the name x and the string 'x', '' and None,
    0 and False, () and [], 0o644 and 420. An answer that does not tokenize is compared as its stripped text."""
    t = str(s).strip()
    t = t[1:-1].strip() if len(t) > 1 and t[0] == t[-1] == "`" else t
    try:
        toks = [x for x in tokenize.generate_tokens(io.StringIO(t).readline) if x.type not in _LAYOUT]
    except tokenize.TokenError:
        return ("text", t)
    key = []
    for x in toks:
        try:
            key.append(repr(ast.literal_eval(x.string)) if x.type == tokenize.STRING else x.string)
        except (ValueError, SyntaxError):
            key.append(x.string)
    return tuple(key)

KINDS = {k.name: k for k in (
    Kind("env_reads", _env_items, _ENV_FIELDS, ("default",),
         "one environment variable read (os.environ.get, os.environ[...] or os.getenv)",
         "Name the environment variable, its default if any, and in one line what it controls.",
         "environment-variable reads", "read", "one environment-variable read", _ENV_FIELDS[1]["ask"],
         lambda it: f"`{it['var']}`{' (a name computed at run time)' if it.get('computed') else ''}",
         "Reads made through a helper function, os.environ.setdefault, os.environ.pop, a membership test or a copy of the whole "
         "environment (`dict(os.environ)`, `os.environ.copy()`) are not listed.",
         frozenset({"none", "null", "nodefault", "", "n/a"}),
         "Note: this read has no second argument and is directly followed by `or <value>` in the code (an `or` may also come "
         "before it); the value used when the variable is unset is the <value> after the read, exactly as written.",
         {"computed": "Note: the name of the variable read here is not a string constant. A default written on the parameter or variable that holds the name is not the default of the variable read.",
          "conditional_default": "Note: the value used when this variable is unset is an expression that holds a condition (if/else, and/or) or another environment read; the answer states that whole expression as written, not one branch of it.",
          "later_fallback": "Note: the result of this read is assigned to a name that the next statements test and may reassign; a value assigned there is not the default of this read, which is what the read itself gives (its second argument, or the value directly after `or`), or none."}),
    Kind("param_defaults", _param_defaults, _PARAM_FIELDS, ("default",),
         "one parameter with a default in a function signature",
         "The item is the parameter `{param}` of the function `{func}`, whose signature starts on line {start}. Name that "
         "parameter and give its default exactly as written.",
         "parameter defaults", "parameter", "the parameter `{param}` of the function `{func}`", _PARAM_AS_WRITTEN,
         lambda it: f"`{it['func']}({it['param']})`",
         "Parameters without a default, lambda parameters, and defaults assigned inside a function body are not listed.",
         frozenset(), "",
         {"expression_default": "Note: the default of this parameter is an expression (a call, an attribute, a condition, a collection or an operation), not a constant or a name; the answer states the whole expression as written, across its lines, not its value and not one part of it.",
          "multiline": "Note: the default of this parameter runs over more than one line; the answer states all of it as written.",
          "literal_form": "Note: the default of this parameter is a number written in another form than its plain decimal value (an octal, hex or binary literal, an exponent or underscores); the answer states it as written, not its value."},
         _expr_key),
)}
assert tuple(KINDS) == contract.ITEM_KINDS, "items.py kinds differ from contract.ITEM_KINDS"


def _kind(kind: str) -> Kind:
    if kind not in KINDS:
        raise ItemsError(f"unknown item kind {kind!r}; one of {tuple(KINDS)}")
    return KINDS[kind]


def enumerate_items(kind: str, repo: str, commit: str, paths: list) -> list:
    """Every item of the kind in the files at the commit, ordered by path as given, then line, then column."""
    k = _kind(kind)
    out: list = []
    for i, p in enumerate(paths):
        if not isinstance(p, str) or not p.endswith(".py"):
            raise ItemsError(f"{p!r}: not a .py file; the {kind} enumerator reads Python only")
        if p in paths[:i]:
            raise ItemsError(f"{p}: declared twice; every item would be read and delivered twice")
        text = _src(repo, commit, p)
        try:
            found = k.enumerate(text, p)
        except SyntaxError as e:
            raise ItemsError(f"{p}: does not parse at {commit[:12]}: {e.msg} (line {e.lineno})") from None
        for it in found:
            out.append({"id": f"i{len(out) + 1:04d}", **it})
    if not out:
        raise ItemsError(f"no {kind} item in {len(paths)} file(s) at {commit[:12]}")
    if len(out) > MAX_ITEMS:
        raise ItemsError(f"{len(out)} {kind} items exceed MAX_ITEMS {MAX_ITEMS}; declare fewer files")
    return out


def _block(k: Kind, item: dict, repo: str, commit: str) -> str:
    lines = _src(repo, commit, item["path"]).splitlines()
    nums = [n for s, e in (item.get("show") or [[item["start"], item["end"]]]) for n in range(max(s, 1), min(e, len(lines)) + 1)]
    cut = ""
    if len(nums) > CAP:
        cut = f" (shown text cut: first {CAP} of {len(nums)} lines)"
        nums = nums[:CAP]
    shown = "\n".join(f"{n}| {lines[n - 1]}" for n in nums)
    head = f"Item under study: {item['name']} ({item['path']}, lines {item['start']}-{item['end']})"
    return f"{head}\nSource: {item['path']}{cut}\n{shown}\n" + (k.or_note + "\n" if item.get("or") and k.or_note else "")


def prompt(kind: str, item: dict, repo: str, commit: str) -> str:
    k = _kind(kind)
    fields = "\n".join(f'   - {f["name"]} (string): {f["ask"]}' for f in k.fields)
    return _FPROMPT.format(question=k.question.format(**item), kind=kind, desc=k.desc, fields=fields, items=_block(k, item, repo, commit))


def judge_prompt(kind: str, item: dict, repo: str, commit: str, readings: list) -> str:
    k = _kind(kind)
    a, b = ("no answer" if r is None else r["fields"]["default"] for r in readings)
    if readings[0] is not None and readings[1] is not None and same(kind, readings[0], readings[1]):
        note = k.judge_notes.get(item.get("judge"), "")
        return _JPROMPT_AGREED.format(block=_block(k, item, repo, commit), note=f"{note}\n" if note else "", a=a, ask=k.judge_ask, what=k.what.format(**item), under=k.unit)
    return _JPROMPT.format(block=_block(k, item, repo, commit), a=a, b=b, ask=k.judge_ask, what=k.what.format(**item), under=k.unit)


def schema(kind: str) -> dict:
    props = {f["name"]: {"type": "string"} for f in _kind(kind).fields}
    props["lines"] = {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 6}
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def parse(kind: str, text: str) -> dict:
    names = [f["name"] for f in _kind(kind).fields]
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


_LITERAL = re.compile(r"(?<!\w)(?:[rR][bBfF]?|[bBfF][rR]?|[uU])(?=['\"])")  # string prefix before a quote: r"C:\x" is C:\x


def _norm(s) -> str:
    return re.sub(r"[\s'\"`]+", "", _LITERAL.sub("", str(s)).replace("\\\\", "\\").lower())


def same(kind: str, a: dict, b: dict) -> bool:
    kd = _kind(kind)
    norm = kd.norm or _norm
    for k in kd.compared:
        x, y = norm(a["fields"][k]), norm(b["fields"][k])
        if not (x == y or (x in kd.none and y in kd.none)):
            return False
    return True


def needs_judge(kind: str, readings: list, item: dict | None = None) -> bool:
    return bool(item and item.get("judge")) or not (readings[0] is not None and readings[1] is not None and same(kind, readings[0], readings[1]))


def parse_judgment(kind: str, text: str) -> dict:
    """The last JSON object in the text (a thinking answer may carry prose before it); keys beyond the three are ignored."""
    _kind(kind)
    if not isinstance(text, str):
        raise ItemsError(f"judgment is {type(text).__name__}, not text")
    dec, d = json.JSONDecoder(), None
    for m in reversed([m.start() for m in re.finditer(r"\{", text)]):
        try:
            d = dec.raw_decode(text, m)[0]
        except ValueError:
            continue
        if isinstance(d, dict):
            break
        d = None
    if d is None:
        raise ItemsError("judgment: no JSON object in the answer")
    if d.get("verdict") not in ("A", "B", "neither"):
        raise ItemsError(f"judgment: verdict must be A, B or neither, got {json.dumps(d.get('verdict'))[:40]}")
    if not isinstance(d.get("default"), str):
        raise ItemsError("judgment: default missing or not a string")
    ls = d.get("lines")
    if not (isinstance(ls, list) and 1 <= len(ls) <= 4 and all(isinstance(n, int) and not isinstance(n, bool) for n in ls)):
        raise ItemsError(f"judgment: lines must be 1 to 4 integers, got {json.dumps(ls)[:80]}")
    return {"verdict": d["verdict"], "default": d["default"], "lines": ls}


def settle(kind: str, readings: list, judgment: dict | None = None, item: dict | None = None) -> dict:
    r, compared = readings, _kind(kind).compared
    if r[0] is not None and r[1] is not None and same(kind, r[0], r[1]):
        if item and item.get("judge"):
            if judgment is not None and same(kind, r[0], {"fields": {k: judgment[k] for k in compared}}):
                return {"state": "agreed", "fields": r[0]["fields"], "by": [0, 1, "judge"]}
            return {"state": "unverified", "fields": None, "by": []}
        return {"state": "agreed", "fields": r[0]["fields"], "by": [0, 1]}
    if judgment is not None:
        own = {"fields": {k: judgment[k] for k in compared}}
        for i in (0, 1):
            if r[i] is not None and same(kind, r[i], own):
                return {"state": "settled", "fields": r[i]["fields"], "by": [i, "judge"]}
    return {"state": "unverified", "fields": None, "by": []}


def _clip(v, n: int) -> str:
    s = " ".join(str(v).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def assemble(kind: str, items: list, rows: list, readers: list) -> tuple:
    """-> (delivery-output.v1 document for quote_mode line_reference, report)."""
    k = _kind(kind)
    if len(items) != len(rows):
        raise ItemsError(f"{len(items)} items but {len(rows)} rows")
    if not 0 < len(items) <= MAX_ITEMS:
        raise ItemsError(f"{len(items)} items outside 1..{MAX_ITEMS}")
    if any("line" not in it for it in items):
        raise ItemsError("an item has no `line` (listed before items gained it); list the items again in a new work")
    paras, count, failures, jfail, marked = [], {"agreed": 0, "settled": 0, "unverified": 0}, 0, 0, 0
    for it, row in zip(items, rows):
        count[row["state"]] += 1
        readings, judgment = row["readings"], row.get("judgment")
        failures += sum(1 for x in readings if x is None)
        by_mark = bool(it.get("judge")) and not needs_judge(kind, readings) and row["by"] != [0, 1]  # a row settled without the item was not judged
        jfail += (needs_judge(kind, readings) or by_mark) and judgment is None
        marked += by_mark
        head = f"{k.subject(it)} at {it['path']}:"
        if row["state"] == "unverified":
            seen = " | ".join("no answer" if x is None else _clip(x["fields"]["default"], 200) or "(empty)" for x in readings)
            judge = "no answer" if judgment is None else _clip(judgment["default"], 200) or "(empty)"
            text = f"{head} NOT VERIFIED. Readings of the default: {seen}; judge: {judge}."
        else:
            f = row["fields"]
            text = f"{head} default {_clip(f['default'], 300) or '(empty)'}."
        paras.append({"text": text, "quotes": [f"{it['path']}:{it['line']}"]})
    n = len(items)
    files = len({it["path"] for it in items})
    summary = (f"Delivery of {n} {k.noun} in {files} files. {count['agreed']} were agreed by the first two readers, "
               f"{count['settled']} were settled by a judge whose own default matches one reading, and {count['unverified']} are marked "
               f"NOT VERIFIED because no two agree or, for a marked {k.unit}, the judge's default differs from the readers' or the judge gave none. "
               f"{marked} rows were sent to the judge because of a mark on the {k.unit}, although the readers agreed. {failures} reader calls failed and {jfail} judge calls gave no answer. Every row "
               f"cites the line of its {k.unit}. {k.cannot_see}")
    secs = [{"heading": f"Items {i + 1} to {min(i + contract.MAX_PARAGRAPHS, n)}", "paragraphs": paras[i:i + contract.MAX_PARAGRAPHS]}
            for i in range(0, n, contract.MAX_PARAGRAPHS)]
    report = {"kind": kind, "items": n, **count, "reader_failures": failures, "judge_failures": jfail, "judged_by_mark": marked, "files": files}
    return {"summary": summary, "sections": secs}, report
