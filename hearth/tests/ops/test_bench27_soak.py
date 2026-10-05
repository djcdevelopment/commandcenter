import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from tools.ops import bench27_soak as soak

class SoakTests(unittest.TestCase):
    def test_overlap_idle_and_last_completed_pair(self):
        pairs=[{'intervals':{'0':[0,3500],'1':[5,3600]}},{'intervals':{'0':[3602,7300],'1':[3603,7205]}}]
        c=soak.coverage(pairs)
        self.assertEqual(c['measured_campaign_span_seconds'],7300)
        self.assertEqual(c['global_between_pair_gaps_seconds'],[2])
        self.assertEqual(c['per_seat']['0']['between_run_idle_seconds'],[102])
        self.assertEqual(c['both_run_overlap_seconds'],3495+3602)
    def test_unknown_counts_refuse(self):
        with TemporaryDirectory() as d:
            p=Path(d);(p/'paired.json').write_text(json.dumps({'calls':{'0':None,'1':2}}))
            with self.assertRaisesRegex(ValueError,'unknown calls'):soak.report_pair(p,'arm')
    def test_hash_change_refuses(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'work';p.write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash changed'):soak.verify({'workload':str(p),'workload_sha256':'wrong','driver_sha256':{}})
    def test_minimum_cannot_be_shortened(self):
        with self.assertRaises(ValueError):soak.run(SimpleNamespace(minimum_seconds=1,maximum_seconds=8400))
    def test_complete_loop_aggregates_actual_calls_and_unique_outputs(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','placeholder']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            children=[]
            class Child:
                returncode=0
                def __init__(self,cmd,**kwargs):children.append(cmd)
                def poll(self):return 0
            results=[({'0':2,'1':2},{'0':[0,3600],'1':[1,3601]}),({'0':2,'1':2},{'0':[3602,7205],'1':[3603,7206]})]
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal'),patch.object(soak.subprocess,'Popen',Child),patch.object(soak,'report_pair',side_effect=results),patch('builtins.print'):
                self.assertEqual(soak.run(args),0)
            result=soak.read(args.out/'summary.json')
            self.assertEqual(result['calls'],{'0':4,'1':4})
            self.assertEqual(result['coverage']['measured_campaign_span_seconds'],7206)
            self.assertNotEqual(children[0][-1],children[1][-1])
    def test_failed_pair_keeps_partial_calls_and_stops(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','x']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            child=SimpleNamespace(returncode=3,poll=lambda:3)
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal'),patch.object(soak.subprocess,'Popen',return_value=child),patch.object(soak,'report_pair',return_value=({'0':1,'1':2},{'0':[0,10],'1':[0,12]})),patch('builtins.print'):
                self.assertEqual(soak.run(args),3)
            self.assertEqual(soak.read(args.out/'summary.json')['calls'],{'0':1,'1':2})

    def test_deadline_terminates_owned_child_and_preserves_partial_counts(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','x']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            class Child:
                pid=98765
                returncode=None
                def poll(self):return self.returncode
                def wait(self,timeout=None):self.returncode=3;return 3
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal'),patch.object(soak.subprocess,'Popen',return_value=Child()),patch.object(soak.time,'monotonic',side_effect=[0,1,8401]),patch.object(soak.os,'killpg') as kill,patch.object(soak,'report_pair',return_value=({'0':1,'1':1},None)),patch('builtins.print'):
                self.assertEqual(soak.run(args),3)
            kill.assert_called_once_with(98765,soak.signal.SIGTERM)
            self.assertEqual(soak.read(args.out/'summary.json')['calls'],{'0':1,'1':1})
    def test_known_calls_survive_absent_run_records(self):
        with TemporaryDirectory() as d:
            p=Path(d);(p/'paired.json').write_text(json.dumps({'calls':{'0':1,'1':0}}))
            self.assertEqual(soak.report_pair(p,'arm'),({'0':1,'1':0},None))

    def test_force_kill_precedes_harness_30_second_grace(self):
        waits=[];signals=[]
        class Child:
            pid=123
            def poll(self):return None
            def wait(self,timeout=None):
                waits.append(timeout)
                if timeout is not None:
                    raise soak.subprocess.TimeoutExpired('child',timeout)
                return -9
        with patch.object(soak.os,'killpg',side_effect=lambda pid,sig:signals.append(sig)):
            soak.stop_child(Child())
        self.assertEqual(waits,[25,None])
        self.assertLess(waits[0],30)
        self.assertEqual(signals,[soak.signal.SIGTERM,soak.signal.SIGKILL])
    def test_exited_group_race_is_harmless(self):
        with patch.object(soak.os,'killpg',side_effect=ProcessLookupError):
            soak.signal_group(SimpleNamespace(pid=123),soak.signal.SIGTERM)
    def test_real_run_keys_and_whole_second_times(self):
        with TemporaryDirectory() as d:
            p=Path(d);(p/'paired.json').write_text(json.dumps({'calls':{'0':2,'1':2},'ok':True}))
            for seat in ['0','1']:
                directory=p/f'seat-{seat}';directory.mkdir()
                (directory/'run.json').write_text(json.dumps({'schema':'thinking-run.v1','arm':'soak','concurrency':1,'started_utc':'2026-10-05T00:00:00Z','finished_utc':'2026-10-05T02:00:00Z','ok':True}))
            calls,intervals=soak.report_pair(p,'soak')
            self.assertEqual(calls,{'0':2,'1':2})
            self.assertEqual(intervals['0'][1]-intervals['0'][0],7200)
    def test_unknown_after_known_preserves_known_sum_and_7200_is_not_enough(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','x']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            child=SimpleNamespace(returncode=0,poll=lambda:0)
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal'),patch.object(soak.subprocess,'Popen',return_value=child),patch.object(soak,'report_pair',side_effect=[({'0':2,'1':2},{'0':[0,7200],'1':[0,7200]}),ValueError('unknown calls')]),patch('builtins.print'):
                self.assertEqual(soak.run(args),3)
            result=soak.read(args.out/'summary.json')
            self.assertEqual(result['known_calls'],{'0':2,'1':2})
            self.assertEqual(result['calls'],{'0':None,'1':None})
            self.assertEqual(len(result['unknown_pairs']),1)
            self.assertEqual(result['required_recorded_span_seconds'],7201)
    def test_model_failure_exit_four_preserved(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','x']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            child=SimpleNamespace(returncode=4,poll=lambda:4)
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal'),patch.object(soak.subprocess,'Popen',return_value=child),patch.object(soak,'report_pair',return_value=({'0':2,'1':2},{'0':[0,100],'1':[0,100]})),patch('builtins.print'):
                self.assertEqual(soak.run(args),4)
            self.assertEqual(soak.read(args.out/'summary.json')['status'],'model_failed')
    def test_sigterm_handler_forwards_to_child_and_cleanup_escalates(self):
        with TemporaryDirectory() as d:
            unit=Path(d)/'unit.json';unit.write_text(json.dumps({'commands':[['python','paired_thinking_workload.py','--arm','soak','--out','x']]}))
            args=SimpleNamespace(unit=unit,unit_sha256=hashlib.sha256(unit.read_bytes()).hexdigest(),out=Path(d)/'out',minimum_seconds=7200,maximum_seconds=8400)
            handlers={}
            class Child:
                pid=321;returncode=None;fired=False;in_handler=False
                def poll(self):
                    if not self.fired:
                        self.fired=True
                        handlers[soak.signal.SIGTERM](soak.signal.SIGTERM,None)
                    return self.returncode
                def wait(self,timeout=None):
                    if timeout is not None:raise soak.subprocess.TimeoutExpired('child',timeout)
                    self.returncode=-9;return -9
            with patch.object(soak,'verify'),patch.object(soak.signal,'signal',side_effect=lambda sig,fn:handlers.update({sig:fn})),patch.object(soak.subprocess,'Popen',return_value=Child()),patch.object(soak.os,'killpg') as kill,patch.object(soak,'report_pair',return_value=({'0':1,'1':1},None)),patch('builtins.print'):
                self.assertEqual(soak.run(args),3)
            self.assertIn(soak.signal.SIGKILL,[call.args[1] for call in kill.call_args_list])
if __name__=='__main__':unittest.main()
