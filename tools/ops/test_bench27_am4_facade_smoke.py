"""Offline only: no credentials, SSH, services or inference."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from tools.ops import bench27_am4_facade_smoke as s


class SmokeTests(unittest.TestCase):
    def audit(self):
        return {'sha256':{'facade':'f','aliases':'a','recipe':s.SCRIPT_SHA},
                'models':{'data':[{'id':'qwen3-27b','root':'/home/derek/models/qwen3-27b-gptq-int4','max_model_len':49152}]},
                'metrics':'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\nvllm:request_success_total 1\n'}

    def test_hash_root_window_and_counters(self):
        self.assertEqual(s.validate_audit(self.audit(),'f','a')['success'],1)
        for kind in ['hash','root','32k','unavailable']:
            rec=self.audit()
            if kind=='hash':rec['sha256']['facade']='changed'
            if kind=='root':rec['models']['data'][0]['root']='wrong'
            if kind=='32k':rec['models']['data'][0]['max_model_len']=32768
            if kind=='unavailable':rec['models']['data']=[]
            with self.assertRaises(RuntimeError):s.validate_audit(rec,'f','a')

    def test_request_is_fixed_actual_facade_alias_reservation(self):
        b=s.request_body('short')
        self.assertEqual((s.HOST,s.PORT),('10.44.0.2',8090))
        self.assertEqual(b['model'],'am4-dense-27b')
        self.assertEqual(b['max_tokens'],24576)
        self.assertTrue(b['chat_template_kwargs']['enable_thinking'])

    def test_missing_env_token_refuses_before_output_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);args=SimpleNamespace(guard_log=root/'guard',trip=root/'guard.tripped',out=root/'out')
            with patch.object(s,'load_pool',return_value=SimpleNamespace(by_name=lambda n:s.Backend('am4-vllm','http://10.44.0.2:8090','openai',auth_env='AM4_VLLM_TOKEN'))), patch.object(s.Backend,'token',return_value=None), patch.object(s,'audit') as remote:
                with self.assertRaises(RuntimeError):s.run(args)
                remote.assert_not_called();self.assertFalse(args.out.exists())

    def test_guard_failure_cancels_owned_socket(self):
        smoke=object.__new__(s.Smoke);smoke.failure=None;smoke.lock=threading.RLock()
        smoke.sock=SimpleNamespace(shutdown=lambda how:seen.append('shutdown'))
        smoke.conn=SimpleNamespace(close=lambda:seen.append('close'));seen=[]
        smoke.cancel('guard stopped')
        self.assertEqual(seen,['shutdown','close']);self.assertEqual(smoke.failure,'guard stopped')

    def test_cancellation_requires_observed_running_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            for running in [0,1]:
                smoke=object.__new__(s.Smoke);smoke.args=SimpleNamespace(out=Path(tmp));smoke.check=lambda:None
                smoke.count=lambda b:100;smoke.cancel=lambda:None
                smoke.snapshot=lambda *a,**k:{'running':running,'waiting':0}
                raw=b'data: {"choices":[{"delta":{"reasoning":"thinking"},"finish_reason":null}]}\n\n'
                response=io.BytesIO(raw);response.status=200;smoke.request=lambda *a:response
                if running:
                    rec=smoke.stream(s.request_body('x'),'cancel1',True)
                    self.assertTrue(rec['cancelled_while_running'])
                else:
                    with self.assertRaises(RuntimeError):smoke.stream(s.request_body('x'),'cancel0',True)

    def test_exact_headroom_prevents_unintended_smoke_send(self):
        smoke=object.__new__(s.Smoke);smoke.count=lambda b:24545
        with patch.object(smoke,'request') as request:
            with self.assertRaises(RuntimeError):smoke.stream(s.request_body('x'),'short')
            request.assert_not_called()

    def test_drain_requires_second_idle_observation(self):
        smoke=object.__new__(s.Smoke);smoke.end=time.monotonic()+20
        calls=[]
        def snapshot(name,idle=True,cleanup=False):
            calls.append((name,idle));return {'running':0,'waiting':0,'success':1}
        smoke.snapshot=snapshot
        with patch.object(s.time,'sleep'):
            self.assertEqual(smoke.drain()['idle_samples'],2)
        self.assertEqual(calls[-1],('cancel-drain-confirm',True))

    def test_connect_trip_prevents_send(self):
        smoke=object.__new__(s.Smoke)
        smoke.args=SimpleNamespace(coordination_db='unused',guard_log='unused',trip='unused')
        smoke.lock=threading.RLock();smoke.failure=None;smoke.end=time.monotonic()+20
        smoke.conn=smoke.sock=None
        from unittest.mock import Mock
        conn=Mock()
        conn.connect.side_effect=lambda:smoke.cancel('guard')
        with patch.object(s,'guard_check'),patch.object(s,'no_leases'),patch.object(s.http.client,'HTTPConnection',return_value=conn):
            with self.assertRaises(s.SmokeError):smoke.request('/v1/chat/completions',{})
        conn.request.assert_not_called()

    def test_cleanup_observer_survives_failure_and_main_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            smoke=object.__new__(s.Smoke)
            smoke.args=SimpleNamespace(out=Path(tmp),coordination_db='unused',facade_sha256='f',alias_sha256='a')
            smoke.failure='guard';smoke.end=time.monotonic()-1;smoke.cleanup_end=time.monotonic()+20
            with patch.object(s,'no_leases'),patch.object(s,'audit',return_value=self.audit()),patch.object(s.time,'sleep'):
                self.assertEqual(smoke.drain(cleanup=True)['idle_samples'],2)
            self.assertTrue((Path(tmp)/'cleanup-drain-confirm.audit.json').exists())
            self.assertFalse((Path(tmp)/'cancel-drain-confirm.audit.json').exists())

    def test_exact_overflow_unique_facade_response(self):
        body,count=s.make_overflow(lambda b:len(b['messages'][0]['content'])//2+10)
        self.assertLess(count,s.WINDOW)
        exact=json.dumps({'error':f'rendered prompt {count} + output {s.BUDGET} exceeds context {s.WINDOW}'}).encode()
        s.validate_overflow(400,exact,count)
        for status,raw,n in [(400,b'{"error":"engine exceeds context"}',count),(500,exact,count),(400,exact,s.WINDOW+1)]:
            with self.assertRaises(s.SmokeError):s.validate_overflow(status,raw,n)

    def test_real_engine_usage_fixture_and_strict_reasoning_gate(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/bench27-am4-reasoning-usage.json').read_text())
        rec={'done':True,'finish_reason':'stop','content':'323','reasoning':'brief arithmetic',
             'prompt_tokens_exact':fixture['usage']['prompt_tokens'],'usage':fixture['usage']}
        s.validate_short(rec)
        rec['usage']['completion_tokens_details']['reasoning_tokens']=0
        with self.assertRaises(s.SmokeError):s.validate_short(rec)

    def test_headroom_boundary_is_allowed(self):
        smoke=object.__new__(s.Smoke);smoke.count=lambda b:24544
        with tempfile.TemporaryDirectory() as tmp:
            smoke.args=SimpleNamespace(out=Path(tmp))
            with patch.object(smoke,'request',side_effect=LookupError('reached send')) as request:
                with self.assertRaises(LookupError):smoke.stream(s.request_body('x'),'boundary')
                request.assert_called_once()

    def test_secret_not_serialized_on_external_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);secret='FAKE_ONLY_NEVER_A_REAL_TOKEN'
            args=SimpleNamespace(guard_log=root/'guard',trip=root/'guard.tripped',out=root/'out',
                                 coordination_db=root/'db',facade_sha256='f'*64,alias_sha256='a'*64)
            backend=SimpleNamespace(endpoint='http://10.44.0.2:8090',auth_env='AM4_VLLM_TOKEN',token=lambda:secret)
            stdout=io.StringIO()
            with patch.object(s,'load_pool',return_value=SimpleNamespace(by_name=lambda n:backend)), \
                 patch.object(s.Smoke,'snapshot',side_effect=ValueError(secret)), \
                 patch.object(s.Smoke,'watch'),patch('sys.stdout',stdout):
                self.assertEqual(s.run(args),3)
            output=stdout.getvalue()+''.join(p.read_text() for p in args.out.iterdir())
            self.assertNotIn(secret,output)
            result=json.loads((args.out/'summary.json').read_text())
            self.assertEqual(result['error_code'],'external_ValueError')

    def test_trip_during_lease_check_prevents_connection(self):
        smoke=object.__new__(s.Smoke)
        smoke.args=SimpleNamespace(coordination_db='unused',guard_log='unused',trip='unused')
        smoke.lock=threading.RLock();smoke.failure=None;smoke.end=time.monotonic()+20
        smoke.conn=smoke.sock=None
        with patch.object(s,'guard_check'),patch.object(s,'no_leases',side_effect=lambda _:smoke.cancel('signal')), \
             patch.object(s.http.client,'HTTPConnection') as connection:
            with self.assertRaises(s.SmokeError):smoke.request('/v1/chat/completions',{})
            connection.assert_not_called()


if __name__=='__main__':unittest.main()
