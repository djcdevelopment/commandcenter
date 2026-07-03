# BUILD-NOTES-HB — Stream H-B · Hands (tool surface)

2026-07-03 · branch `worktree-agent-a0a5804a3974ac9d2` · builds HEARTH L1 per
HEARTH-BUILD-ORCHESTRATION.html / HEARTH-FULL-BUILDOUT.html.

## What shipped

The lab's only write path: six pure tool-provider modules under `hearth/toolsurface/`.
Each exposes `get_tools() -> list[Callable]` — plain typed python functions,
JSON-serializable args/returns, one-line docstrings (they become the MCP tool
descriptions), `ValueError` on bad input, **zero imports from hearth.kernel** (verified
by test). Stdlib + subprocess only; the mcp SDK is not required to import or test any
of this. The gateway (Stream H-A) wraps these with auth/provenance/ledger.

Warm-path note (Derek's design update): fs is pure stdlib (no subprocess at all);
git and testing are one direct `subprocess.run` per operation — no shells, no wrappers,
no extra layers.

### Sandbox scoping (the lockdown seam)

`hearth/toolsurface/_scope.py` — every path-taking tool (fs, git, testing, knowledge)
resolves paths against `HEARTH_SCOPE` (env var; default = the repo root the module
ships in, same pattern as `corpus_guard.REPO_ROOT`). Containment is checked on the
fully **resolved** path (`Path.resolve()` + `is_relative_to`), so `..` hops and
symlinks cannot escape. Read at call time, so H2 lockdown is an env-var change.

## Tool inventory

| Module | Tool | One-liner |
|---|---|---|
| fs.py | `read_file(path, max_bytes=200000)` | Read a UTF-8 text file inside the sandbox, truncated to max_bytes (size/truncated flags) |
| fs.py | `write_file(path, content, create_dirs=False)` | Write UTF-8 text inside the sandbox, optionally creating parent dirs |
| fs.py | `list_dir(path='.')` | List a sandbox directory: name, kind, size per entry |
| fs.py | `glob_files(pattern, root='.')` | Recursive glob under a sandbox dir; symlinked escapees filtered out |
| git.py | `git_status(repo='.')` | Branch, cleanliness, changed paths (porcelain v1) |
| git.py | `git_diff(repo='.', staged=False, max_bytes=100000)` | Unified diff, truncated with a flag |
| git.py | `git_log(repo='.', n=10)` | Recent commits: sha, author, ISO date, subject |
| git.py | `git_commit_push(message, repo='.', add_all=True, push=False)` | ONE audited stage+commit+push; **push runs even on a clean tree** (the F/0 fix) |
| testing.py | `run_tests(suite='tests', runner='unittest', timeout_s=600)` | Failures-only digest: {ran, failures, errors, ok, failing_tests[{id, short_traceback≤15 lines}], duration_s} — never full stdout |
| testing.py | `lint_digest(paths=None)` | ruff/flake8 digest if one is on PATH, else {available: false} |
| knowledge.py | `record_event(event, events_path='runs/hearth/events.jsonl')` | Validate + append via the existing `append_event` machinery |
| knowledge.py | `project(kinds=['all'], sources=['runs'], out='knowledge', allow_fixture_sources=False)` | Run the existing projectors; per-kind ok/error digest |
| knowledge.py | `query_capabilities(knowledge_dir='knowledge')` | capabilities.json + file mtime |
| knowledge.py | `query_findings(knowledge_dir='knowledge')` | findings.json + file mtime |
| knowledge.py | `query_beliefs_summary(knowledge_dir='knowledge')` | Cross-file counts + mtimes digest, not full contents |
| summon.py | `wake_am4()` | [stub] `ssh derek@am4.tail8e749c.ts.net 'sudo systemctl start am4-hermes-backend.service'` (+ preflight/verify from am4-fleet-node docs) |
| summon.py | `start_ollama(model='qwen3-coder:30b')` | [stub] `ollama serve` + warmup /api/generate hit to load the model resident |
| summon.py | `checkpoint_vm(name)` | [stub] Hyper-V `Checkpoint-VM -Name '<name>'` (+ Export-VMSnapshot), the proven snapshot path |
| inference.py | `local_generate(prompt, model='qwen3-coder:30b', endpoint='http://127.0.0.1:11434', system=None, max_tokens=1024, timeout_s=120)` | Blocking Ollama /api/generate (stream=false): {text, tokens_in, tokens_out, duration_ms}; `HEARTH_OLLAMA` overrides the default endpoint; connection failure → {ok:false, error}, not an exception |

All summon stubs return `{ok: false, stub: true, would_run: <real command>}` — shapes
are final, H3 flips them live.

## How knowledge.py wires into the existing machinery

**Direct imports, no shelling out** — the projector entry points were importable as-is:

- `record_event` → `tools.workflow.append_event.append_event` (which itself calls
  `validate_events.validate_event`); `ValidationError` is re-raised as `ValueError`
  per the provider contract.
- `project` → the same `materialize_*` functions the projector CLIs call:
  `project_capacity.materialize_knowledge`, `project_findings.materialize_findings`,
  `project_associations.materialize_associations`, `project_coverage.materialize_coverage`,
  `project_experiments.materialize_experiments`, `project_policy.materialize_policy`
  (policy last — it consumes the findings.json materialized upstream).
  `collect_event_files` + `check_fixture_taint` run first, exactly like the CLIs, so
  the corpus-regression guard AND the fixture-taint guard keep enforcing through this
  path (a guard refusal surfaces as `{ok:false, error}` in the per-kind digest).
- Queries read `knowledge/*.json` with `utf-8-sig` (matching the repo's BOM-tolerant
  readers) and return file mtimes.
- `knowledge.py` inserts its repo root into `sys.path` before the `tools.workflow`
  imports so the gateway can run from any cwd.

## Tests

`hearth/tests/toolsurface/` — **54 tests, all green** on system Python 3.12
(`python -m unittest discover -s hearth/tests`):

- fs: write/read roundtrip, truncation flags, scope-escape rejection (relative + absolute), glob escape filtering
- git: throwaway temp repo + local bare remote; **clean-tree-still-pushes proven**
  (unpushed local commit, clean tree, `push=True` → `committed:false, pushed:true`,
  remote HEAD advanced); commit/push/log/diff/status; scope rejection
- testing: synthetic failing suite in a temp sandbox → digest {ran:3, failures:1,
  errors:1}, tracebacks ≤15 lines, passing test's name never leaks; lint availability shape
- knowledge: fixture run trees **copied into a temp sandbox** (never the real
  knowledge/); record_event roundtrip + invalid-event ValueError; project single-kind,
  all-kinds dependency order, per-kind fault reporting; queries before/after projection
- summon: stub shapes + real command substrings
- inference: mocked `urllib.request.urlopen` (no live Ollama) — field mapping,
  connection-failure-as-result, env override, precedence
- provider contract: every module's get_tools() callables have docstrings + full type
  hints, no `hearth.kernel` imports, unique tool names across the surface

Regression: existing suite **177/177 OK** (`python -m unittest discover -s tests`).

## Deviations

1. **`git_commit_push` parameter order** — the brief's `(repo='.', message, ...)` is
   not valid Python (non-default after default); shipped as
   `git_commit_push(message, repo='.', add_all=True, push=False)`.
2. **`HEARTH_OLLAMA` precedence** — the env var overrides the *default* endpoint; an
   explicitly passed non-default `endpoint` argument still wins (tested both ways).
3. **Extra module** `_scope.py` (private, exposes no tools) so fs/git/testing/knowledge
   share one containment check instead of four copies.

`hearth/__init__.py` and `hearth/tests/__init__.py` are empty per the cross-stream
merge rule. No skip-marked tests were needed — the projector APIs take explicit out
dirs, so everything runs against temp sandboxes.
