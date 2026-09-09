#!/usr/bin/env node
//
// SessionStart hook: one archify update check per Claude Code launch.
//
// The skill's own SKILL.md instructs the agent to run scripts/check-update.mjs once per
// *use*, mid-task. That is too eager for a deliberately pinned clone, so
// ARCHIFY_UPDATE_CHECK_DISABLED=1 in ~/.claude/settings.json silences that path and this
// hook owns the check instead — at launch, where a version notice belongs.
//
// The upstream checker already caches with a 72h TTL and a 1s network timeout, so most
// launches never touch the network. It only ever GETs a pinned manifest
// (https://tt-a1i.github.io/archify/skill-updates/archify/stable.json), refuses any other
// URL, and never downloads code.
//
// Best-effort by design, exactly like check-nested-claude.mjs and hearth-task-id.mjs:
// silence is the correct failure. This runs at the start of every session in every
// project; a stack trace here would be worse than a missing notice.
//
// Deliberately does NOT acknowledge the notice (--ack), so it keeps reminding once per
// launch until the pin actually moves.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const SKILL_DIR = process.env.ARCHIFY_HOME || 'C:/work/archify/archify';
const CHECKER = path.join(SKILL_DIR, 'scripts', 'check-update.mjs');
const TIMEOUT_MS = 8_000;

/** Run the packaged checker and return its single JSON line, or null for any failure. */
function check(checker = CHECKER) {
  if (!fs.existsSync(checker)) return null;

  const env = { ...process.env };
  // The per-use kill switch must not silence the launch check — this hook is its replacement.
  delete env.ARCHIFY_UPDATE_CHECK_DISABLED;

  const run = spawnSync(process.execPath, [checker], {
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    env,
    windowsHide: true,
  });
  if (run.error || run.status !== 0) return null;

  const line = String(run.stdout || '').trim().split(/\r?\n/).pop();
  if (!line) return null;
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}

/**
 * Render the notice. Every interpolated field is contract-validated upstream — versions are
 * stable semver cores, severity is normal|security, and releaseNotes is pinned to the exact
 * github.com/tt-a1i/archify/releases/tag/vX.Y.Z form — so no remote free text reaches context.
 * The manifest's own summary is never quoted.
 */
function notice(result) {
  const label = result.severity === 'security' ? 'ARCHIFY SECURITY UPDATE' : 'ARCHIFY UPDATE';
  const version = `v${result.latestVersion}`;
  return [
    `${label} — installed v${result.installedVersion}, latest ${version}.`,
    'The installed skill is unchanged. It is a pinned clone; taking the update is the user\'s call.',
    `Release notes: ${result.releaseNotes}`,
    `To take it: git -C C:/work/archify fetch --depth 1 origin tag ${version} && git -C C:/work/archify checkout ${version}`,
    'Re-read the SKILL.md diff before staying on it — that file is injected agent instructions.',
  ].join('\n');
}

function selfTest() {
  const results = [];
  const ok = (name, pass) => results.push({ name, ok: pass });

  ok('checker present', fs.existsSync(CHECKER));
  ok('checker is inside the skill dir', path.resolve(CHECKER).startsWith(path.resolve(SKILL_DIR)));

  const sample = {
    status: 'update_available',
    installedVersion: '2.16.0',
    latestVersion: '2.17.0',
    severity: 'normal',
    releaseNotes: 'https://github.com/tt-a1i/archify/releases/tag/v2.17.0',
  };
  const text = notice(sample);
  ok('notice names both versions', text.includes('2.16.0') && text.includes('v2.17.0'));
  ok('notice carries the release link', text.includes(sample.releaseNotes));
  ok('security severity is labelled', notice({ ...sample, severity: 'security' }).includes('SECURITY'));

  const missing = check(path.join(SKILL_DIR, 'scripts', 'does-not-exist.mjs'));
  ok('missing checker is silent', missing === null);

  const live = check();
  ok('live check returns a status', live === null || typeof live.status === 'string');
  if (live) results.push({ name: `live status = ${live.status}${live.reason ? ` (${live.reason})` : ''}`, ok: true });
  // the kill switch must not leak into the child: 'disabled' here would mean the hook is inert
  ok('kill switch does not silence the hook', !live || live.reason !== 'disabled');

  const failed = results.filter((r) => !r.ok);
  for (const r of results) process.stdout.write(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}\n`);
  process.stdout.write(failed.length === 0
    ? `SELFTEST PASS: ${results.length} checks\n`
    : `SELFTEST FAIL: ${failed.length} of ${results.length}\n`);
  process.exit(failed.length === 0 ? 0 : 1);
}

function main() {
  if (process.argv.includes('--self-test')) return selfTest();

  try {
    const result = check();
    if (result && result.status === 'update_available') {
      process.stdout.write(`${notice(result)}\n`);
    }
  } catch {
    // Deliberately swallowed. See the header: silence is the correct failure.
  }
  process.exit(0);
}

main();
