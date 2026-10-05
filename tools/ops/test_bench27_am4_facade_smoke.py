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
            with patch.object(s.Backend,'token',return_value=None), patch.object(s,'audit') as remote:
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
        def snapshot(name,idle=True):
            calls.append((name,idle));return {'running':0,'waiting':0}
        smoke.snapshot=snapshot
        with patch.object(s.time,'sleep'):
            self.assertEqual(smoke.drain()['idle_samples'],2)
        self.assertEqual(calls[-1],('cancel-drain-confirm',True))


if __name__=='__main__':unittest.main()
