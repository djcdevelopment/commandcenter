#!/usr/bin/env node
// rnd-mode — carry an R&D session posture across context compression.
//
// Why this exists: standing instructions live in CLAUDE.md and memory, which means the agent has
// to read and remember them. A background context compression the agent never sees eats that,
// and the agent resumes in its default posture — which is enterprise baseline mode, where unit
// tests and gates are the deliverable. On 2026-08-25 that cost a full day: two threads in a row
// built verification apparatus around code that had never been run, while the stated objective
// went undone. Derek's assessment of the fix was exact: a rule the agent has to remember is not
// worth the byte.
//
// So the posture is carried by the harness. Registered on UserPromptSubmit, this prints the R&D
// contract on EVERY prompt. There is no window in which it can be forgotten, because it is never
// remembered — it is re-supplied.
//
// Discipline copied from check-nested-claude.mjs, which shares this directory:
//   * the happy path writes nothing;
//   * every path exits 0.
// The second rule is load-bearing here in a way it is not there. This runs on every prompt in
// every project on this machine. A hook that throws, hangs, or exits non-zero would degrade or
// break every session, including the eleven that never asked for R&D mode. When in doubt, this
// stays silent and gets out of the way.
//
// State: %USERPROFILE%/.claude/rnd-mode.json, keyed by session id. Written by the /rnd skill.
// Usage:
//   node rnd-mode.mjs            # hook mode: reads the hook payload on stdin
//   node rnd-mode.mjs --self-test  # prove the failure paths stay silent and exit 0

import { readFileSync, writeFileSync, existsSync, mkdirSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';

const STATE_PATH = join(homedir(), '.claude', 'rnd-mode.json');
const SCHEMA = 'rnd-mode/v1';
// A session someone walked away from should not still be steering a week later. The /rnd skill
// is cheap to re-invoke; a stale posture is not cheap to notice.
const MAX_AGE_HOURS = 24;

export function readState(path = STATE_PATH) {
  try {
    if (!existsSync(path)) return { schema: SCHEMA, sessions: {} };
    const parsed = JSON.parse(readFileSync(path, 'utf8'));
    if (!parsed || typeof parsed !== 'object' || parsed.schema !== SCHEMA) {
      return { schema: SCHEMA, sessions: {} };
    }
    if (!parsed.sessions || typeof parsed.sessions !== 'object') {
      return { schema: SCHEMA, sessions: {} };
    }
    return parsed;
  } catch {
    // Corrupt state is not an error worth surfacing on every prompt. It means "no R&D mode".
    return { schema: SCHEMA, sessions: {} };
  }
}

export function writeState(state, path = STATE_PATH) {
  mkdirSync(join(homedir(), '.claude'), { recursive: true });
  writeFileSync(path, JSON.stringify(state, null, 2) + '\n', 'utf8');
}

export function prune(state, nowMs = Date.now()) {
  const cutoff = nowMs - MAX_AGE_HOURS * 3600 * 1000;
  const kept = {};
  for (const [id, entry] of Object.entries(state.sessions ?? {})) {
    const since = Date.parse(entry?.since ?? '');
    if (Number.isFinite(since) && since >= cutoff) kept[id] = entry;
  }
  return { schema: SCHEMA, sessions: kept };
}

/**
 * The contract, re-supplied every prompt. Deliberately short — this is paid for on every turn,
 * so it carries only what changes behaviour, and points at the artifacts for the rest.
 */
export function contract(entry) {
  const probe = entry?.probe?.trim();
  const log = entry?.log?.trim() || 'the project edge log (ask once, then remember it here)';
  return [
    '<rnd-mode>',
    `R&D MODE is active${probe ? ` — probing: ${probe}` : ''}. Started ${entry?.since ?? 'this session'}.`,
    '',
    'Sampling for edges in an environment whose shape is not yet known. This is not baseline',
    'mode. It is re-supplied every prompt because it must survive a context compression you',
    'will not be aware of.',
    '',
    '  - NO test files. Not fewer — none, unless Derek names one.',
    '  - Vertical slice first: connect it end to end before refining any layer.',
    '  - ONE edge ends the lap. Record it and set up the next probe. Do not harden, do not',
    '    generalize, do not chase a second edge in the same lap.',
    '  - A clean sample is a result. "Probed X, no edge here" saves a future lap.',
    '  - Momentum over completeness. Throw the rest of the samples out.',
    '',
    'End every turn with exactly three things:',
    `  1. An edge log entry → ${log}`,
    '  2. The exact command to re-run the slice',
    '  3. The uncertainty list — read it off the tool wherever the tool emits one (rehearsal',
    "     available_paths[]/limitations[] and their kin). Do not compose it as prose; the tool",
    '     already knows what it did not sample.',
    '',
    'If you are reaching for a test right now, that is the tell. The task that grades itself is',
    'not the task. Leave with /rnd --off, or /rnd --lock to pin what survived.',
    '</rnd-mode>',
  ].join('\n');
}

function readStdin() {
  try {
    return readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

function selfTest() {
  const tmp = join(homedir(), '.claude', `rnd-mode.selftest-${process.pid}.json`);
  const results = [];
  const check = (name, ok) => results.push({ name, ok });

  try {
    // Missing state: silent, no crash.
    if (existsSync(tmp)) unlinkSync(tmp);
    check('missing state yields no sessions', Object.keys(readState(tmp).sessions).length === 0);

    // Corrupt state: silent, no crash. This is the one that matters — a throw here would break
    // every prompt in every project on this machine.
    writeFileSync(tmp, '{ this is not json', 'utf8');
    check('corrupt state yields no sessions', Object.keys(readState(tmp).sessions).length === 0);

    // Wrong schema is treated as absent rather than guessed at.
    writeFileSync(tmp, JSON.stringify({ schema: 'something-else', sessions: { a: {} } }), 'utf8');
    check('foreign schema is ignored', Object.keys(readState(tmp).sessions).length === 0);

    // Pruning drops the stale and keeps the live.
    const now = Date.parse('2026-08-25T12:00:00Z');
    const pruned = prune({
      schema: SCHEMA,
      sessions: {
        fresh: { since: '2026-08-25T11:00:00Z' },
        stale: { since: '2026-08-20T11:00:00Z' },
        bogus: { since: 'not a date' },
      },
    }, now);
    check('prune keeps fresh, drops stale and unparseable',
      Object.keys(pruned.sessions).join(',') === 'fresh');

    // The contract says the thing it exists to say.
    const text = contract({ since: 'x', probe: 'y', log: 'docs/rnd-log.md' });
    check('contract forbids tests', text.includes('NO test files'));
    check('contract names the three deliverables',
      text.includes('edge log entry') && text.includes('re-run the slice') && text.includes('uncertainty list'));
  } finally {
    try { if (existsSync(tmp)) unlinkSync(tmp); } catch { /* ignore */ }
  }

  const failed = results.filter((r) => !r.ok);
  for (const r of results) process.stdout.write(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}\n`);
  process.stdout.write(failed.length === 0
    ? `SELFTEST PASS: ${results.length} checks\n`
    : `SELFTEST FAIL: ${failed.length} of ${results.length}\n`);
  process.exit(failed.length === 0 ? 0 : 1);
}

function main() {
  if (process.argv.includes('--self-test')) return selfTest();

  // Everything below is best-effort by design. Silence is the correct failure.
  try {
    const raw = readStdin();
    if (!raw.trim()) return process.exit(0);

    let payload;
    try { payload = JSON.parse(raw); } catch { return process.exit(0); }

    const sessionId = payload?.session_id;
    if (!sessionId) return process.exit(0);

    const state = readState();
    const entry = state.sessions?.[sessionId];
    if (!entry) return process.exit(0);

    const since = Date.parse(entry.since ?? '');
    if (!Number.isFinite(since) || Date.now() - since > MAX_AGE_HOURS * 3600 * 1000) {
      return process.exit(0);
    }

    process.stdout.write(contract(entry) + '\n');
  } catch {
    // Deliberately swallowed. This runs on every prompt in every project; a stack trace here
    // would be worse than the missing posture.
  }
  process.exit(0);
}

main();
