# The HEARTH Wire: The Proof of Work Paradox
## Chapter 03: The Missing Architect

**Setting:** A sharp, percussive sound effect—like a stamp hitting paper—followed by the quiet ticking of a clock. The tone is forensic and inquisitive.

---

**ALEX:**
Sam, let’s look at the identity roster. HEARTH is gated. You don't just connect to port 8710 anonymously; every single tool invocation requires an HTTP header: `X-Hearth-Key`. That key is looked up in a JSON registry file: `hearth/var/callers.json`. And when the gateway accepts a call, it binds that key to an identity, a runner class, a host node, and a capability profile.

**SAM:**
Right. Let’s look at who is in that registry.
- `botherder-am4`: Running on the AM4 machine, connected via Tailscale, running the IRC adapter. That single identity accounts for **114,388 calls**.
- `dmos-poc`: The image generation client on OMEN. **45,420 calls**.
- `clippy-dispatcher`: The video highlight rendering pipeline. **12,064 calls**.
- `claude-frontier`: The Claude Code CLI session connected over MCP. **2,198 calls**, including 1,685 high-value work calls.
- `mechnet-watchdog` and `dev-local`: Mapped to the generic lane called **`Automation`**. That accounts for **55,119 calls**.

**ALEX:**
Now scan the list of callers in `hearth/projection/public_portfolio.py`. Find Derek. Find the human engineer.

**SAM:**
He isn't there. There is no `derek` lane. There is no `lead_engineer` lane. There is no `architect` lane.

**ALEX:**
How is that possible? The man owns the hardware, designed the network, and sits in front of the monitors eighteen hours a day. Where did his work go?

**SAM:**
It went into two dead ends.
First dead end: whenever Derek ran manual scripts or local test probes on the OMEN machine, he used the development key: `dev-local`. But in `public_portfolio.py`, line 132 maps `dev-local` directly to `automation`. So every manual, hands-on probe he executed was swallowed into the same bucket as the mindless keepalive cron jobs.

**ALEX:**
And the second dead end?

**SAM:**
The second dead end is much larger: the vast majority of his actual day-to-day engineering **never touched port 8710 at all**.
When Derek opens Windows Terminal and runs `git commit`, he’s using standard Git CLI. He’s not calling `git_commit` through the HEARTH MCP gateway.
When he opens PowerShell and runs `pytest`, he’s invoking the local Python interpreter directly. He’s not routing the assay through `testing.py` on the gateway.
When he uses an AI assistant like Antigravity—this very environment we are speaking from—Antigravity’s MCP configuration in `~/.gemini/config/mcp_config.json` was sitting at **zero bytes**. Blank. Unconnected.

**ALEX:**
So every conversation, every refactoring pass, every architectural debate, every line of code generated right here inside Antigravity was operating as a shadow pipeline. It was doing real work, burning real CPU cycles, solving real engineering problems... and leaving zero receipts in the HEARTH ledger.

**SAM:**
Exactly. The machine was diligently recording its own automated heartbeat, while the human architect was doing 99% of his work off the books. And that is why Derek felt that friction. That’s why he said: *"I need to show and prove to people I'm using them, not just use them."* Because right now, the public ledger is giving credit to the robots and rendering the human invisible.

**ALEX:**
To understand how we fix this, we have to understand how we got here. Because Derek didn't start with a multi-box fleet and 200,000 events. He started six months ago, in March 2026, on a single consumer GPU, with a handful of Python scripts and an idea. In Chapter Four, we go back to the beginning: The First Trinity.
