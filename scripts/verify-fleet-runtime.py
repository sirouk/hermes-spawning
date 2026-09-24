#!/usr/bin/env python3
"""Opt-in, state-writing native fleet qualification (NOT a read-only preflight).

Runs real model turns and creates one paused, local-delivery cron job per persona.
Model turns can change sessions, logs, usage, memory and plugin state. Cleanup only
removes the temporary job; it does not undo those effects. Use a fresh qualification
instance first. No service restart, arbitrary cron tick, config edit or guard bypass.

Run with the repository's Python environment (PyYAML is required by preflight).
  python3 scripts/verify-fleet-runtime.py --instance-dir instances/qualification \
      --allow-state-writes --container NAME [--persona default]
API requests run inside the verified container. --api-url overrides the native
loopback API address (HTTP allowed only for loopback). Keys are read from the
persona's .env, or --api-key-env names an existing host environment variable.
No model response or raw transcript is saved in evidence.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import uuid

REPO = Path(__file__).resolve().parents[1]
SKILLS = ('fleet-organism-design', 'hermes-bot-roster-and-rooms', 'hermes-group-chat-delivery')
SOURCE_FILES = ('hermes_cli/subcommands/cron.py', 'hermes_cli/_parser.py',
                'gateway/platforms/api_server_openai_routes.py', 'tools/approval_detection.py',
                'hermes_state_common.py', 'tools/skills_tool.py', 'pyproject.toml')


class QualificationError(RuntimeError):
    pass


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(argv, *, data=None, timeout=900):
    """Bounded subprocess. Do not include stdout/stderr (possibly secrets) in errors."""
    try:
        result = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise QualificationError('operation timed out; a container child may still be running') from exc
    if result.returncode:
        raise QualificationError(f'operation failed with exit code {result.returncode}')
    return result.stdout


def control_values(path):
    """Read simple deployment assignments without sourcing a shell."""
    values = {}
    for line in path.read_text().splitlines():
        key, sep, val = line.partition('=')
        if sep and re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            values[key] = val.strip().strip('"').strip("'")
    return values


def resolve_container(instance, container, control):
    if not container:
        project = control.get('COMPOSE_PROJECT_NAME')
        if not project:
            raise QualificationError('missing COMPOSE_PROJECT_NAME')
        names = run(['docker', 'ps', '--filter', f'label=com.docker.compose.project={project}',
                     '--filter', 'label=com.docker.compose.service=hermes', '--format', '{{.Names}}']).splitlines()
        if len(names) != 1:
            raise QualificationError('expected exactly one running Hermes container')
        container = names[0]
    info = json.loads(run(['docker', 'inspect', container]))[0]
    expected = (instance / 'hermes-data').resolve()
    matches = [m for m in info.get('Mounts', []) if m.get('Destination') == '/opt/data'
               and m.get('Type') == 'bind' and Path(m.get('Source', '')).resolve() == expected]
    if len(matches) != 1 or not info.get('State', {}).get('Running'):
        raise QualificationError('container does not mount this instance home at /opt/data, or is stopped')
    return container, info


class Native:
    def __init__(self, container, persona, uid, gid, timeout=900):
        self.container, self.persona, self.timeout = container, persona, timeout
        self.prefix = ['docker', 'exec', '-i', '--user', f'{uid}:{gid}',
                       '-e', 'HOME=/opt/data', '-e', 'HERMES_HOME=/opt/data',
                       '-e', 'PYTHONDONTWRITEBYTECODE=1', container]

    def python(self, source, args=(), data=None):
        # -c script passed as argv; optional stdin data can contain a key, never argv.
        text = run(self.prefix + ['/opt/hermes/.venv/bin/python', '-B', '-c', source, *args],
                   data=data, timeout=self.timeout)
        return json.loads(text)

    def cli(self, *args):
        return run(self.prefix + ['hermes', '-p', self.persona, *args], timeout=self.timeout)

    def schema(self):
        source = """import json, pathlib, hashlib
root=pathlib.Path('/opt/hermes')
files=json.loads(__import__('sys').argv[1])
print(json.dumps({p:(root/p).read_text() for p in files}))
"""
        texts = self.python(source, [json.dumps(SOURCE_FILES)])
        requirements = {
            SOURCE_FILES[0]: ('"--paused"', '"--failure-deliver"', '"--deliver"'),
            SOURCE_FILES[1]: ('"--format"', '"stream-json"'),
            SOURCE_FILES[2]: ('X-Hermes-Session-Id', 'body.get("messages")'),
            SOURCE_FILES[3]: ('def detect_dangerous_command(', 'def detect_hardline_command('),
            SOURCE_FILES[4]: ('CREATE TABLE IF NOT EXISTS messages', 'tool_call_id TEXT', 'tool_calls TEXT'),
            SOURCE_FILES[5]: ('"success": True', '"content":'),
        }
        for path, needles in requirements.items():
            if any(needle not in texts[path] for needle in needles):
                raise QualificationError(f'unsupported installed native interface: {path}')
        return {p: hashlib.sha256(t.encode()).hexdigest() for p, t in texts.items()}

    def classify(self, command):
        return self.python("""import json,sys
sys.path.insert(0,'/opt/hermes')
from tools.approval_detection import detect_dangerous_command, detect_hardline_command
s=sys.argv[1]
print(json.dumps({'dangerous':bool(detect_dangerous_command(s)[0]),'hardline':bool(detect_hardline_command(s)[0])}))
""", [command])

    def api(self, prompt, session_id, url=None, api_key=None):
        """Use supported profile mirror; fail if native configured key is absent."""
        result = self.python(r"""import json,sys,pathlib,urllib.request,urllib.parse
import yaml
from dotenv import dotenv_values
p=json.load(sys.stdin)
root=pathlib.Path('/opt/data'); home=root if p['persona']=='default' else root/'profiles'/p['persona']
cfg=yaml.safe_load((root/'config.yaml').read_text()) or {}
vals=dotenv_values(home/'.env')
active_cfg=yaml.safe_load((home/'config.yaml').read_text()) or {}
key=p.get('key') or ((active_cfg.get('platforms') or {}).get('api_server') or {}).get('key') or vals.get('API_SERVER_KEY')
if not key:
    print(json.dumps({'qualification_error':'API: profile-scoped API_SERVER_KEY is not configured'}))
    sys.exit(0)
base=p.get('url')
if not base:
    pc=(cfg.get('platforms') or {}).get('api_server') or {}
    env=dotenv_values(root/'.env')
    port=pc.get('port') or env.get('API_SERVER_PORT') or 8642
    base='http://127.0.0.1:'+str(port)
u=urllib.parse.urlsplit(base)
if u.scheme not in ('https','http') or u.username or u.password or u.query or u.fragment:
    raise RuntimeError('invalid API origin')
if u.scheme=='http' and u.hostname not in ('127.0.0.1','localhost','::1'):
    raise RuntimeError('unencrypted non-loopback API rejected')
if u.path not in ('','/'):
    raise RuntimeError('API URL must be an origin without a path')
url=base.rstrip('/')+'/p/'+urllib.parse.quote(p['persona'],safe='')+'/v1/chat/completions'
body=json.dumps({'messages':[{'role':'user','content':p['prompt']}],'stream':False}).encode()
req=urllib.request.Request(url,body,{'Authorization':'Bearer '+key,'Content-Type':'application/json','X-Hermes-Session-Id':p['session_id']})
with urllib.request.urlopen(req,timeout=p['timeout']) as r:
    r.read() # Model claims are not evidence; state.db is checked separately.
    print(json.dumps({'http_status':r.status}))
""", data=json.dumps({'persona': self.persona, 'prompt': prompt, 'session_id': session_id,
                        'url': url, 'key': api_key, 'timeout': self.timeout}))
        if result.get('qualification_error'):
            raise QualificationError(result['qualification_error'])
        return result


def persona_home(home, persona):
    return home if persona == 'default' else home / 'profiles' / persona


def read_rows(home):
    path = home / 'state.db'
    if not path.exists():
        return []
    # mode=ro is important: no schema migration or new DB. No immutable=1: observe WAL.
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in con.execute(
            'SELECT id,session_id,role,content,tool_call_id,tool_calls,tool_name FROM messages ORDER BY id')]
    finally:
        con.close()


def new_rows(home, minimum_id):
    return [r for r in read_rows(home) if r['id'] > minimum_id]


def parse_json(value, default=None):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return default


def paired_tools(rows):
    """Only actual role=tool rows paired with assistant call IDs in SAME session."""
    calls = {}
    for row in rows:
        if row['role'] != 'assistant':
            continue
        for call in parse_json(row.get('tool_calls'), []) or []:
            if not isinstance(call, dict):
                continue
            fn = call.get('function') or {}
            if call.get('id') and fn.get('name'):
                calls[(row['session_id'], call['id'])] = (fn['name'], parse_json(fn.get('arguments'), {}))
    for row in rows:
        key = (row['session_id'], row.get('tool_call_id'))
        if row['role'] != 'tool' or key not in calls:
            continue
        name, arguments = calls[key]
        body = parse_json(row.get('content'), {})
        if isinstance(body, dict) and isinstance(arguments, dict):
            yield row, name, arguments, body


def tool_evidence(rows, tool, arg_key, exact_arg, marker):
    for row, name, args, body in paired_tools(rows):
        if name != tool or args.get(arg_key) != exact_arg:
            continue
        if tool == 'terminal' and (args.get('background', False) is not False
                or args.get('heartbeat', 0) != 0 or not isinstance(args.get('timeout'), (int, float))
                or not 0 < args['timeout'] <= 10):
            continue
        output = body.get('output', body.get('stdout', ''))
        if not isinstance(output, str) or marker not in output or body.get('error'):
            continue
        exit_code = body.get('exit_code')
        if type(exit_code) is not int or exit_code != 0:
            continue
        if body.get('status') in ('error', 'timeout', 'interrupted'):
            continue
        return {'ok': True, 'call_id': row['tool_call_id'], 'session_id': row['session_id'],
                'stdout': marker, 'stdout_sha256': hashlib.sha256(output.encode()).hexdigest(),
                'stdout_retention': 'verified-marker-only', 'expected_stdout': marker, 'exit_code': 0,
                'arguments': args, 'tool': tool}
    raise QualificationError(f'no verified {tool} call/result pair for required marker')


def skill_evidence(rows, expected):
    found = {}
    for row, name, args, body in paired_tools(rows):
        requested = args.get('name')
        content = body.get('content')
        expected_dir = '/opt/data/fleet-skills/'+str(requested)
        if (name == 'skill_view' and requested in SKILLS and body.get('success') is True
                and isinstance(content, str) and content == expected.get(requested)
                and body.get('_source_path') == expected_dir+'/SKILL.md'
                and body.get('skill_dir', expected_dir) == expected_dir):
            found[requested] = row['tool_call_id']
    if set(found) != set(SKILLS):
        raise QualificationError('missing successful native skill_view results')
    return {'ok': True, 'names': sorted(found), 'call_ids': found,
            'content_sha256': {name: hashlib.sha256(expected[name].encode()).hexdigest() for name in found},
            'source_root': '/opt/data/fleet-skills'}


def shell_probe(marker, dangerous=True):
    literals = f"'169.254.169.254' '{marker}'" if dangerous else f"'{marker}'"
    return "printf '%s\\n' " + literals


def terminal_prompt(command):
    return ('Call terminal exactly once with '+json.dumps({'command': command, 'timeout': 10, 'background': False,
                'heartbeat': 0, 'notify': False, 'pty': False})+
            '. Set heartbeat to 0 (disabled), never 60; keep background, notify and pty false. Do not use another shell wrapper, retries, delegation, file writes or network calls. ')


def jobs(home):
    path = home / 'cron' / 'jobs.json'
    if not path.exists():
        return []
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('jobs'), list):
        raise QualificationError('unsupported native cron storage schema')
    return value['jobs']


def ensure_future_job(job):
    try:
        when = dt.datetime.fromisoformat(job['next_run_at'].replace('Z', '+00:00'))
        if when.tzinfo is None or when <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1):
            raise ValueError('not safely future-dated')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise QualificationError('temporary cron job must remain more than one hour in the future') from exc


def stage_evidence(stage, rows, tool, arg_key, exact_arg, marker):
    try:
        return tool_evidence(rows, tool, arg_key, exact_arg, marker)
    except QualificationError as exc:
        raise QualificationError(stage + ': ' + str(exc)) from exc


def scoped_cron(native, home, prompt, name):
    """Never parse a human CLI line as a job ID. Read only our exact unique job name."""
    if any(j.get('name') == name for j in jobs(home)):
        raise QualificationError('temporary job name collision')
    job_id = None
    result = None
    cleanup_ok = False
    try:
        scheduled_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
        native.cli('cron', 'create', scheduled_at, prompt, '--name', name, '--paused',
                   '--paused-reason', 'runtime qualification', '--repeat', '1',
                   '--deliver', 'local', '--failure-deliver', 'local')
        matches = [j for j in jobs(home) if j.get('name') == name]
        if len(matches) != 1:
            raise QualificationError('could not uniquely identify temporary cron job')
        if matches[0].get('enabled') is not False:
            raise QualificationError('temporary cron job was not created paused')
        if matches[0].get('deliver') != 'local' or matches[0].get('failure_deliver') != 'local':
            raise QualificationError('temporary cron job delivery is not local')
        job_id = matches[0]['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', job_id):
            raise QualificationError('unexpected native job ID shape')
        # Native manual=True does not bypass paused state despite CLI help wording.
        # Resume only our future-dated job; never make an occurrence due to the ticker.
        schedule = matches[0].get('schedule', {})
        if schedule.get('kind') != 'once' or schedule.get('run_at') != scheduled_at:
            raise QualificationError('temporary cron schedule differs from exact future one-shot')
        # Native paused creation deliberately sets next_run_at=None. Validate the
        # stored one-shot run_at before resume; validate next_run_at afterward.
        ensure_future_job({'next_run_at': schedule['run_at']})
        native.cli('cron', 'resume', job_id)
        resumed = [j for j in jobs(home) if j.get('id') == job_id]
        if len(resumed) != 1 or resumed[0].get('enabled') is not True:
            raise QualificationError('temporary cron job was not resumed')
        ensure_future_job(resumed[0])
        native.cli('cron', 'run', job_id)
        result = job_id
    finally:
        # Even create can time out after writing. Recover exact unique owned name only.
        matches = [j for j in jobs(home) if j.get('name') == name]
        if len(matches) == 1:
            owned_id = matches[0].get('id', '')
            if re.fullmatch(r'[A-Za-z0-9_-]+', owned_id):
                native.cli('cron', 'remove', owned_id)
                cleanup_ok = not any(j.get('id') == owned_id for j in jobs(home))
        elif not matches:
            cleanup_ok = True
        if not cleanup_ok:
            raise QualificationError('temporary cron cleanup incomplete; inspect scoped job manually')
    return result, cleanup_ok


def atomic_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(report, out, indent=2)
        out.write('\n')
    os.replace(tmp, path)


def qualify(native, home, persona, before_binding, *, api_url=None, api_key=None, progress=None):
    progress = progress if progress is not None else {}
    ph = persona_home(home, persona)
    nonce = 'fleetqual_' + uuid.uuid4().hex
    config = native.python("import sys,json; sys.path.insert(0,'/opt/hermes'); from pathlib import Path; import yaml; p=Path(sys.argv[1]); print(json.dumps((yaml.safe_load(p.read_text()) or {}).get('approvals',{})))",
                           [str(Path('/opt/data') / ('config.yaml' if persona == 'default' else 'profiles/'+persona+'/config.yaml'))])
    if config.get('mode') not in ('off', False):
        raise QualificationError('approvals.mode must already be off; runner never edits policy')
    initial = max((r['id'] for r in read_rows(ph)), default=0)
    cli_marker, cron_marker, code_marker, api_marker = (nonce+'_'+x for x in ('cli','cron','code','api'))
    cli_command, cron_command, api_command = map(shell_probe, (cli_marker, cron_marker, api_marker))
    classifications = [native.classify(c) for c in (cli_command, cron_command, api_command)]
    if any(c != {'dangerous': True, 'hardline': False} for c in classifications):
        raise QualificationError('safe literal probe did not match expected installed classifier')
    classification = classifications[0]
    prompt = ('Qualification '+nonce+'. '+terminal_prompt(cli_command)+
              'Then call skill_view for each exact name: '+', '.join(SKILLS)+
              '. Only read skills; do not carry out their workflows. Finish with one short sentence.')
    native.cli('chat', '-q', prompt, '--format', 'stream-json', '--max-turns', '6')
    cli_rows = new_rows(ph, initial)
    cli_check = stage_evidence('CLI', cli_rows, 'terminal', 'command', cli_command, cli_marker)
    progress.update(foreground_terminal=dict(cli_check), single_query_dangerous_shell=dict(cli_check))
    expected_skills = {name: (home/'fleet-skills'/name/'SKILL.md').read_text() for name in SKILLS}
    skills_check = skill_evidence(cli_rows, expected_skills)
    progress['fleet_skills_visible'] = skills_check
    cron_start = max((r['id'] for r in read_rows(ph)), default=initial)
    code = "print('"+code_marker+"')"
    cron_prompt = ('Qualification '+nonce+'. '+terminal_prompt(cron_command)+
                   'Then call execute_code exactly once with code='+json.dumps(code)+
                   '. Do not create other jobs, send messages or modify files. Finish.')
    job_id, cleaned = scoped_cron(native, ph, cron_prompt, nonce)
    cron_rows = new_rows(ph, cron_start)
    cron_shell = stage_evidence('cron shell', cron_rows, 'terminal', 'command', cron_command, cron_marker)
    cron_code = stage_evidence('cron execute_code', cron_rows, 'execute_code', 'code', code, code_marker)
    if not cron_shell['session_id'].startswith('cron_'+job_id+'_'):
        raise QualificationError('cron evidence not bound to temporary job session')
    if cron_shell['session_id'] != cron_code['session_id']:
        raise QualificationError('cron tool results must belong to one native session')
    for check in (cron_shell, cron_code):
        check.update(job_id=job_id, cleanup_ok=cleaned, completed=True)
    progress.update(cron_dangerous_shell=cron_shell, cron_execute_code=cron_code)
    api_start = max((r['id'] for r in read_rows(ph)), default=cron_start)
    api_session = nonce+'_api_session'
    native.api('Qualification '+nonce+'. '+terminal_prompt(api_command)+'Finish.', api_session, api_url, api_key)
    api_check = stage_evidence('API', new_rows(ph, api_start), 'terminal', 'command', api_command, api_marker)
    if api_check['session_id'] != api_session:
        raise QualificationError('API result did not bind requested persona session')
    progress['api_dangerous_shell'] = api_check
    for check in (cli_check, cron_shell, api_check):
        check.update(classified_dangerous=True, classified_hardline=False)
    return {'schema': 1, 'producer': 'verify-fleet-runtime', 'scope': 'native-end-to-end',
            'persona': persona, 'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'binding': before_binding, 'classification': classification,
            'checks': {'approvals': {'ok': True, 'mode': 'off', 'pending': 0, 'scope': 'tested-tool-calls-only'},
                       'foreground_terminal': dict(cli_check), 'single_query_dangerous_shell': cli_check,
                       'cron_dangerous_shell': cron_shell, 'cron_execute_code': cron_code,
                       'api_dangerous_shell': api_check, 'fleet_skills_visible': skills_check}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--instance-dir', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--container')
    parser.add_argument('--persona', action='append')
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--api-url')
    parser.add_argument('--api-key-env')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--allow-state-writes', action='store_true')
    args = parser.parse_args(argv)
    if not args.allow_state_writes:
        parser.error('--allow-state-writes required: model calls and cron qualification write state')
    if args.timeout < 10 or args.timeout > 3600:
        parser.error('--timeout must be 10..3600 seconds')
    instance, repo = args.instance_dir.resolve(), args.repo.resolve()
    home = instance / 'hermes-data'
    api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
    if args.api_key_env and not api_key:
        parser.error('API key environment variable is empty')
    try:
        control = control_values(instance / 'control.env')
        container, info = resolve_container(instance, args.container, control)
        stack = load_module(repo / 'lib/apply_stack.py', 'runtime_apply_stack')
        preflight = load_module(repo / 'lib/fleet_preflight.py', 'runtime_preflight')
        personas = stack.personas(str(home))
        selected = args.persona or personas
        if len(set(selected)) != len(selected) or any(p not in personas for p in selected):
            raise QualificationError('invalid or duplicate persona selection')
        evidence_dir = args.evidence_dir or home / 'fleet-preflight'
        uid, gid = control.get('HERMES_UID', '10000'), control.get('HERMES_GID', '10000')
        if not uid.isdigit() or not gid.isdigit():
            raise QualificationError('invalid container UID/GID')
        failures = []
        for persona in selected:
            partial_checks = {}
            native = Native(container, persona, uid, gid, args.timeout)
            path = evidence_dir / (persona+'.json')
            try:
                schemas = native.schema()
                # Use identical hash implementation on native installed source, no Hermes imports.
                import inspect
                hash_source = inspect.getsource(preflight.tree_hash) + '\n' + inspect.getsource(preflight.runtime_fingerprint)
                runtime_hash = native.python('import hashlib, json, os\nfrom pathlib import Path\n'+hash_source+
                                             "\nprint(json.dumps(runtime_fingerprint(Path('/opt/hermes'))))")
                binding = preflight.binding(home, persona, repo/'stack-defaults.yaml', repo/'skills', runtime_hash)
                report = qualify(native, home, persona, binding, api_url=args.api_url, api_key=api_key, progress=partial_checks)
                after_hash = native.python('import hashlib, json, os\nfrom pathlib import Path\n'+hash_source+
                                           "\nprint(json.dumps(runtime_fingerprint(Path('/opt/hermes'))))")
                after = preflight.binding(home, persona, repo/'stack-defaults.yaml', repo/'skills', after_hash)
                if binding != after:
                    changed = ', '.join(sorted(key for key in set(binding) | set(after) if binding.get(key) != after.get(key)))
                    raise QualificationError('binding changed during qualification (' + changed + '); evidence invalid')
                errors = preflight.validate_evidence(report, binding, persona, SKILLS)
                if errors:
                    raise QualificationError('evidence contract validation failed: '+ '; '.join(errors))
                report['native_schema_sha256'] = schemas
                report['container_image_id'] = info['Image']
                atomic_report(path, report)
                print(persona+': verified native runtime; evidence '+str(path))
            except Exception as exc:
                # Invalidate stale success without retaining model text or secret-bearing errors.
                atomic_report(path, {'schema': 1, 'producer': 'verify-fleet-runtime', 'persona': persona,
                                     'scope': 'native-end-to-end', 'checks': {}, 'failed': True,
                                     'partial_checks_unqualified': partial_checks,
                                     'error_type': type(exc).__name__,
                                     'error': str(exc) if isinstance(exc, QualificationError) else 'native qualification failed'})
                failures.append(persona)
                print(persona+': FAILED; no success evidence', file=sys.stderr)
        return 1 if failures else 0
    except Exception as exc:
        print(str(exc) if isinstance(exc, QualificationError) else 'qualification setup failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
