#!/usr/bin/env python3
"""Grade a delivery against brief.v2 form limits: word count, verbatim quotes resolvable in the sources, no line numbers.

usage: grade_delivery.py --report FILE --source NAME=PATH [--source NAME=PATH] [--min 300 --max 400]
Prints a JSON verdict. Substance is graded by a human or frontier reader; this script measures form only.
"""
import argparse, json, re

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True); ap.add_argument("--source", action="append", default=[])
    ap.add_argument("--min", type=int, default=300); ap.add_argument("--max", type=int, default=400)
    a = ap.parse_args()
    text = open(a.report, errors="ignore").read()
    body = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    srcs = {}
    for s in a.source:
        name, path = s.split("=", 1); srcs[name] = open(path, errors="ignore").read()
    norm = lambda x: re.sub(r"\s+", " ", x).strip()
    joined = norm(" ".join(srcs.values()))
    words = len(re.findall(r"\b\w[\w'./-]*\b", body))
    quotes = [q for q in re.findall(r'"([^"\n]{12,})"|`([^`\n]{12,})`', body)]
    quotes = [q[0] or q[1] for q in quotes]
    resolved, unresolved = [], []
    for q in quotes:
        (resolved if norm(q) in joined else unresolved).append(q)
    line_refs = re.findall(r"\b(?:line|lines|L)\s?\d+", body)
    names_used = [n for n in srcs if n in body]
    print(json.dumps({"words": words, "words_ok": a.min <= words <= a.max, "quotes": len(quotes),
                      "quotes_resolved": len(resolved), "quotes_unresolved": unresolved[:8],
                      "line_number_refs": line_refs[:8], "sources_named": names_used,
                      "has_think": "<think>" in text}, indent=1))

if __name__ == "__main__": main()
