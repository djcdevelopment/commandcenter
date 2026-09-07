#!/usr/bin/env node
/**
 * sync-offload-block.mjs — keep one canonical HEARTH offload block in step across
 * every repository's `AGENTS.md`.
 *
 * Why this exists: Codex reads `AGENTS.md`, Claude reads `CLAUDE.md`. The offload
 * doctrine lived only in the two `CLAUDE.md` files, so every Codex session in the
 * other eight repos started blind to the door. Hand-pasting nine copies of a block
 * that changes whenever a rung moves drifts within a week, and a drifted copy is
 * worse than no copy — it sends an agent at a rung that is no longer there.
 *
 * So there is exactly one source of truth (`docs/agents/hearth-offload-block.md`),
 * one distribution list (`docs/agents/offload-block-targets.json`), and this tool,
 * which owns only the bytes between two markers and proves it did nothing else.
 *
 * Discipline inherited from check-nested-claude.mjs and rnd-mode.mjs next door:
 *   * the happy path writes nothing;
 *   * `--check` NEVER writes, so it is safe in CI and in a pre-commit hook;
 *   * `--write` touches only the marker span and never invokes a git write command —
 *     staging and committing stay a human decision, per repository.
 *
 * Usage:
 *   node sync-offload-block.mjs [--check | --write] [options]
 *
 * Options:
 *   --check            report drift; write nothing (default)
 *   --write            replace the marker span in every target
 *   --only <repo>      restrict to the target whose `repo` resolves to this path
 *   --manifest <path>  target list (default docs/agents/offload-block-targets.json)
 *   --block <path>     block source  (default docs/agents/hearth-offload-block.md)
 *   --force-dirty      write even to a target whose AGENTS.md is already modified
 *   --json             machine-readable report on stdout
 *   -h, --help         this text
 *
 * Exit: 0 every target in step, 1 drift / missing / malformed / refused,
 *       2 usage error or unreadable manifest or block.
 */

import { readFileSync, writeFileSync, existsSync, statSync } from 'node:fs';
import { join, resolve, dirname, basename, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

export const MARKER_BEGIN = '<!-- hearth-offload:begin -->';
export const MARKER_END = '<!-- hearth-offload:end -->';

/**
 * The complete set of git subcommands this tool is allowed to run. Both are reads.
 * `git()` refuses anything outside it, so "never stages, never commits" is a property
 * of the code rather than a promise in a comment.
 */
export const GIT_SUBCOMMANDS = Object.freeze(['status']);

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const DEFAULT_MANIFEST = join(ROOT, 'docs', 'agents', 'offload-block-targets.json');
const DEFAULT_BLOCK = join(ROOT, 'docs', 'agents', 'hearth-offload-block.md');

/** A target ends in exactly one of these. Anything but `unchanged` is drift. */
export const CLEAN_STATUS = 'unchanged';
const WRITE_OK_STATUSES = new Set(['unchanged', 'written', 'appended', 'created']);

class UsageError extends Error {}
class MarkersMalformedError extends Error {}

// ---------------------------------------------------------------------------
// pure helpers — exported so the tests can reach them without spawning a process
// ---------------------------------------------------------------------------

/**
 * Reduce a block source to its canonical form: no BOM, LF only, no trailing
 * whitespace on any line, no leading or trailing blank lines. Two sources that
 * differ only in those respects must produce byte-identical output, or a checkout
 * on a machine with a different `core.autocrlf` would read as drift forever.
 */
export function normalizeBlock(raw) {
  const text = String(raw).replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
  const lines = text.split('\n').map((line) => line.replace(/[ \t]+$/, ''));
  while (lines.length && lines[0] === '') lines.shift();
  while (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines.join('\n');
}

/**
 * The dominant line ending of an existing file. A target keeps whatever it already
 * uses. This is not hypothetical: measured 2026-09-07, seven of the eight existing
 * AGENTS.md files are LF and lumberjacks-platform/Lumberjacks is CRLF, so a tool that
 * picked one ending would rewrite every line of that ninth file and bury the one
 * section that actually moved. A file with no newline, and every new file, gets LF.
 */
export function detectEol(raw) {
  const crlf = (raw.match(/\r\n/g) || []).length;
  const lf = (raw.match(/\n/g) || []).length - crlf;
  return crlf > 0 && crlf >= lf ? '\r\n' : '\n';
}

/** Split into lines while keeping the byte offsets, so the span can be spliced exactly. */
function scanLines(raw) {
  const out = [];
  let i = 0;
  while (i <= raw.length) {
    const nl = raw.indexOf('\n', i);
    if (nl === -1) {
      if (i < raw.length) out.push({ start: i, textEnd: raw.length, end: raw.length, text: raw.slice(i) });
      break;
    }
    let textEnd = nl;
    if (textEnd > i && raw[textEnd - 1] === '\r') textEnd -= 1;
    out.push({ start: i, textEnd, end: nl + 1, text: raw.slice(i, textEnd) });
    i = nl + 1;
  }
  return out;
}

/**
 * Locate the marker span. `kind` is 'none' (no markers at all — append),
 * 'ok' (exactly one begin then one end), or 'malformed' (anything else: a lone
 * begin, a lone end, duplicates, or an end before its begin). Malformed is refused
 * rather than repaired: a half-marked file means someone edited it by hand mid-way,
 * and guessing where the span was meant to end could delete their prose.
 */
export function findMarkers(raw) {
  const lines = scanLines(raw);
  const begins = [];
  const ends = [];
  lines.forEach((line, index) => {
    const text = line.text.trim();
    if (text === MARKER_BEGIN) begins.push(index);
    if (text === MARKER_END) ends.push(index);
  });
  if (begins.length === 0 && ends.length === 0) return { kind: 'none', lines };
  if (begins.length !== 1 || ends.length !== 1 || begins[0] > ends[0]) {
    return { kind: 'malformed', lines, begins, ends };
  }
  return { kind: 'ok', lines, begin: begins[0], end: ends[0] };
}

/**
 * What an existing file should contain. Everything outside the marker span — the
 * begin marker line and everything before it, the end marker line and everything
 * after it — is carried through as the original bytes, line endings included.
 */
export function renderExisting(raw, block) {
  const markers = findMarkers(raw);
  if (markers.kind === 'malformed') throw new MarkersMalformedError('unbalanced hearth-offload markers');
  const eol = detectEol(raw);
  const body = block.split('\n').join(eol);

  if (markers.kind === 'ok') {
    const head = raw.slice(0, markers.lines[markers.begin].end);
    const tail = raw.slice(markers.lines[markers.end].start);
    return head + body + eol + tail;
  }

  let base = raw;
  if (base.length && !base.endsWith('\n')) base += eol;
  const gap = base.length ? eol : '';
  return `${base}${gap}${MARKER_BEGIN}${eol}${body}${eol}${MARKER_END}${eol}`;
}

/** What a target that has no file at all should contain. New files are LF. */
export function renderNew(block, name, eol = '\n') {
  const body = block.split('\n').join(eol);
  return `# AGENTS.md — ${name}${eol}${eol}${MARKER_BEGIN}${eol}${body}${eol}${MARKER_END}${eol}`;
}

// ---------------------------------------------------------------------------
// git — reads only
// ---------------------------------------------------------------------------

/**
 * The one gate every git invocation passes through. Exported so a test can prove the
 * refusal rather than read the allowlist and take its word for it.
 */
export function assertGitReadOnly(subcommand) {
  if (!GIT_SUBCOMMANDS.includes(subcommand)) {
    throw new Error(`sync-offload-block refuses to run "git ${subcommand}": reads only`);
  }
  return subcommand;
}

function git(cwd, args) {
  assertGitReadOnly(args[0]);
  const result = spawnSync('git', ['-C', cwd, ...args], { encoding: 'utf8' });
  if (result.error || result.status !== 0) return { ok: false, stdout: '' };
  return { ok: true, stdout: result.stdout };
}

/**
 * Is this file already modified by somebody else?
 *
 * `??` (untracked) is deliberately NOT dirty: a target we just created is untracked
 * until its repository owner commits it, and treating that as a foreign edit would
 * make the second `--write` refuse and break idempotency. Only a tracked file with
 * local modifications counts. A target outside any git repository is unknown, and
 * unknown proceeds — there is no second writer to protect it from.
 *
 * The pathspec is resolved from the file's own directory rather than from a computed
 * repository root, so a target that lives in a subdirectory of a larger repository —
 * lumberjacks-platform/Lumberjacks is one — is asked about correctly without this
 * tool needing to know it is not a repository of its own.
 */
export function gitDirty(filePath) {
  const status = git(dirname(filePath), ['status', '--porcelain', '--', basename(filePath)]);
  if (!status.ok) return { known: false, dirty: false, code: null };
  const line = status.stdout.split('\n').find((l) => l.trim().length > 0);
  if (!line) return { known: true, dirty: false, code: null };
  const code = line.slice(0, 2);
  return { known: true, dirty: code !== '??', code };
}

// ---------------------------------------------------------------------------
// unified diff (stdlib only)
// ---------------------------------------------------------------------------

function lcsTable(a, b) {
  const table = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

export function unifiedDiff(aText, bText, aLabel, bLabel, context = 3) {
  const a = aText.replace(/\r\n/g, '\n').split('\n');
  const b = bText.replace(/\r\n/g, '\n').split('\n');
  const table = lcsTable(a, b);

  const ops = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { ops.push([' ', a[i]]); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { ops.push(['-', a[i]]); i += 1; }
    else { ops.push(['+', b[j]]); j += 1; }
  }
  while (i < a.length) { ops.push(['-', a[i]]); i += 1; }
  while (j < b.length) { ops.push(['+', b[j]]); j += 1; }

  const interesting = ops.map((op) => op[0] !== ' ');
  if (!interesting.some(Boolean)) return '';

  const keep = ops.map((_, index) => interesting
    .slice(Math.max(0, index - context), index + context + 1)
    .some(Boolean));

  const out = [`--- ${aLabel}`, `+++ ${bLabel}`];
  let aLine = 1;
  let bLine = 1;
  let hunk = null;
  ops.forEach((op, index) => {
    const [kind, text] = op;
    if (keep[index]) {
      if (!hunk) hunk = { aStart: aLine, bStart: bLine, aCount: 0, bCount: 0, lines: [] };
      hunk.lines.push(kind + text);
      if (kind !== '+') hunk.aCount += 1;
      if (kind !== '-') hunk.bCount += 1;
    } else if (hunk) {
      out.push(`@@ -${hunk.aStart},${hunk.aCount} +${hunk.bStart},${hunk.bCount} @@`, ...hunk.lines);
      hunk = null;
    }
    if (kind !== '+') aLine += 1;
    if (kind !== '-') bLine += 1;
  });
  if (hunk) out.push(`@@ -${hunk.aStart},${hunk.aCount} +${hunk.bStart},${hunk.bCount} @@`, ...hunk.lines);
  return out.join('\n');
}

// ---------------------------------------------------------------------------
// manifest
// ---------------------------------------------------------------------------

export function parseManifest(text, label) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new UsageError(`${label}: not valid JSON (${err.message})`);
  }
  if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.targets)) {
    throw new UsageError(`${label}: expected an object with a "targets" array`);
  }
  return parsed.targets.map((entry, index) => {
    if (!entry || typeof entry !== 'object') throw new UsageError(`${label}: target ${index} is not an object`);
    if (typeof entry.repo !== 'string' || !entry.repo.trim()) throw new UsageError(`${label}: target ${index} has no "repo"`);
    if (typeof entry.file !== 'string' || !entry.file.trim()) throw new UsageError(`${label}: target ${index} has no "file"`);
    if (/[\\/]/.test(entry.file) || entry.file.includes('..')) {
      throw new UsageError(`${label}: target ${index} "file" must be a bare file name`);
    }
    const repo = resolve(entry.repo);
    return { repo, file: entry.file, name: typeof entry.name === 'string' && entry.name ? entry.name : repo.split(sep).pop() };
  });
}

// ---------------------------------------------------------------------------
// the work
// ---------------------------------------------------------------------------

function inspect(target, block) {
  const path = join(target.repo, target.file);
  const repoExists = existsSync(target.repo) && statSync(target.repo).isDirectory();
  if (!repoExists) {
    return { path, repoMissing: true, status: 'file-missing', raw: null, next: null, bytesBefore: 0, bytesAfter: 0 };
  }
  if (!existsSync(path)) {
    const next = renderNew(block, target.name);
    return { path, repoMissing: false, status: 'file-missing', raw: null, next, bytesBefore: 0, bytesAfter: Buffer.byteLength(next, 'utf8') };
  }
  const raw = readFileSync(path, 'utf8');
  const bytesBefore = Buffer.byteLength(raw, 'utf8');
  const markers = findMarkers(raw);
  if (markers.kind === 'malformed') {
    return { path, repoMissing: false, status: 'markers-malformed', raw, next: null, bytesBefore, bytesAfter: bytesBefore };
  }
  const next = renderExisting(raw, block);
  const status = markers.kind === 'none' ? 'markers-missing' : (next === raw ? CLEAN_STATUS : 'drift');
  return { path, repoMissing: false, status, raw, next, bytesBefore, bytesAfter: Buffer.byteLength(next, 'utf8') };
}

export function runCheck(targets, block) {
  return targets.map((target) => {
    const found = inspect(target, block);
    return {
      repo: target.repo,
      file: target.file,
      status: found.status,
      bytes_before: found.bytesBefore,
      bytes_after: found.bytesAfter,
      changed: found.status !== CLEAN_STATUS,
      _raw: found.raw,
      _next: found.next,
      _repoMissing: found.repoMissing,
      _path: found.path,
    };
  });
}

export function runWrite(targets, block, { forceDirty = false } = {}) {
  return targets.map((target) => {
    const found = inspect(target, block);
    const row = {
      repo: target.repo,
      file: target.file,
      status: found.status,
      bytes_before: found.bytesBefore,
      bytes_after: found.bytesAfter,
      changed: false,
      _path: found.path,
      _note: null,
    };

    if (found.repoMissing) {
      row.status = 'skipped-missing-repo';
      row.bytes_after = 0;
      row._note = `no such directory: ${target.repo}`;
      return row;
    }
    if (found.status === 'markers-malformed') {
      row._note = 'unbalanced hearth-offload markers; refusing to guess the span';
      return row;
    }
    if (found.raw !== null) {
      const dirt = gitDirty(found.path);
      if (dirt.dirty && !forceDirty) {
        row.status = 'skipped-dirty';
        row.bytes_after = found.bytesBefore;
        row._note = `already modified (git status "${dirt.code}"); rerun with --force-dirty to override`;
        return row;
      }
    }
    if (found.next === found.raw) {
      row.status = CLEAN_STATUS;
      return row;
    }

    writeFileSync(found.path, found.next, 'utf8');
    row.status = found.raw === null ? 'created' : (found.status === 'markers-missing' ? 'appended' : 'written');
    row.changed = true;
    return row;
  });
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

function usage() {
  process.stdout.write(
    'usage: node sync-offload-block.mjs [--check | --write] [--only <repo>] [--manifest <path>]\n' +
    '                                   [--block <path>] [--force-dirty] [--json]\n' +
    '  --check       report drift, write nothing (default)\n' +
    '  --write       replace the text between the hearth-offload markers\n' +
    '  --only        restrict to one target by its manifest "repo" path\n' +
    '  --force-dirty write even to an AGENTS.md another writer has modified\n' +
    'exit: 0 in step, 1 drift/missing/refused, 2 usage or manifest error\n'
  );
}

function parseArgs(argv) {
  const options = { mode: 'check', only: null, manifest: DEFAULT_MANIFEST, block: DEFAULT_BLOCK, json: false, forceDirty: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const value = () => {
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) throw new UsageError(`${arg} needs a value`);
      i += 1;
      return next;
    };
    if (arg === '--check') options.mode = 'check';
    else if (arg === '--write') options.mode = 'write';
    else if (arg === '--json') options.json = true;
    else if (arg === '--force-dirty') options.forceDirty = true;
    else if (arg === '-h' || arg === '--help') options.help = true;
    else if (arg === '--only') options.only = resolve(value());
    else if (arg === '--manifest') options.manifest = resolve(value());
    else if (arg === '--block') options.block = resolve(value());
    else throw new UsageError(`unknown argument: ${arg}`);
  }
  return options;
}

function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    usage();
    return 2;
  }
  if (options.help) { usage(); return 0; }

  let targets;
  let block;
  try {
    if (!existsSync(options.manifest)) throw new UsageError(`manifest not found: ${options.manifest}`);
    if (!existsSync(options.block)) throw new UsageError(`block source not found: ${options.block}`);
    targets = parseManifest(readFileSync(options.manifest, 'utf8'), options.manifest);
    block = normalizeBlock(readFileSync(options.block, 'utf8'));
    if (!block) throw new UsageError(`block source is empty: ${options.block}`);
    if (options.only) {
      targets = targets.filter((t) => t.repo === options.only);
      if (targets.length === 0) throw new UsageError(`--only ${options.only} matches no target in ${options.manifest}`);
    }
  } catch (err) {
    process.stderr.write(`${err.message}\n`);
    return 2;
  }

  const rows = options.mode === 'write'
    ? runWrite(targets, block, { forceDirty: options.forceDirty })
    : runCheck(targets, block);

  const report = {
    targets: rows.map((row) => ({
      repo: row.repo,
      file: row.file,
      status: row.status,
      bytes_before: row.bytes_before,
      bytes_after: row.bytes_after,
      changed: row.changed,
    })),
    drift_count: rows.filter((row) => row.status !== CLEAN_STATUS).length,
  };

  if (options.json) {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  } else {
    for (const row of rows) {
      process.stdout.write(`${row.status.padEnd(21)} ${row._path}${row._note ? `  — ${row._note}` : ''}\n`);
    }
    if (options.mode === 'check') {
      for (const row of rows) {
        if (row.status === 'drift' || row.status === 'markers-missing') {
          const diff = unifiedDiff(row._raw ?? '', row._next ?? '', row._path, `${row._path} (synced)`);
          if (diff) process.stdout.write(`\n${diff}\n\n`);
        }
      }
    }
    if (options.mode === 'write') {
      const changed = rows.filter((row) => row.changed).length;
      const refused = rows.filter((row) => !WRITE_OK_STATUSES.has(row.status)).length;
      process.stdout.write(`${changed} written, ${refused} refused, of ${rows.length} target(s)\n`);
    } else {
      process.stdout.write(`${report.drift_count} of ${rows.length} target(s) out of step\n`);
    }
  }

  if (options.mode === 'write') {
    return rows.every((row) => WRITE_OK_STATUSES.has(row.status)) ? 0 : 1;
  }
  return report.drift_count === 0 ? 0 : 1;
}

const invokedDirectly = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (invokedDirectly) process.exit(main(process.argv.slice(2)));

export { main };
