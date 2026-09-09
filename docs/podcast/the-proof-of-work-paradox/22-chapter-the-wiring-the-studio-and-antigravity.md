# The HEARTH Wire: The Proof of Work Paradox
## Chapter 22: Wiring the Studio & Antigravity

**Setting:** The sound of a virtual cable plugging into a console socket—a clean, satisfying electrical snap, followed by the immediate hum of a new data stream syncing up.

---

**ALEX:**
Let’s look at step one of our five-point strategy: the assistant sitting right here with us. Derek uses Google’s Antigravity environment as his primary AI pair programming assistant. And earlier tonight, we looked inside `C:\Users\derek\.gemini\config\mcp_config.json`. Sam, what was in that file?

**SAM:**
Nothing. It was zero bytes.
Antigravity supports the Model Context Protocol (MCP). It can connect to local tool servers over standard I/O, or to remote services over Server-Sent Events (SSE). But because `mcp_config.json` was empty, Antigravity was completely unaware that an enterprise-grade MCP gateway with twenty-two custom tools was running on `127.0.0.1:8710`.

**ALEX:**
And what does that mean in practice?
In [`c:\work\commandcenter\AGENTS.md`](file:///c:/work/commandcenter/AGENTS.md), there is a mandatory user rule:
> *"HEARTH is an always-on MCP door on loopback at http://127.0.0.1:8710/mcp. Before spending metered frontier tokens on a self-contained sub-task, delegate it with `local_generate`. Keep frontier reasoning for architecture, multi-file logic, judgment... Spend freely on grunt work."*
And yet, because the MCP bridge was never wired into Antigravity’s config, Antigravity couldn't call `local_generate`! Every single sub-task had to be reasoned out inline using Google’s frontier tokens!

**SAM:**
It was burning frontier tokens on boilerplate, and it was leaving zero trace in the HEARTH ledger.
So here is the fix:
We drop an MCP configuration into `C:\Users\derek\.gemini\config\mcp_config.json`:
```json
{
  "mcpServers": {
    "hearth": {
      "command": "python",
      "args": ["-m", "hearth.callers.stdio_bridge"],
      "env": {
        "HEARTH_ENDPOINT": "http://127.0.0.1:8710/mcp",
        "HEARTH_KEY": "derek-studio-key-c839f1"
      }
    }
  }
}
```

**ALEX:**
And look at what key it uses: `derek-studio-key-c839f1`.
We don't use the generic `dev-local` key. We register a brand new, first-class human identity in `hearth/var/callers.json`:
```json
"derek-studio-key-c839f1": {
  "id": "derek-studio",
  "runner_class": "human",
  "node": "omen",
  "profile": "unrestricted"
}
```
And in `hearth/projection/public_portfolio.py`, we add `derek-studio` to `AGENT_LANE_BY_CALLER`, mapping it to a brand new public agent lane:
**`Lead Engineer (Studio)`**!

**SAM:**
Think about what happens the moment that is wired up:
Every time Antigravity calls `local_generate` to summarize a file or draft boilerplate, the request hits port 8710.
The gateway logs the event under `derek-studio`.
The Vulkan inference engine on the dual Intel Arc Pro B70s executes the token generation.
The input and output tokens are measured and stamped into a token-bearing receipt.
And on `steppeintegrations.com`, a brand new bar appears on the chart: **Lead Engineer (Studio)**, displaying real work calls, real token receipts, and real job completions!

**ALEX:**
The human architect finally gets his own lane on his own dashboard.
That fixes the AI assistant. But what about when Derek is just typing commands in the shell? In Chapter Twenty-Three, we wire the physical terminal: The Git & Shell Relays.
