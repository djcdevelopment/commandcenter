"""Private-key HTTP client; persist worst-case spend before sending anything."""
import json
import os
from pathlib import Path
import stat
import time
import urllib.error
import urllib.request

from fleet.jev import policy


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with open(temporary, 'w', encoding='utf-8') as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, indent=2, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def read_key():
    filename = Path(os.environ.get('TYPESAFE_KEY_FILE', '/home/derek/.config/fleet-scheduler/typesafe.key'))
    if filename.is_symlink():
        raise RuntimeError('credential_symlink_refused')
    if os.name != 'nt' and (filename.stat().st_mode & 0o077 or filename.stat().st_uid != os.getuid()):
        raise RuntimeError('credential_must_be_private_and_owned')
    value = filename.read_text().strip()
    if not value or len(value) > 512 or any(c.isspace() for c in value):
        raise RuntimeError('credential_unavailable')
    return value


def evaluate(state, home):
    payload = policy.request_for(state)
    ledger = Path(home) / 'api-budget.json'
    budget = json.loads(ledger.read_text()) if ledger.exists() else {'calls': 0, 'charged_usd': 0, 'input_tokens': 0}
    reserve = policy.MAX_REQUEST_TOKENS * policy.INPUT_USD_PER_TOKEN
    started = time.monotonic()
    for attempt in range(2):
        key = read_key()  # Re-read for rotation; never record it.
        if budget['calls'] >= policy.MAX_CALLS or budget['charged_usd'] + reserve > policy.MAX_COST_USD:
            raise RuntimeError('jev_budget_exhausted')
        budget['calls'] += 1
        budget['charged_usd'] += reserve
        atomic_json(ledger, budget)
        request = urllib.request.Request(policy.ENDPOINT, data=json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=10) as result:
                raw = result.read(262145)
            if len(raw) > 262144:
                raise ValueError('oversized_jev_response')
            answer = json.loads(raw)
            usage = answer.get('usage', {})
            tokens = usage.get('input_tokens')
            if type(tokens) is not int or not 0 <= tokens <= policy.MAX_REQUEST_TOKENS:
                raise ValueError('missing_jev_usage')
            budget['charged_usd'] += tokens * policy.INPUT_USD_PER_TOKEN - reserve
            budget['input_tokens'] += tokens
            atomic_json(ledger, budget)
            return answer, {'provider': 'typesafe', 'model': answer.get('model'),
                'input_tokens': tokens, 'output_tokens': usage.get('output_tokens'),
                'estimated_usd': tokens * policy.INPUT_USD_PER_TOKEN,
                'elapsed_s': round(time.monotonic() - started, 3),
                'request_sha256': policy.digest(payload)}
        except urllib.error.HTTPError as error:
            # Never log response bodies or request headers. Keep uncertain charges reserved.
            if attempt == 0 and error.code in (429, 529):
                delay = error.headers.get('Retry-After', '1')
                if delay.isdigit() and int(delay) <= 3:
                    time.sleep(max(1, int(delay)))
                    continue
            raise RuntimeError('jev_http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError('jev_transport_unavailable') from None
    raise RuntimeError('jev_unavailable')
