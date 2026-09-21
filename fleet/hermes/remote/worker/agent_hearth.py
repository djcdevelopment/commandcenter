"""HEARTH transport adapter for the existing JSON-action worker loop."""
import json
import sys
from hearth_client import HearthClient
from runner_presets import BASE_URL, MODEL, unwrap
import agent_openai as runner

TASK_ID = ''


# Whole-file JSON actions exceeded 2048 tokens in the recorded cycle-13 failure.
# The gateway still enforces its 4096-output and native-context admission limits.
def chat(base_url, model, token, messages, max_tokens=4096, timeout=120):
    if base_url != BASE_URL or model != MODEL:
        raise ValueError('OMEN-only transport')
    system = '\n'.join(m['content'] for m in messages if m['role']=='system')
    turns = [m for m in messages if m['role']!='system']
    prompt = ('Conversation history in chronological order. Continue with the next JSON action.\n'
              + json.dumps(turns,ensure_ascii=False) + '\nNext action:')
    result = unwrap(HearthClient(base_url,token,TASK_ID).call_sync('local_generate',
        prompt=prompt, system=system, backend='omen-arc', model=MODEL,
        task_id=TASK_ID, max_tokens=max_tokens, timeout_s=max(1,min(120,int(timeout)))))
    if not result.get('ok') or result.get('backend') != 'omen-arc':
        raise RuntimeError('OMEN generation failed: '+str(result.get('error','route mismatch'))[:250])
    attempt = {k:result.get(k) for k in ('backend','model','tokens_in','tokens_out','duration_ms')}
    attempt.update(task_id=TASK_ID, job_id=(result.get('execution') or {}).get('job_id'),
                   requested_max_tokens=max_tokens)
    print('[hearth_attempt] '+json.dumps(attempt),flush=True)
    return result.get('text') or ''


if __name__ == '__main__':
    TASK_ID = sys.argv[sys.argv.index('--task-id')+1]
    runner.chat = chat
    raise SystemExit(runner.main())
