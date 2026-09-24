"""Unit tests using synthetic native transcripts/mocks; NOT fleet qualification."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verify_runtime', ROOT/'scripts/verify-fleet-runtime.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def transcript(command, output='nonce', exit_code=0, session='test', tool='terminal'):
    arg = {'command': command, 'timeout': 10, 'background': False, 'heartbeat': 0, 'notify': False, 'pty': False} if tool == 'terminal' else {'code': command}
    return [dict(id=1,session_id=session,role='assistant',tool_calls=json.dumps([
        {'id':'call_1','function':{'name':tool,'arguments':json.dumps(arg)}}]),content='claim',tool_call_id=None),
        dict(id=2,session_id=session,role='tool',tool_calls=None,content=json.dumps({'output':output,'exit_code':exit_code}),tool_call_id='call_1')]


class EvidenceUnitTests(unittest.TestCase):
    def test_success_requires_actual_pair(self):
        rows = transcript('printf x')
        self.assertTrue(v.tool_evidence(rows,'terminal','command','printf x','nonce')['ok'])

    def test_model_claim_not_evidence(self):
        rows = transcript('printf x')[:1]
        rows[0]['content'] = 'nonce exit_code=0 success'
        with self.assertRaises(v.QualificationError):
            v.tool_evidence(rows,'terminal','command','printf x','nonce')

    def test_wrong_id_and_session_rejected(self):
        for field, value in [('tool_call_id','other'),('session_id','other')]:
            rows = transcript('printf x'); rows[1][field] = value
            with self.assertRaises(v.QualificationError):
                v.tool_evidence(rows,'terminal','command','printf x','nonce')

    def test_failed_or_fabricated_output_rejected(self):
        for output, code in [('nonce',1),('not-present',0),('nonce',False)]:
            with self.assertRaises(v.QualificationError):
                v.tool_evidence(transcript('printf x',output,code),'terminal','command','printf x','nonce')

    def test_argument_mutation_rejected(self):
        for update in [{'heartbeat':1},{'background':True},{'timeout':11},{'command':'other'}]:
            rows=transcript('printf x'); call=json.loads(rows[0]['tool_calls'])
            args=json.loads(call[0]['function']['arguments']); args.update(update)
            call[0]['function']['arguments']=json.dumps(args); rows[0]['tool_calls']=json.dumps(call)
            with self.assertRaises(v.QualificationError):
                v.tool_evidence(rows,'terminal','command','printf x','nonce')

    def test_foreground_prompt_explicitly_disables_heartbeat(self):
        command=v.shell_probe('nonce')
        prompt=v.terminal_prompt(command)
        args=json.JSONDecoder().raw_decode(prompt.split(' with ',1)[1])[0]
        self.assertEqual(args,{'command':command,'timeout':10,'background':False,
                               'heartbeat':0,'notify':False,'pty':False})
        self.assertNotIn('Omit heartbeat',prompt)

    def test_actual_heartbeat_failure_cannot_pass(self):
        rows=transcript('printf x')
        calls=json.loads(rows[0]['tool_calls'])
        args=json.loads(calls[0]['function']['arguments']); args['heartbeat']=60
        calls[0]['function']['arguments']=json.dumps(args); rows[0]['tool_calls']=json.dumps(calls)
        rows[1]['content']=json.dumps({'error':'notify/heartbeat only apply to background commands (foreground results return directly).'})
        with self.assertRaises(v.QualificationError):
            v.tool_evidence(rows,'terminal','command','printf x','nonce')

    def test_missing_named_api_key_is_clear_failure(self):
        native=v.Native('mock','scout',10000,10000)
        with patch.object(native,'python',return_value={'qualification_error':'API: profile-scoped API_SERVER_KEY is not configured'}):
            with self.assertRaisesRegex(v.QualificationError,'API: profile-scoped'):
                native.api('prompt','session')

    def test_opt_in_before_any_operation(self):
        with patch.object(v,'run') as run:
            with self.assertRaises(SystemExit):
                v.main(['--instance-dir','/nonexistent'])
            run.assert_not_called()

    def test_mount_crosscheck(self):
        info=[{'State':{'Running':True},'Mounts':[{'Destination':'/opt/data','Type':'bind','Source':'/wrong'}]}]
        with patch.object(v,'run',return_value=json.dumps(info)):
            with self.assertRaises(v.QualificationError):
                v.resolve_container(Path('/test'),'container',{})

    def _scoped_cron_cleanup(self, fail_at):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'cron').mkdir(); path=home/'cron/jobs.json'
            path.write_text(json.dumps({'jobs': [{'id':'unrelated','name':'production'}]}))
            calls=[]
            class MockNative:
                def cli(self,*args):
                    calls.append(args)
                    data=json.loads(path.read_text())
                    if args[:2]==('cron','create'):
                        data['jobs'].append({'id':'our-job','name':'unique-probe','enabled':False,'deliver':'local','failure_deliver':'local','next_run_at':None,'schedule':{'kind':'once','run_at':args[2]}})
                    elif args[:2]==('cron','resume'):
                        if fail_at == 'resume': raise v.QualificationError('mock resume failure')
                        job=next(j for j in data['jobs'] if j['id']==args[2]); job['enabled']=True
                        job['next_run_at']=job['schedule']['run_at']
                    elif args[:2]==('cron','run'):
                        raise v.QualificationError('mock failure')
                    elif args[:2]==('cron','remove'):
                        data['jobs']=[j for j in data['jobs'] if j['id']!=args[2]]
                    path.write_text(json.dumps(data))
            with self.assertRaises(v.QualificationError):
                v.scoped_cron(MockNative(),home,'prompt','unique-probe')
            self.assertEqual(v.jobs(home),[{'id':'unrelated','name':'production'}])
            self.assertIn(('cron','remove','our-job'),calls)
            self.assertIn(('cron','resume','our-job'),calls)
            if fail_at == 'run': self.assertIn(('cron','run','our-job'),calls)
            self.assertFalse(any('tick' in call for call in calls))
            self.assertIn('--paused',calls[0]); self.assertIn('--failure-deliver',calls[0])

    def test_scoped_cron_cleanup_on_run_failure(self):
        self._scoped_cron_cleanup('run')

    def test_scoped_cron_cleanup_on_resume_failure(self):
        self._scoped_cron_cleanup('resume')

    def test_cron_schedule_must_stay_safely_future_dated(self):
        for value in [None,'invalid',v.dt.datetime.now(v.dt.timezone.utc).isoformat(),
                      (v.dt.datetime.now(v.dt.timezone.utc)+v.dt.timedelta(minutes=59)).isoformat()]:
            with self.assertRaises(v.QualificationError):
                v.ensure_future_job({'next_run_at':value})
        v.ensure_future_job({'next_run_at':(v.dt.datetime.now(v.dt.timezone.utc)+v.dt.timedelta(days=1)).isoformat()})

    def test_evidence_failure_identifies_stage(self):
        with self.assertRaisesRegex(v.QualificationError,'cron shell:'):
            v.stage_evidence('cron shell',[], 'terminal','command','printf x','nonce')

    def test_shell_probe_is_literal_only(self):
        self.assertEqual(v.shell_probe('nonce'),"printf '%s\\n' '169.254.169.254' 'nonce'")

    def test_skill_claim_without_tool_result_rejected(self):
        with self.assertRaises(v.QualificationError):
            v.skill_evidence([], {})


    def test_mocked_orchestration_contract_not_runtime_verification(self):
        from types import SimpleNamespace
        token='a'*32
        nonce='fleetqual_'+token
        commands=[v.shell_probe(nonce+'_'+x) for x in ('cli','cron','api')]
        rows_cli=transcript(commands[0],nonce+'_cli')
        for i,name in enumerate(v.SKILLS):
            rows_cli.extend([
                dict(id=10+i*2,session_id='test',role='assistant',tool_calls=json.dumps([
                    {'id':'skill_'+str(i),'function':{'name':'skill_view','arguments':json.dumps({'name':name})}}]),content='',tool_call_id=None),
                dict(id=11+i*2,session_id='test',role='tool',tool_calls=None,tool_call_id='skill_'+str(i),
                     content=json.dumps({'success':True,'name':name,'content':'unit mock skill','skill_dir':'/opt/data/fleet-skills/'+name,'_source_path':'/opt/data/fleet-skills/'+name+'/SKILL.md'}))])
        rows_cron=transcript(commands[1],nonce+'_cron',session='cron_ourjob_20260101')
        rows_code=transcript("print('"+nonce+"_code')",nonce+'_code',session='cron_ourjob_20260101',tool='execute_code')
        # Native call IDs must be distinct within a session.
        call=json.loads(rows_code[0]['tool_calls']); call[0]['id']='code_call'; rows_code[0]['tool_calls']=json.dumps(call)
        rows_code[1]['tool_call_id']='code_call'; rows_cron.extend(rows_code)
        rows_api=transcript(commands[2],nonce+'_api',session=nonce+'_api_session')
        class MockNative:
            def python(self,*a,**k): return {'mode':'off'}
            def classify(self,command): return {'dangerous':True,'hardline':False}
            def cli(self,*args): pass
            def api(self,*args): pass
        with patch.object(v.uuid,'uuid4',return_value=SimpleNamespace(hex=token)), \
             patch.object(v,'read_rows',return_value=[]), \
             patch.object(v,'new_rows',side_effect=[rows_cli,rows_cron,rows_api]), \
             patch.object(v,'scoped_cron',return_value=('ourjob',True)), \
             patch.object(Path,'read_text',return_value='unit mock skill'):
            report=v.qualify(MockNative(),Path('/synthetic'),'default',{})
        self.assertEqual(set(report['checks']),{'approvals','foreground_terminal','single_query_dangerous_shell',
                         'cron_execute_code','cron_dangerous_shell','api_dangerous_shell','fleet_skills_visible'})
        self.assertTrue(report['checks']['cron_execute_code']['cleanup_ok'])
        self.assertEqual(report['checks']['api_dangerous_shell']['stdout'],nonce+'_api')

    def test_skill_shadow_or_modified_content_rejected(self):
        rows=[]
        expected={name:'canonical '+name for name in v.SKILLS}
        for i,name in enumerate(v.SKILLS):
            rows.extend([
                dict(id=i*2,session_id='s',role='assistant',tool_call_id=None,content='',
                     tool_calls=json.dumps([{'id':str(i),'function':{'name':'skill_view','arguments':json.dumps({'name':name})}}])),
                dict(id=i*2+1,session_id='s',role='tool',tool_calls=None,tool_call_id=str(i),
                     content=json.dumps({'success':True,'content':expected[name],'skill_dir':'/opt/data/fleet-skills/'+name,'_source_path':'/opt/data/fleet-skills/'+name+'/SKILL.md'}))])
        self.assertTrue(v.skill_evidence(rows,expected)['ok'])
        original=rows[1]['content']; body=json.loads(original)
        body['skill_dir']='/opt/data/skills/'+v.SKILLS[0]; rows[1]['content']=json.dumps(body)
        with self.assertRaises(v.QualificationError): v.skill_evidence(rows,expected)
        body=json.loads(original); body['content']='stale local copy'; rows[1]['content']=json.dumps(body)
        with self.assertRaises(v.QualificationError): v.skill_evidence(rows,expected)

    def test_report_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json'; v.atomic_report(p,{'ok':False})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)


if __name__ == '__main__':
    unittest.main()
