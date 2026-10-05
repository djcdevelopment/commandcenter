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
if __name__=='__main__':unittest.main()
