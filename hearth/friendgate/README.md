# The friend gate — runbook (owner) and walkthrough (friend)

ADR-0046. An invited friend's OWN agent (OpenHands, aider, Cline, Hermes — anything that speaks the
OpenAI chat-completions API) uses the B70s through `https://omen.tail8e749c.ts.net/v1` with a
per-friend key. The friend's files stay on the friend's machine; only prompts and completions travel.

## Owner runbook

```
PY=C:\work\commandcenter\fleet-worker-node\.venv-omen\Scripts\python.exe   (PYTHONPATH=C:\work\commandcenter)

%PY% -m hearth.friendgate.friendctl mint --account <label> --display "Name" [--expires-days 30]
%PY% -m hearth.friendgate.friendctl list
%PY% -m hearth.friendgate.friendctl usage [--account <label>]
%PY% -m hearth.friendgate.friendctl revoke <key_id>          # takes effect on the friend's next request
```

- `--account` is the attribution label (an Ergo account if they are in the IRC community, otherwise
  any stable handle, e.g. their Discord name). The gate does not check it against Ergo.
- The plaintext key is printed once by `mint`; the file holds sha256 only. Send it over a channel you
  trust; rotating = `revoke` + `mint`.
- Service: `fleet/friendgate/serve-friendgate.cmd` (through `with-gateway-env.cmd`; unarmed = 503).
  Boot task `HearthFriendGateBoot` (clone of `HearthFunnelProxyBoot`; register from an elevated shell —
  the one-liner is in ADR-0046 / the 2026-09-13 session). Health: `curl http://127.0.0.1:8791/healthz`
  shows the eligible models; `fleet/inventory.toml` watches the port.
- Hours are the serving shape: a friend can only use models with ≥ 64k tokens per slot, i.e. the night
  shape's `qwen38-27b-mtp` today. `ArcServeNight` on → open; day shape → every request answers
  503 `off_hours`. Nothing to schedule in the gate.
- Limits per key (defaults): 1 request at a time, 65,536 tokens per request, 3,000,000 tokens/day.
  Global: 1 friend request at a time (`FRIENDGATE_MAX_CONCURRENT`), so you keep the other slot.
- Privacy note for you: the gate logs no bodies and no access log; Caddy redacts `Authorization`.
  But llama-server on the night shape runs `-lv 5` with a log file (`hearth/var/swap-logs/*.log`),
  which can include request content at that verbosity. Lower it in `omen-night.yaml` if friends'
  prompts should never touch a log.

## Walkthrough for the friend (paste-ready)

> You've got three things from me: a **URL**, a **key**, and a **model name**. Any tool that can talk
> to an "OpenAI-compatible" server can use my GPUs with them. Your files stay on your computer; only
> the conversation goes to my machine.
>
> **1. Check it works (30 seconds)** — in a terminal:
> ```
> curl -H "Authorization: Bearer YOUR_KEY" https://omen.tail8e749c.ts.net/v1/models
> ```
> If you see `qwen38-27b-mtp` in the list, it's on. If you see `off_hours`, my cards are in day mode —
> try again in the evening (Europe morning is my night, so mornings are usually good for you).
>
> **2a. OpenHands** (the one you mentioned) — in Settings → LLM, switch on **Advanced** and enter:
> - Custom Model: `openai/qwen38-27b-mtp`
> - Base URL: `https://omen.tail8e749c.ts.net/v1`
> - API Key: `YOUR_KEY`
> Save, open your project folder, and give it a task. OpenHands does its own context summarising — keep
> it on.
>
> **2b. aider** (simplest if you like the terminal) — in your project folder:
> ```
> pip install aider-chat
> aider --model openai/qwen38-27b-mtp --openai-api-base https://omen.tail8e749c.ts.net/v1 --openai-api-key YOUR_KEY
> ```
>
> **2c. Cline / Continue / anything else** — provider "OpenAI compatible", same three values.
>
> **3. What to expect**
> - It's one 27B model on two graphics cards in my house, not a datacenter: about 75 tokens/second on a
>   short conversation, and much slower once a conversation grows past ~50,000 tokens. Start new chats
>   often; give it small, concrete tasks ("change the Termine page to a table") rather than "redo the site".
> - **One request at a time.** If your tool fires two, the second gets a "busy, retry" and most tools
>   just wait. Each request is capped at 64k tokens; over that it's refused with a clear message.
> - You have a daily budget of 3 million tokens — plenty; it resets at my midnight.
> - The key is yours alone. If it leaks, tell me and I'll issue a new one in a minute.
>
> **4. Rules of the house**: don't share the URL+key, don't try to hit anything but `/v1/…` (there's
> nothing else there), and if it's misbehaving tell me instead of hammering it.
