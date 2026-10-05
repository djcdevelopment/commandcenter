#!/usr/bin/env python3
"""Prepare ONE reviewable AM4 arm; never SSH, dispatch, install, or restart.

All candidate scripts are reconstructed from a hash-pinned original and an explicit
accepted control record. Outputs are new files only. The campaign owner performs
lease/thermal/tenancy checks and applies or restores the candidate separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

BASE = dict(graphs=False, reasoning_parser=None, tool_parser='step3p5', mtp=0,
            sequences=1, window=16384, kv_dtype='auto', utilization=0.93)
ARMS = {
    'A0': None, 'A1-graphs': ('graphs', True), 'A2r-reasoning': ('reasoning_parser', 'qwen3'),
    'A2t-tools': ('tool_parser', 'qwen3_xml'), 'A3-mtp2': ('mtp', 2),
    'A4-sequences2': ('sequences', 2), 'A5-fp8': ('kv_dtype', 'fp8'),
    **{f'W{n}': ('window', n) for n in (24576, 32768, 49152, 65536)},
    'A7-util90': ('utilization', 0.90), 'A7-util95': ('utilization', 0.95),
}
OPTIONS = {'reasoning_parser': '--reasoning-parser', 'tool_parser': '--tool-call-parser',
           'sequences': '--max-num-seqs', 'window': '--max-model-len',
           'kv_dtype': '--kv-cache-dtype', 'utilization': '--gpu-memory-utilization'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def original_parts(raw):
    text = raw.decode()
    prefix, separator, command = text.partition('exec ')
    if not separator or '\nexec ' in command:
        raise ValueError('expected exactly one final exec command')
    argv = shlex.split(command.replace('\\\n', ' '))
    expected = ['/home/derek/.venvs/vllm-cuda-030/bin/vllm', 'serve',
                '/home/derek/models/qwen3-27b-gptq-int4']
    if argv[:3] != expected:
        raise ValueError('original is not the known AM4 CUDA 27B launcher')
    for flag, value in [('--max-model-len', '16384'), ('--max-num-seqs', '1'),
                        ('--gpu-memory-utilization', '0.93'), ('--tool-call-parser', 'step3p5')]:
        if argv.count(flag) != 1 or argv[argv.index(flag) + 1] != value:
            raise ValueError(f'original baseline differs at {flag}')
    if '--enforce-eager' not in argv or any(f in argv for f in ('--reasoning-parser', '--speculative-config', '--kv-cache-dtype')):
        raise ValueError('original feature set differs from the frozen baseline')
    return prefix, argv


def candidate(raw, recipe):
    prefix, argv = original_parts(raw)
    def option(flag, value):
        if flag in argv:
            i = argv.index(flag)
            del argv[i:i + 2]
        if value is not None:
            argv.extend([flag, str(value)])
    if recipe['graphs']:
        argv.remove('--enforce-eager')
    for key, flag in OPTIONS.items():
        value = recipe[key]
        if key == 'kv_dtype' and value == 'auto':
            value = None
        option(flag, value)
    if recipe['mtp']:
        option('--speculative-config', json.dumps({'method': 'mtp', 'num_speculative_tokens': recipe['mtp']}, separators=(',', ':')))
    # No prefill/kernel/parser changes beyond the declared arm.
    return (prefix + 'exec ' + ' \\\n  '.join(shlex.quote(v) for v in argv) + '\n').encode(), argv


def fixed_workload(repo):
    commit = 'de666ef75ce79733bffb34c5e146c9b8ddd42f30'
    path = 'hearth/toolsurface/backends.py'
    source = subprocess.check_output(['git', '-C', str(repo), 'show', f'{commit}:{path}']).decode()
    lines = source[:6500].splitlines()
    values = {f'{n:02}': hashlib.sha256(f'bench27-am4-fixed-needle-{n}'.encode()).hexdigest()[:8] for n in range(1, 13)}
    planted = []
    for n, chunk in enumerate(range(0, len(lines), max(1, len(lines) // 12)), 1):
        planted.extend(lines[chunk:chunk + max(1, len(lines) // 12)])
        if n <= 12:
            planted.append(f'NEEDLE-{n:02}: {values[f"{n:02}"]}')
    user = 'SOURCE FILES (fixed baseline commit)\n' + '\n'.join(planted) + '\nFind all twelve NEEDLE-NN lines. Verify every value against the source, then list the indices and exact eight hexadecimal characters.'
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['needles'], 'properties': {
        'needles': {'type': 'object', 'additionalProperties': False, 'required': list(values),
                    'properties': {k: {'type': 'string', 'pattern': '^[0-9a-f]{8}$'} for k in values}}}}
    return {'schema': 'thinking-workload.v1', 'id': 'bench27-am4-fixed-small-needle-v1', 'backend': 'am4-vllm',
            'source_commit': commit, 'origin': path, 'aids': ['needle count stated'],
            'needle_truth': {k: {'value': v, 'byte_offset': len(user[:user.index(f"NEEDLE-{k}: {v}")].encode())} for k, v in values.items()},
            'work_request': {'model': 'am4-dense-27b', 'messages': [
                {'role': 'system', 'content': 'Read the source. Copy every planted needle exactly. Do not invent values.'},
                {'role': 'user', 'content': user}], 'max_tokens': 8000, 'temperature': 0.0, 'seed': 42,
                'top_p': 0.95, 'stream': False, 'chat_template_kwargs': {'enable_thinking': True}},
            'final_request_template': {'model': 'am4-dense-27b', 'max_tokens': 600, 'temperature': 0.0,
                'seed': 42, 'stream': False, 'chat_template_kwargs': {'enable_thinking': False},
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'needles_v1', 'schema': schema, 'strict': True}}},
            'final_user_message': {'role': 'user', 'content': 'Return only {"needles":{"01":"value",...}} with all twelve exact values.'},
            'conversation_rule': 'append only the new work response content as assistant, then final_user_message',
            'metrics_contract': {'work_budget': 8000, 'final_budget': 600, 'context': 16384,
                'work_thinking': True, 'final_thinking': False, 'score_work_and_final_separately': True,
                'truncation_is_completed_task': False},
            'preflight': 'Exact-tokenize both stages against actual AM4 tokenizer; bytes are not tokens. An 8000-token allowance is not evidence of 8000 reasoning tokens produced.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--original', type=Path, required=True)
    ap.add_argument('--original-sha256', required=True)
    ap.add_argument('--control-record', type=Path, help='accepted frontier/human verdict with original_sha256 and recipe')
    ap.add_argument('--arm', choices=ARMS, required=True)
    ap.add_argument('--repo', type=Path, required=True, help='local flash checkout containing frozen source commit')
    ap.add_argument('--out', type=Path, required=True, help='new review directory, never an immutable run directory')
    a = ap.parse_args()
    raw = a.original.read_bytes()
    if digest(raw) != a.original_sha256:
        raise ValueError('original SHA256 mismatch')
    control = dict(BASE)
    control_record_sha256 = None
    adopted_candidate_sha256 = None
    if a.control_record:
        control_record_bytes = a.control_record.read_bytes()
        control_record_sha256 = digest(control_record_bytes)
        verdict = json.loads(control_record_bytes)
        adopted_candidate_sha256 = verdict.get('candidate_sha256')
        if verdict.get('verdict') != 'accepted' or verdict.get('reviewer_class') not in ('frontier', 'human') or verdict.get('original_sha256') != digest(raw):
            raise ValueError('control must have a frontier/human accepted verdict on this original')
        control = verdict['recipe']
        if set(control) != set(BASE):
            raise ValueError('control recipe keys differ')
        for key, value in control.items():
            allowed = [BASE[key], *(change[1] for change in ARMS.values() if change and change[0] == key)]
            if value not in allowed:
                raise ValueError(f'unknown adopted value for {key}')
    control_sha256 = digest(raw) if control == BASE else digest(candidate(raw, control)[0])
    if a.control_record and adopted_candidate_sha256 != control_sha256:
        raise ValueError('adopted candidate SHA256 does not match reconstructed control')
    recipe = dict(control)
    change = ARMS[a.arm]
    if change:
        key, value = change
        if control[key] == value:
            raise ValueError('arm is already the control; no lever changes')
        recipe[key] = value
    elif control != BASE:
        raise ValueError('A0 must use the original baseline')
    if a.arm == 'A5-fp8' and recipe['window'] != 16384:
        raise ValueError('fp8 feature arm must run at unchanged 16384 window before window sweep')
    data, argv = candidate(raw, recipe)
    if a.arm == 'A0':
        data = raw  # baseline script is byte-identical, not just argv-equivalent
        argv = original_parts(raw)[1]
    workload = fixed_workload(a.repo)
    work_bytes = (json.dumps(workload, indent=2) + '\n').encode()
    manifest = {'schema': 'bench27-am4-prepared-arm.v1', 'status': 'prepared_for_review', 'arm': a.arm,
                'backend': 'am4-vllm', 'caller': 'codex', 'original_sha256': digest(raw),
                'control_record': str(a.control_record) if a.control_record else None, 'control_recipe': control,
                'control_sha256': control_sha256, 'control_record_sha256': control_record_sha256,
                'recipe': recipe, 'changed_levers': [] if change is None else [change[0]],
                'candidate_sha256': digest(data), 'argv': argv, 'workload_sha256': digest(work_bytes),
                'metrics_url': 'ssh+http://10.44.0.2:18094/metrics',
                'rung_proposal': {'context_tokens': recipe['window'], 'parallel_slots': 1, 'max_tokens': 4096,
                                 'deliberate_max_tokens': 'absent until thinking evidence passes; then only measured safe allowance'},
                'requirements': ['guard heartbeat <=40s and no sticky trip', 'zero affected leases, running and waiting requests',
                    'sole owner before script swap/restart', 'dated remote original backup with matching SHA256',
                    'owned direct cancellation smoke before baseline/direct probes', 'no declarations of unmeasured capability',
                    'on failed load: preserve failure, drain, restore hash-verified pre-arm script; retain hold on failed restoration']}
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / 'candidate.sh').write_bytes(data)
    (a.out / 'fixed-workload.json').write_bytes(work_bytes)
    (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'out': str(a.out), 'candidate_sha256': digest(data), 'workload_sha256': digest(work_bytes)}))


if __name__ == '__main__':
    main()
