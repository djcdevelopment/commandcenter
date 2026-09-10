# The HEARTH Wire: The Proof of Work Paradox
## Chapter 23: The Git & Shell Relays

**Setting:** The fast, confident clatter of a developer typing rapid commands in a bash shell: `git commit -m "feat..."`, `pytest -v`, `git push`. A tight, energetic groove propels the conversation.

---

**ALEX:**
Now let’s tackle the command line. An engineer spends hours every day inside PowerShell and Windows Terminal. They type `git commit`, they type `pytest`, they run build scripts. And right now, the public dashboard says: *Git / VCS: 66 events. Test / assay: 30 events.*
Sam, how do we make every single Git commit and every single test run automatically register on port 8710 without forcing Derek to change how he types in the terminal?

**SAM:**
We use Git’s built-in hook architecture: `.git/hooks/post-commit`.
Git has supported lifecycle hooks for twenty years. A `post-commit` hook is an executable script that Git runs automatically immediately after a commit is successfully created.
So we install a tiny PowerShell script in `.git/hooks/post-commit`:
```powershell
#!/usr/bin/env pwsh
$commitSha = git rev-parse HEAD
$commitMsg = git log -1 --pretty=%s

$payload = @{
    tool = "git_commit"
    args = @{
        sha = $commitSha
        message = $commitMsg
        repo = "commandcenter"
    }
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:8710/mcp" -Method POST `
    -Headers @{ "X-Hearth-Key" = "derek-studio-key-c839f1" } `
    -Body $payload -TimeoutSec 2 -ErrorAction SilentlyContinue | Out-Null
```

**ALEX:**
Walk through what happens when he types `git commit -m "fix: reindex ledger"`.

**SAM:**
Derek types `git commit` exactly as he always has.
Git creates the commit object on the local branch.
Then Git executes the `post-commit` hook in the background.
The hook grabs the new commit SHA, packages it into a JSON payload, and fires a sub-two-millisecond HTTP call to `127.0.0.1:8710`.
The gateway catches the call, verifies the `derek-studio` key, stamps the timestamp, and appends a new `hearth-event.v1` to `events.ndjson` under the **`Git / VCS`** family.
Zero extra keystrokes for Derek. Complete, cryptographic attribution in the ledger.

**ALEX:**
And what about running tests? What about `pytest`?

**SAM:**
Same philosophy. In his PowerShell `$PROFILE`, we define a helper function:
`function Invoke-HearthTest { ... }`
Aliased as `htest`.
When Derek wants to run his tests, instead of typing `pytest tests/`, he types:
`htest`
What does `htest` do?
It starts a high-resolution stopwatch.
It invokes `pytest tests/`.
It captures the exit code—zero for green, non-zero for failure.
It stops the watch.
And then it calls a lightweight Python emitter:
`python -m hearth.callers.emit_assay --key "derek-studio-key-c839f1" --suite tests --ok $true --duration-ms 1420`
And immediately, `events.ndjson` logs a new event in the **`Test / assay`** family: *Suite passed, 1.42 seconds, authored by derek-studio.*

**ALEX:**
Think about how that transforms the public portfolio!
Instead of thirty test runs and sixty-six git commits over an entire summer, every single unit test run during daily development, every green test suite, every git commit across every repository automatically streams into the ledger!
The `Work Plane` line on `steppeintegrations.com` goes from zero to hundreds of verified events per week.

**SAM:**
And now we take the final step. Because logging individual tool calls is great, but how do you package an entire day's sprint into an undeniable proof of work that nobody can question?
In Chapter Twenty-Four, we create the ceremony: The Cryptographic Session Seal.
