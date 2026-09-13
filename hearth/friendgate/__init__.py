"""The friend gate: an OpenAI-compatible mouth on OMEN loopback for an invited friend's own agent.

    friend's harness (OpenHands, aider, Cline, Hermes ...) on the friend's machine, files on their disk
        -> https://omen.tail8e749c.ts.net/v1        (Funnel -> Caddy :8711, handle /v1/*)
        -> 127.0.0.1:8791                           (this package)
        -> 127.0.0.1:8081/v1                        (llama-swap -> llama-server)

What the gate does, and nothing more (BotHerder non-goal: no second job system):
- authenticates a per-friend bearer (sha256 at rest in hearth/var/friendgate/keys.json), mapped to an
  IRC account;
- lists only the models that are resident AND offer >= FRIENDGATE_MIN_CONTEXT tokens per slot
  (by day that is nobody: production's 8 x 16k slots are useless to an agent -> 503 "off hours");
- refuses over-budget requests up front, the way the door does, instead of letting llama-server
  truncate silently (ADR-0018);
- limits concurrency per key and globally (the owner keeps a slot), and a per-day token budget;
- injects the rung's bearer (OMEN_ARC_TOKEN, sourced only via hearth/etc/with-gateway-env.cmd) so the
  friend never holds it, and forwards /v1/chat/completions verbatim, streaming included;
- writes one usage row per request to hearth/var/friendgate/usage.ndjson (principal irc_account:<id>).

Admin paths of llama-swap (/running, /unload, /upstream/*, /ui, /logs, /metrics) are never reachable
through here: only /healthz, /v1/models and /v1/chat/completions exist.
"""
