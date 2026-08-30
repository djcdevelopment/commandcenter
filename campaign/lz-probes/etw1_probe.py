"""Fixed-shape completion probe with bearer auth, for the ETW1 feasibility control.

The kit's probe_completion.py predates the server requiring auth and gets HTTP 401.
Token is read from the gitignored fragment the same way ff_ratecheck.py does it, and
is NEVER printed.

Usage: etw1_probe.py <port> <prompt_file> <n_predict>
Prints one JSON line on stdout.
"""
import io, json, os, sys, time, urllib.request, urllib.error

TOKEN_FRAGMENT = r"C:\work\commandcenter\hearth\var\gateway.cmd"


def token(var="OMEN_ARC_TOKEN"):
    """Read the rung's bearer from the fragment. Never printed."""
    if os.path.exists(TOKEN_FRAGMENT):
        for line in io.open(TOKEN_FRAGMENT, encoding="utf-8", errors="replace"):
            s = line.strip()
            if s.lower().startswith("set "):
                s = s[4:]
            if "=" in s:
                k, _, v = s.partition("=")
                if k.strip() == var:
                    return v.strip()
    return os.environ.get(var) or None


def main():
    port, prompt_file = sys.argv[1], sys.argv[2]
    n_predict = int(sys.argv[3]) if len(sys.argv) > 3 else 32

    prompt = io.open(prompt_file, encoding="utf-8").read()
    body = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "cache_prompt": True,
        "temperature": 0,
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:%s/completion" % port,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    tok = token()
    if tok:
        req.add_header("Authorization", "Bearer %s" % tok)

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            r = json.load(resp)
    except urllib.error.HTTPError as e:
        # Loud, and without leaking the bearer.
        print(json.dumps({"error": "HTTP %s" % e.code, "auth_sent": bool(tok)}))
        return 1
    wall = time.perf_counter() - t0

    t = r.get("timings", {})
    print(json.dumps({
        "wall_s": round(wall, 3),
        "prompt_n": t.get("prompt_n"),
        "prompt_ms": round(t.get("prompt_ms", 0), 2),
        "prefill_tps": round(t.get("prompt_per_second", 0), 1),
        "predicted_n": t.get("predicted_n"),
        "predicted_ms": round(t.get("predicted_ms", 0), 2),
        "decode_tps": round(t.get("predicted_per_second", 0), 2),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
