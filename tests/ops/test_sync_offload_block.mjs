/**
 * test_sync_offload_block.mjs — proof for tools/ops/sync-offload-block.mjs.
 *
 * Run: node --test tests/ops/
 *
 * Every fixture lives in a fresh directory under the OS temp dir and every case
 * passes an explicit --manifest. Nothing here may reach the real target
 * repositories: a test that accidentally pointed at the default manifest and ran
 * --write would edit eight repositories that this Work Item is forbidden to touch.
 * The last case in this file asserts that as a property of the suite itself.
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, statSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';

import {
  MARKER_BEGIN,
  MARKER_END,
  GIT_SUBCOMMANDS,
  CLEAN_STATUS,
  assertGitReadOnly,
  normalizeBlock,
  detectEol,
  findMarkers,
  renderExisting,
  renderNew,
  parseManifest,
  runCheck,
  runWrite,
} from '../../tools/ops/sync-offload-block.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..');
const TOOL = join(REPO_ROOT, 'tools', 'ops', 'sync-offload-block.mjs');
const BLOCK_SOURCE = join(REPO_ROOT, 'docs', 'agents', 'hearth-offload-block.md');
const REAL_MANIFEST = join(REPO_ROOT, 'docs', 'agents', 'offload-block-targets.json');

const BLOCK = normalizeBlock(readFileSync(BLOCK_SOURCE, 'utf8'));

const scratch = [];
function sandbox(label) {
  const dir = mkdtempSync(join(tmpdir(), `sync-offload-${label}-`));
  scratch.push(dir);
  return dir;
}

process.on('exit', () => {
  for (const dir of scratch) {
    try { rmSync(dir, { recursive: true, force: true }); } catch { /* best effort */ }
  }
});

/** Build a target directory + manifest. Returns { dir, repo, file, manifest }. */
function fixture(label, content, { name = 'fixture', fileName = 'AGENTS.md' } = {}) {
  const dir = sandbox(label);
  const repo = join(dir, 'repo');
  mkdirSync(repo, { recursive: true });
  const file = join(repo, fileName);
  if (content !== null) writeFileSync(file, content, 'utf8');
  const manifest = join(dir, 'targets.json');
  writeFileSync(manifest, JSON.stringify({ targets: [{ repo, file: fileName, name }] }, null, 2), 'utf8');
  return { dir, repo, file, manifest };
}

function cli(args, options = {}) {
  return spawnSync(process.execPath, [TOOL, ...args], { encoding: 'utf8', ...options });
}

function sha(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

function git(cwd, args) {
  return spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8' });
}

const CRLF_PROLOGUE = '# Fixture AGENTS.md\r\n\r\nHouse rules that must survive untouched.\r\n\r\n';
const CRLF_EPILOGUE = '\r\n## Afterword\r\n\r\nAlso must survive untouched.\r\n';

// ---------------------------------------------------------------------------
// the canonical block itself — it lands in public repositories
// ---------------------------------------------------------------------------

test('canonical block carries none of the forbidden public patterns', () => {
  const raw = readFileSync(BLOCK_SOURCE, 'utf8');

  assert.equal(/[A-Za-z]:\\/.test(raw), false, 'block must not contain a backslash drive-letter path');
  assert.equal(/(?<![A-Za-z])[A-Za-z]:\//.test(raw), false, 'block must not contain a forward-slash drive-letter path');
  assert.equal(/\\Users\\/i.test(raw), false, 'block must not contain a Windows user path');
  assert.equal(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i.test(raw), false, 'no UUIDs');
  assert.equal(/\b[^\s@]+@[^\s@]+\.[^\s@]+\b/.test(raw), false, 'no email addresses');
  assert.equal(/hearth[\\/]var/i.test(raw), false, 'no ledger paths');
  assert.equal(/\bOMEN\b/.test(raw), false, 'no machine hostname — the door is reached on loopback');

  // Loopback is the one address allowed to appear.
  const addresses = raw.match(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g) || [];
  assert.ok(addresses.length > 0, 'the loopback door address should be stated');
  for (const address of addresses) assert.equal(address, '127.0.0.1', `unexpected address ${address}`);

  // Only the ports already public in CLAUDE.md.
  const allowedPorts = new Set(['8710', '8082', '8081']);
  for (const port of raw.match(/\d{4,5}/g) || []) {
    assert.ok(allowedPorts.has(port), `unexpected 4-5 digit number in the block: ${port}`);
  }
});

test('canonical block states the doctrine it exists to carry', () => {
  for (const needle of [
    'http://127.0.0.1:8710/mcp',
    'local_generate',
    'gcp-gemini',
    'gcp-gemini-pro',
    'omen-arc',
    'omen-arc-oss',
    'omen-swap',
    'files=',
    'routed_by',
    'ok:false',
    '/checkmcp',
    'submit_task',
    'task_status',
    'llama-swap unload',
    'HEARTH scope root',
  ]) {
    assert.ok(BLOCK.includes(needle), `canonical block is missing "${needle}"`);
  }
  assert.ok(
    BLOCK.includes('<!-- synced by tools/ops/sync-offload-block.mjs from docs/agents/hearth-offload-block.md; edit the source, not this copy -->'),
    'canonical block is missing its provenance footer',
  );
  // task_family routing is live on the door since C-05 (promoted 2026-09-06, mounted at the
  // 2026-09-07 restart): the block must name it as usable, not hedge it as pending.
  assert.ok(/task_family=/.test(BLOCK), 'the block must show how to pass task_family=');
  assert.equal(/after C-05 lands/.test(BLOCK), false, 'the pre-C-05 hedge must be gone');
});

test('real manifest lists the nine targets, commandcenter first', () => {
  const targets = parseManifest(readFileSync(REAL_MANIFEST, 'utf8'), REAL_MANIFEST);
  assert.equal(targets.length, 9);
  assert.match(targets[0].repo, /commandcenter$/);
  for (const target of targets) assert.equal(target.file, 'AGENTS.md');
  assert.equal(new Set(targets.map((t) => t.repo)).size, 9, 'no duplicate targets');
});

// ---------------------------------------------------------------------------
// normalization / determinism
// ---------------------------------------------------------------------------

test('block sources differing only in whitespace, BOM or line endings normalize identically', () => {
  const clean = 'alpha\nbeta\n\ngamma';
  const noisy = `\uFEFF\n\nalpha   \r\nbeta\t\r\n\r\ngamma  \r\n\r\n\r\n`;
  assert.equal(normalizeBlock(noisy), clean);
  assert.equal(normalizeBlock(clean), clean);
  assert.equal(normalizeBlock(normalizeBlock(noisy)), normalizeBlock(noisy), 'normalization is idempotent');
  // and the real source is already canonical, so the tool never rewrites its own input
  assert.equal(normalizeBlock(readFileSync(BLOCK_SOURCE, 'utf8')), BLOCK);
});

test('detectEol reports the dominant ending and defaults to LF', () => {
  assert.equal(detectEol('a\r\nb\r\n'), '\r\n');
  assert.equal(detectEol('a\nb\n'), '\n');
  assert.equal(detectEol('no newline at all'), '\n');
  assert.equal(detectEol('a\r\nb\nc\nd\n'), '\n', 'LF wins when it is the majority');
});

test('markers must own their line', () => {
  assert.equal(findMarkers('nothing here\n').kind, 'none');
  assert.equal(findMarkers(`x\n${MARKER_BEGIN}\ny\n${MARKER_END}\nz\n`).kind, 'ok');
  assert.equal(findMarkers(`  ${MARKER_BEGIN}  \nbody\n\t${MARKER_END}\n`).kind, 'ok', 'surrounding whitespace is tolerated');
  assert.equal(findMarkers(`prose ${MARKER_BEGIN} prose\n`).kind, 'none', 'a marker with text beside it is not a marker');
});

// ---------------------------------------------------------------------------
// check mode
// ---------------------------------------------------------------------------

test('--check reports markers-missing and exits 1, writing nothing', () => {
  const fx = fixture('missing', CRLF_PROLOGUE);
  const before = readFileSync(fx.file);
  const result = cli(['--check', '--manifest', fx.manifest]);
  assert.equal(result.status, 1);
  assert.match(result.stdout, /markers-missing/);
  assert.match(result.stdout, /^--- /m, 'a unified diff is printed for a drifting target');
  assert.deepEqual(readFileSync(fx.file), before, '--check must not write');
});

test('--check exits 0 once the target is in step', () => {
  const fx = fixture('instep', `${CRLF_PROLOGUE}${MARKER_BEGIN}\r\n${BLOCK.split('\n').join('\r\n')}\r\n${MARKER_END}\r\n`);
  const result = cli(['--check', '--manifest', fx.manifest]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /unchanged/);
  assert.match(result.stdout, /0 of 1 target\(s\) out of step/);
});

test('--check exits 2 on usage and manifest errors', () => {
  const fx = fixture('usage', 'x\n');
  assert.equal(cli(['--nonsense', '--manifest', fx.manifest]).status, 2);
  assert.equal(cli(['--check', '--manifest', join(fx.dir, 'no-such-manifest.json')]).status, 2);
  assert.equal(cli(['--check', '--block', join(fx.dir, 'no-such-block.md'), '--manifest', fx.manifest]).status, 2);
  assert.equal(cli(['--check', '--manifest', fx.manifest, '--only', join(fx.dir, 'not-a-target')]).status, 2);

  const bad = join(fx.dir, 'bad.json');
  writeFileSync(bad, '{ not json', 'utf8');
  assert.equal(cli(['--check', '--manifest', bad]).status, 2);

  const shapeless = join(fx.dir, 'shapeless.json');
  writeFileSync(shapeless, JSON.stringify({ targets: [{ repo: fx.repo }] }), 'utf8');
  assert.equal(cli(['--check', '--manifest', shapeless]).status, 2);
});

test('--json emits exactly the agreed report shape', () => {
  const fx = fixture('json', CRLF_PROLOGUE);
  const result = cli(['--check', '--json', '--manifest', fx.manifest]);
  assert.equal(result.status, 1);
  const report = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(report).sort(), ['drift_count', 'targets']);
  assert.equal(report.drift_count, 1);
  assert.equal(report.targets.length, 1);
  assert.deepEqual(
    Object.keys(report.targets[0]).sort(),
    ['bytes_after', 'bytes_before', 'changed', 'file', 'repo', 'status'],
  );
  assert.equal(report.targets[0].status, 'markers-missing');
  assert.equal(report.targets[0].changed, true);
  assert.equal(report.targets[0].bytes_before, Buffer.byteLength(CRLF_PROLOGUE, 'utf8'));
  assert.ok(report.targets[0].bytes_after > report.targets[0].bytes_before);
});

// ---------------------------------------------------------------------------
// write mode
// ---------------------------------------------------------------------------

test('--write appends markers when they are missing, then is idempotent', () => {
  const fx = fixture('append', CRLF_PROLOGUE);

  const first = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(first.status, 0, first.stdout + first.stderr);
  assert.match(first.stdout, /appended/);

  const after = readFileSync(fx.file, 'utf8');
  assert.ok(after.startsWith(CRLF_PROLOGUE), 'existing bytes are preserved verbatim');
  assert.equal(after.slice(0, CRLF_PROLOGUE.length), CRLF_PROLOGUE);
  assert.equal(findMarkers(after).kind, 'ok');
  assert.equal(after.endsWith(`${MARKER_END}\r\n`), true);

  const digest = sha(after);
  const mtime = statSync(fx.file).mtimeMs;

  const second = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(second.status, 0);
  assert.match(second.stdout, new RegExp(CLEAN_STATUS));
  assert.equal(sha(readFileSync(fx.file, 'utf8')), digest, 'a second --write changes zero bytes');
  assert.equal(statSync(fx.file).mtimeMs, mtime, 'a second --write performs no filesystem write at all');

  assert.equal(cli(['--check', '--manifest', fx.manifest]).status, 0, '--check after --write exits 0');
});

test('--write replaces only the marker span and preserves CRLF inside and out', () => {
  const stale = `${CRLF_PROLOGUE}${MARKER_BEGIN}\r\nSTALE LINE ONE\r\nSTALE LINE TWO\r\n${MARKER_END}${CRLF_EPILOGUE}`;
  const fx = fixture('span', stale);

  const result = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /written/);

  const after = readFileSync(fx.file, 'utf8');
  const beginAt = after.indexOf(MARKER_BEGIN);
  const endAt = after.indexOf(MARKER_END);

  assert.equal(after.slice(0, beginAt), stale.slice(0, stale.indexOf(MARKER_BEGIN)), 'prologue byte-identical');
  assert.equal(after.slice(endAt), CRLF_EPILOGUE.length ? MARKER_END + CRLF_EPILOGUE : MARKER_END, 'epilogue byte-identical');

  const span = after.slice(beginAt + MARKER_BEGIN.length + 2, endAt);
  assert.equal(span, `${BLOCK.split('\n').join('\r\n')}\r\n`, 'span is the canonical block in the file\'s own line ending');
  assert.equal(after.includes('STALE LINE'), false);
  assert.equal(/(?<!\r)\n/.test(after), false, 'not one bare LF anywhere in a CRLF file');
});

test('--write preserves LF in an LF file', () => {
  const stale = `# LF fixture\n\n${MARKER_BEGIN}\nold\n${MARKER_END}\ntail\n`;
  const fx = fixture('lf', stale);
  assert.equal(cli(['--write', '--manifest', fx.manifest]).status, 0);
  const after = readFileSync(fx.file, 'utf8');
  assert.equal(after.includes('\r'), false, 'no CR introduced into an LF file');
  assert.equal(after, `# LF fixture\n\n${MARKER_BEGIN}\n${BLOCK}\n${MARKER_END}\ntail\n`);
});

test('--write creates a missing file with a heading, and only that file', () => {
  const fx = fixture('create', null, { name: 'commandcenter' });
  assert.equal(existsSync(fx.file), false);

  const result = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /created/);

  const after = readFileSync(fx.file, 'utf8');
  assert.equal(after.split('\n')[0], '# AGENTS.md — commandcenter');
  assert.equal(after.includes('\r'), false, 'new files are LF');
  assert.equal(after, renderNew(BLOCK, 'commandcenter'));
  assert.equal(findMarkers(after).kind, 'ok');

  const digest = sha(after);
  assert.equal(cli(['--write', '--manifest', fx.manifest]).status, 0);
  assert.equal(sha(readFileSync(fx.file, 'utf8')), digest, 'creation is idempotent too');
});

test('--only restricts the run to a single target', () => {
  const dir = sandbox('only');
  const repoA = join(dir, 'a');
  const repoB = join(dir, 'b');
  mkdirSync(repoA, { recursive: true });
  mkdirSync(repoB, { recursive: true });
  writeFileSync(join(repoA, 'AGENTS.md'), '# a\n', 'utf8');
  writeFileSync(join(repoB, 'AGENTS.md'), '# b\n', 'utf8');
  const manifest = join(dir, 'targets.json');
  writeFileSync(manifest, JSON.stringify({
    targets: [{ repo: repoA, file: 'AGENTS.md' }, { repo: repoB, file: 'AGENTS.md' }],
  }), 'utf8');

  const before = readFileSync(join(repoB, 'AGENTS.md'), 'utf8');
  const result = cli(['--write', '--only', repoA, '--manifest', manifest]);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.equal(findMarkers(readFileSync(join(repoA, 'AGENTS.md'), 'utf8')).kind, 'ok');
  assert.equal(readFileSync(join(repoB, 'AGENTS.md'), 'utf8'), before, 'the untargeted repo is untouched');
});

// ---------------------------------------------------------------------------
// refusals and fault injection
// ---------------------------------------------------------------------------

test('--write refuses a target another writer has already modified', () => {
  const fx = fixture('dirty', `${CRLF_PROLOGUE}${MARKER_BEGIN}\r\nold\r\n${MARKER_END}\r\n`);
  const init = git(fx.repo, ['init']);
  assert.equal(init.status, 0, init.stderr);

  // Untracked is NOT another writer's edit — the tool must proceed.
  const untracked = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(untracked.status, 0, untracked.stdout + untracked.stderr);
  assert.match(untracked.stdout, /written/);

  // Staged is a tracked local change — refuse it.
  assert.equal(git(fx.repo, ['add', 'AGENTS.md']).status, 0);
  writeFileSync(fx.file, `${CRLF_PROLOGUE}${MARKER_BEGIN}\r\nhand edit\r\n${MARKER_END}\r\n`, 'utf8');
  const guarded = readFileSync(fx.file, 'utf8');

  const refused = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(refused.status, 1);
  assert.match(refused.stdout, /skipped-dirty/);
  assert.match(refused.stdout, /--force-dirty/);
  assert.equal(readFileSync(fx.file, 'utf8'), guarded, 'a refused target is not written');

  const forced = cli(['--write', '--force-dirty', '--manifest', fx.manifest]);
  assert.equal(forced.status, 0, forced.stdout + forced.stderr);
  assert.notEqual(readFileSync(fx.file, 'utf8'), guarded, '--force-dirty overrides the refusal');
});

test('a target with one marker is refused, not repaired', () => {
  const lonely = `${CRLF_PROLOGUE}${MARKER_BEGIN}\r\nhalf written\r\n`;
  const fx = fixture('malformed', lonely);

  const checked = cli(['--check', '--json', '--manifest', fx.manifest]);
  assert.equal(checked.status, 1);
  assert.equal(JSON.parse(checked.stdout).targets[0].status, 'markers-malformed');

  const written = cli(['--write', '--manifest', fx.manifest]);
  assert.equal(written.status, 1);
  assert.match(written.stdout, /markers-malformed/);
  assert.equal(readFileSync(fx.file, 'utf8'), lonely, 'a malformed target is left exactly as found');

  // the mirror cases
  assert.equal(findMarkers(`${MARKER_END}\nbody\n`).kind, 'malformed', 'a lone end marker');
  assert.equal(findMarkers(`${MARKER_BEGIN}\na\n${MARKER_BEGIN}\nb\n${MARKER_END}\n`).kind, 'malformed', 'duplicate begins');
  assert.equal(findMarkers(`${MARKER_END}\na\n${MARKER_BEGIN}\n`).kind, 'malformed', 'end before begin');
  assert.throws(() => renderExisting(lonely, BLOCK), /markers/);
});

test('a manifest entry pointing at a non-existent repo is reported, not fatal', () => {
  const dir = sandbox('ghost');
  const ghost = join(dir, 'no-such-repo');
  const real = join(dir, 'real');
  mkdirSync(real, { recursive: true });
  writeFileSync(join(real, 'AGENTS.md'), '# real\n', 'utf8');
  const manifest = join(dir, 'targets.json');
  writeFileSync(manifest, JSON.stringify({
    targets: [{ repo: ghost, file: 'AGENTS.md' }, { repo: real, file: 'AGENTS.md' }],
  }), 'utf8');

  const checked = cli(['--check', '--json', '--manifest', manifest]);
  assert.equal(checked.status, 1);
  const report = JSON.parse(checked.stdout);
  assert.equal(report.targets[0].status, 'file-missing');
  assert.equal(report.drift_count, 2);

  const written = cli(['--write', '--manifest', manifest]);
  assert.equal(written.status, 1, 'a skipped target keeps the exit code non-zero');
  assert.match(written.stdout, /skipped-missing-repo/);
  assert.match(written.stdout, /no such directory/);
  assert.equal(existsSync(ghost), false, 'the tool did not conjure the missing repo');
  assert.equal(findMarkers(readFileSync(join(real, 'AGENTS.md'), 'utf8')).kind, 'ok', 'the reachable target still synced');
});

test('a missing file inside an existing repo is created, not reported as a missing repo', () => {
  const fx = fixture('absent', null);
  const report = JSON.parse(cli(['--check', '--json', '--manifest', fx.manifest]).stdout);
  assert.equal(report.targets[0].status, 'file-missing');
  assert.equal(report.targets[0].bytes_before, 0);
  assert.ok(report.targets[0].bytes_after > 0, 'check knows what it would create');
  assert.equal(existsSync(fx.file), false, '--check still wrote nothing');
});

// ---------------------------------------------------------------------------
// invariants
// ---------------------------------------------------------------------------

test('the tool can only ever run read-only git subcommands', () => {
  assert.deepEqual([...GIT_SUBCOMMANDS], ['status']);
  assert.equal(assertGitReadOnly('status'), 'status');
  for (const forbidden of ['add', 'commit', 'push', 'checkout', 'reset', 'clean', 'stash', 'rm', 'mv']) {
    assert.throws(() => assertGitReadOnly(forbidden), /reads only/, `git ${forbidden} must be refused`);
  }
});

test('--check leaves every byte of every target alone', () => {
  const dir = sandbox('readonly');
  const repos = ['one', 'two', 'three'].map((n) => {
    const repo = join(dir, n);
    mkdirSync(repo, { recursive: true });
    const file = join(repo, 'AGENTS.md');
    writeFileSync(file, `# ${n}\r\n\r\nbody\r\n`, 'utf8');
    return { repo, file };
  });
  const manifest = join(dir, 'targets.json');
  writeFileSync(manifest, JSON.stringify({ targets: repos.map((r) => ({ repo: r.repo, file: 'AGENTS.md' })) }), 'utf8');

  const before = repos.map((r) => sha(readFileSync(r.file, 'utf8')));
  assert.equal(cli(['--check', '--manifest', manifest]).status, 1);
  assert.equal(cli(['--check', '--json', '--manifest', manifest]).status, 1);
  const after = repos.map((r) => sha(readFileSync(r.file, 'utf8')));
  assert.deepEqual(after, before);
});

test('runCheck and runWrite agree on a target already in step', () => {
  const fx = fixture('agree', `head\n${MARKER_BEGIN}\n${BLOCK}\n${MARKER_END}\ntail\n`);
  const targets = parseManifest(readFileSync(fx.manifest, 'utf8'), fx.manifest);
  assert.equal(runCheck(targets, BLOCK)[0].status, CLEAN_STATUS);
  const written = runWrite(targets, BLOCK);
  assert.equal(written[0].status, CLEAN_STATUS);
  assert.equal(written[0].changed, false);
});

test('the suite never points a write at the real manifest', () => {
  const source = readFileSync(fileURLToPath(import.meta.url), 'utf8');
  const writeCalls = source.match(/cli\(\[[^\]]*'--write'[^\]]*\]/g) || [];
  assert.ok(writeCalls.length >= 6, 'expected the write cases to be present');
  for (const call of writeCalls) {
    assert.ok(call.includes("'--manifest'"), `a --write case without an explicit --manifest: ${call}`);
  }
  assert.equal(
    /runWrite\(\s*parseManifest\(\s*readFileSync\(REAL_MANIFEST/.test(source),
    false,
    'never drive runWrite from the real manifest',
  );
});
