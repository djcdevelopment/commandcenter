# The HEARTH Wire: The Proof of Work Paradox
## Chapter 21: The Leaky Funnel Autopsy

**Setting:** A sharp, decisive tone. The ambient noise fades, leaving a crisp, forward-leaning electronic tempo—the sound of an engineering team transitioning from diagnosis to execution.

---

**ALEX:**
Let’s look at the funnel. In marketing and sales, a leaky funnel is when hundreds of interested users arrive at your website, but a broken checkout button or a bad form causes ninety-nine percent of them to drop off before they buy.
Sam, what is the "Leaky Funnel" in Derek’s development workflow?

**SAM:**
The Leaky Funnel is the gap between Derek's physical keyboard and the HEARTH gateway on port 8710.
Consider everything Derek does in a typical six-hour engineering session:
1. He opens Windows Terminal, navigates to a repository, and runs `git status` or `git commit`.
2. He opens his editor—VS Code, Cursor, or the Antigravity IDE—and refactors code, writes ADRs, or drafts architectural plans.
3. He opens PowerShell and runs `pytest` or `dotnet test` to verify his changes.
4. He interacts with an AI assistant—like Antigravity—asking it to analyze files, generate implementations, or trace logs.
Now, here is the autopsy question:
*How many of those four actions pass through the HEARTH gateway?*

**ALEX:**
Zero. Not a single one.
The Git commit runs locally through standard `git.exe`.
The editor modifies files directly on the NTFS filesystem.
The test suite runs through local Python or .NET runtimes.
And Antigravity communicates directly with Google's frontier models over HTTPS, with an empty `mcp_config.json`.

**SAM:**
So from HEARTH's perspective—from the perspective of `events.ndjson` and `steppeintegrations.com`—**none of that work ever happened**.
The only things that registered on the gateway were the background watchdogs asking if the port was open, and the automated IRC bots responding to chat commands.

**ALEX:**
And this explains the psychological tension Derek felt. He’s putting in forty, fifty, sixty hours a week of intense cognitive labor. He is building, testing, fixing, teaching. And then he looks at his own public website, and the website says: *Git: 66 calls. Filesystem: 51 calls. Test: 30 calls.*
It makes him look like a spectator in his own lab!

**SAM:**
And why did this happen? It didn't happen because Derek didn't want to log his work. It happened because **the friction was too high**.
If an engineer has to manually craft an HTTP POST request to port 8710 with an `X-Hearth-Key` header every time they save a file or run a test, they will never do it. Developers take the path of least resistance. If the toolchain doesn't capture the telemetry automatically and invisibly, the work leaks into the dark.

**ALEX:**
So our engineering challenge is simple: **we have to plug the leaks without adding friction**.
We have to make the developer's everyday tools—Antigravity, Git, PowerShell, and the terminal—automatically and silently relay their receipts into port 8710.
In Chapter Twenty-Two, we start with the assistant in the room: Wiring the Studio & Antigravity.
