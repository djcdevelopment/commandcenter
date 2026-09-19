"""Only configuration and exec: the agent loop remains unmodified pinned Hermes."""
from pathlib import Path
import os
import sys

ROOT = Path('/home/derek/.local/share/hermes-fleet')
PROFILE = Path('/home/derek/.config/hermes-fleet')
BASE = 'http://192.168.12.233:8090/v1'
MODEL = 'am4-dense-27b'


def configuration():
    provider = {'provider':'custom','model':MODEL,'base_url':BASE,
                'api_key':'${HERMES_AM4_KEY}','timeout':600,
                'extra_body':{'reasoning_effort':'none'}}
    return {
        'model': {'provider':'custom','default':MODEL,'base_url':BASE,
                  'api_key':'${HERMES_AM4_KEY}','context_length':131072},
        'providers': {'custom': {**provider,'request_timeout_seconds':600}},
        'compression': {'enabled':True,'threshold':0.85,'context_timeout_seconds':600,
                        'context_total_ceiling_seconds':600},
        'auxiliary': {'compression': {**provider,'max_concurrency':1},
                      'title_generation':{'enabled':False},'background_review':{'enabled':False}},
        'mcp_servers': {'hearth': {'url':'http://127.0.0.1:8712/mcp',
                                 'headers':{'X-Hearth-Key':'${HERMES_HEARTH_KEY}'}}},
        'platform_toolsets': {'cli':['hearth']},
        'display': {'interface':'cli'},
    }


def main():
    os.environ['HERMES_HOME'] = str(PROFILE / 'sessions')
    for env, name in [('HERMES_AM4_KEY','am4.key'),('HERMES_HEARTH_KEY','hearth.key')]:
        os.environ[env] = (PROFILE/name).read_text().strip()
    # OpenAI custom provider has no fallback provider or external API key.
    os.environ['OPENAI_API_KEY'] = os.environ['HERMES_AM4_KEY']
    os.environ['OPENAI_BASE_URL'] = BASE
    from hermes_cli.config import save_config
    conf = configuration()
    # Provider credentials are resolved here; MCP's loader resolves env references.
    conf['model']['api_key'] = os.environ['HERMES_AM4_KEY']
    conf['providers']['custom']['api_key'] = os.environ['HERMES_AM4_KEY']
    conf['auxiliary']['compression']['api_key'] = os.environ['HERMES_AM4_KEY']
    save_config(conf, merge_existing=True)
    cli = ROOT/'venv/bin/hermes'
    args = sys.argv[1:] or ['chat','--cli']
    # Explicit CLI toolset overrides cannot widen this governed entrypoint.
    if any(a in ('-t','--toolsets','--provider','--base-url','--model') or a.startswith(('--toolsets=','--provider=','--base-url=','--model=')) for a in args):
        raise SystemExit('Use the governed profile; provider/toolset overrides are disabled.')
    if args and args[0] == 'chat':
        args.extend(['--toolsets','hearth'])
    os.execv(str(cli), [str(cli), *args])


if __name__ == '__main__':
    main()
