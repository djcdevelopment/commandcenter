#!/usr/bin/env python3
"""Multi-hop tool-calling probe against an OpenAI-compatible llama-server (needs --jinja).

The model gets three file tools sandboxed to --root and a question that needs several hops.
We execute the calls, feed results back, and record per-turn prompt tokens, latency, malformed
calls and the final answer. Optional --pad FILE prepends a long background document to stress context.

usage: toolprobe.py --port 8001 --root DIR --question "..." [--pad FILE --pad-tokens N] [--max-turns 25]
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request

TOOLS = [
 {"type": "function", "function": {"name": "list_dir", "description": "List entries of a directory (relative to the repo root).",
   "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
 {"type": "function", "function": {"name": "read_file", "description": "Read a text file (relative path). Returns at most 200 lines starting at start_line (1-based).",
   "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}}, "required": ["path"]}}},
 {"type": "function", "function": {"name": "grep", "description": "Search files under path for a regex; returns up to 60 matching lines as path:line:text.",
   "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern", "path"]}}},
]

def safe(root, p):
    full = os.path.realpath(os.path.join(root, p or "."))
    if not full.startswith(os.path.realpath(root)): raise ValueError("path escapes root")
    return full

def run_tool(root, name, args):
    if name == "list_dir":
        d = safe(root, args.get("path", ".")); ents = sorted(os.listdir(d))[:200]
        return "\n".join(e + ("/" if os.path.isdir(os.path.join(d, e)) else "") for e in ents) or "(empty)"
    if name == "read_file":
        f = safe(root, args["path"]); s = max(1, int(args.get("start_line") or 1))
        lines = open(f, errors="ignore").read().splitlines()
        return "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines) if s-1 <= i < s-1+200) or "(empty)"
    if name == "grep":
        d = safe(root, args.get("path", "."))
        r = subprocess.run(["grep", "-rnE", "--include=*", args["pattern"], d], capture_output=True, text=True)
        out = [l.replace(os.path.realpath(root) + "/", "") for l in r.stdout.splitlines()][:60]
        return "\n".join(x[:300] for x in out) or "(no matches)"
    return f"unknown tool {name}"

def chat(port, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r: return json.load(r)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001); ap.add_argument("--root", required=True)
    ap.add_argument("--question", required=True); ap.add_argument("--pad"); ap.add_argument("--pad-tokens", type=int, default=0)
    ap.add_argument("--max-turns", type=int, default=25); ap.add_argument("--label", default=""); ap.add_argument("--max-tokens", type=int, default=2048)
    a = ap.parse_args()
    sysmsg = ("You are a careful engineer answering a question about a repository using the tools. "
              "Use tools to look at files before answering; cite file paths. When done, answer in plain text without calling tools.")
    msgs = [{"role": "system", "content": sysmsg}]
    if a.pad:
        text = open(a.pad, errors="ignore").read()
        words = text.split()[: int(a.pad_tokens / 1.4)] if a.pad_tokens else text.split()
        msgs.append({"role": "user", "content": "Background reading (reference only, do not summarize it):\n\n" + " ".join(words)})
        msgs.append({"role": "assistant", "content": "Noted. I have read the background material."})
    msgs.append({"role": "user", "content": a.question})
    log = {"label": a.label, "turns": [], "malformed": 0, "tool_calls": 0, "errors": 0}
    t_start = time.time(); final = None
    for turn in range(a.max_turns):
        t0 = time.time()
        try:
            r = chat(a.port, {"model": "flash-next", "messages": msgs, "tools": TOOLS, "tool_choice": "auto",
                              "temperature": 0, "max_tokens": a.max_tokens})
        except Exception as e:
            log["errors"] += 1; log["turns"].append({"turn": turn, "error": str(e)[:300]}); break
        dt = time.time() - t0; ch = r["choices"][0]; m = ch["message"]; u = r.get("usage", {})
        calls = m.get("tool_calls") or []
        rec = {"turn": turn, "s": round(dt, 1), "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
               "finish": ch.get("finish_reason"), "n_calls": len(calls), "calls": []}
        if (m.get("reasoning_content") or "") : rec["reasoning_chars"] = len(m["reasoning_content"])
        msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls") and v is not None})
        if not calls:
            final = m.get("content"); rec["final_chars"] = len(final or ""); log["turns"].append(rec); break
        for c in calls:
            fn = c["function"]["name"]; raw = c["function"].get("arguments") or "{}"
            try: args = json.loads(raw) if isinstance(raw, str) else raw
            except Exception: args = None
            if args is None:
                log["malformed"] += 1; out = "error: arguments were not valid JSON"
            else:
                try: out = run_tool(a.root, fn, args)
                except Exception as e: out = f"error: {e}"
            log["tool_calls"] += 1
            rec["calls"].append({"fn": fn, "args": (args if args is not None else raw)[:120] if isinstance(args, str) or args is None else args, "out_chars": len(out)})
            msgs.append({"role": "tool", "tool_call_id": c.get("id", f"call_{turn}"), "name": fn, "content": out[:12000]})
        log["turns"].append(rec); print(json.dumps(rec), flush=True)
    log["total_s"] = round(time.time() - t_start, 1); log["final"] = final
    print(json.dumps({"label": a.label, "summary": True, "turns": len(log["turns"]), "tool_calls": log["tool_calls"],
                      "malformed": log["malformed"], "errors": log["errors"], "total_s": log["total_s"],
                      "last_prompt_tokens": next((t.get("prompt_tokens") for t in reversed(log["turns"]) if t.get("prompt_tokens")), None)}))
    print("FINAL ANSWER:\n" + (final or "(none)"))
    json.dump(log, open(f"toolprobe-{a.label or 'run'}.json", "w"), indent=1)

if __name__ == "__main__": main()
