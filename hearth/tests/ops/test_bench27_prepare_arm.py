import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from tools.ops.bench27_prepare_arm import prepare, speculative_intervals

class ArmPreparationTests(unittest.TestCase):
    def test_partial_counter_reset_and_absent_treatment(self):
        def sample(t, value):
            return {'utc': t, 'seats': {'0': 'vllm:spec_decode_num_drafts_total{engine="0"} '+str(value), '1': '# no speculative counters'}}
        with TemporaryDirectory() as directory:
            path=Path(directory)/'raw.ndjson'
            path.write_text('\n'.join(json.dumps(x) for x in [sample('t1',10),sample('t2',15),sample('t3',2),sample('t4',4)]))
            got=speculative_intervals(path)['seats']
            self.assertEqual([list(x['deltas'].values()) for x in got['0']['segments']], [[5.0],[2.0]])
            self.assertEqual(got['1']['segments'], [])
            self.assertEqual(got['0']['segments'][1]['first_utc'], 't3')

    def test_refuses_existing_or_nested_output_before_read(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for output in [root,root/'new']:
                with self.assertRaisesRegex(ValueError,'new directory outside'):
                    prepare(SimpleNamespace(root=root,out=output))


class BindingComparisonTests(unittest.TestCase):
    def rows(self):
        return [{'seat':seat,'arm':'pass-a','workload':{'sha256':'hash'},'timing_exclusions':[],
                 'conversations':[{'work':{'controls':{'temperature':0,'seed':42,'top_p':.95,'max_tokens':24000,'chat_template_kwargs':{'enable_thinking':True}}},'final':{'controls':{'max_tokens':4096,'chat_template_kwargs':{'enable_thinking':False}}}}],
                 **{k:value for k in ('seconds_total','work_seconds','final_seconds','decode_tokens_per_s')}}
                for seat,value in [('seat-0',10),('seat-1',20)] for _ in range(3)]
    def parity(self):
        from tools.ops.bench27_summarize import METRICS
        return {'runs':self.rows(),'calibration':{'reasons':[],'metrics':{k:{'ready':True,'threshold':.1} for k in METRICS}}}
    def test_orientation_and_unknown_seat(self):
        from tools.ops.bench27_prepare_arm import compare,validate_binding
        rows=self.rows();p=self.parity()
        self.assertEqual(compare(rows,p,'seat-0','seat-1')['seconds_total']['treatment_over_control_minus_one'],1)
        self.assertEqual(compare(rows,p,'seat-1','seat-0')['seconds_total']['treatment_over_control_minus_one'],-.5)
        with self.assertRaisesRegex(ValueError,'observed seats'):validate_binding(rows,'seat0','seat-1','hash')
        self.assertFalse(validate_binding(rows,'seat-0','seat-1','hash')['verified'])
    def test_harness_and_workload_binding(self):
        from tools.ops.bench27_prepare_arm import validate_binding
        rows=self.rows();state={'spec':{'id':'pass-a','dropin':{'0':None,'1':'treatment.conf'}}}
        self.assertTrue(validate_binding(rows,'seat-0','seat-1','hash',state)['verified'])
        self.assertEqual(validate_binding(rows,'seat-0','seat-1','hash',state)['experiment_id'],'pass-a')
        wrong={'spec':{'id':'pass-b','dropin':state['spec']['dropin']}}
        with self.assertRaisesRegex(ValueError,'spec.id'):validate_binding(rows,'seat-0','seat-1','hash',wrong)
        with self.assertRaisesRegex(ValueError,'dropin'):validate_binding(rows,'seat-1','seat-0','hash',state)
        with self.assertRaisesRegex(ValueError,'workload hash'):validate_binding(rows,'seat-0','seat-1','wrong',state)
    def test_null_threshold_and_nonfinite_metric(self):
        from tools.ops.bench27_prepare_arm import compare
        rows=self.rows();p=self.parity();p['calibration']['metrics']['seconds_total'].update(threshold=None,ready=False)
        rows[0]['decode_tokens_per_s']=None;rows[1]['decode_tokens_per_s']=float('nan')
        result=compare(rows,p,'seat-0','seat-1')
        self.assertIsNone(result['seconds_total']['exceeds_frozen_spread'])
        self.assertEqual(result['decode_tokens_per_s']['seats']['seat-0']['n'],1)
        self.assertIsNone(result['decode_tokens_per_s']['exceeds_frozen_spread'])
    def test_incompatible_generation_or_workload(self):
        from tools.ops.bench27_prepare_arm import compare
        for change in ('workload','temperature'):
            rows=self.rows()
            if change=='workload':rows[0]['workload']['sha256']='other'
            else:rows[0]['conversations'][0]['work']['controls']['temperature']=.5
            result=compare(rows,self.parity(),'seat-0','seat-1')
            self.assertFalse(result['seconds_total']['comparable'])
            self.assertIsNone(result['seconds_total']['exceeds_frozen_spread'])
    def test_exclusion_validation(self):
        from tools.ops.bench27_prepare_arm import pairs
        for values in [['rep-1='],['rep-1=cold','rep-1=again'],['=reason']]:
            with self.assertRaises(ValueError):pairs(values)
    def test_gap_splits_even_if_counter_is_higher_after_gap(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'raw.ndjson'
            def sample(t,value):return {'utc':t,'seats':{'0':value}}
            counter='vllm:spec_decode_num_drafts_total{engine="0"} '
            path.write_text('\n'.join(json.dumps(x) for x in [sample('a',counter+'10'),sample('b',{'error':'503'}),sample('c',counter+'100')]))
            result=speculative_intervals(path)['seats']['0']
            self.assertEqual(len(result['segments']),2)
            self.assertEqual([list(s['deltas'].values()) for s in result['segments']],[[0.0],[0.0]])

    def test_prepare_writes_bound_package_end_to_end(self):
        from unittest.mock import patch
        from tools.ops.bench27_prepare_arm import prepare
        from tools.ops.bench27_summarize import digest
        with TemporaryDirectory() as directory:
            base=Path(directory);root=base/'input';root.mkdir();out=base/'output'
            workload=base/'workload.json';pin='a'*40
            workload.write_text(json.dumps({'source_commit':pin,'brief':{'sources':[{'path':'source.py','commit':pin}]}}))
            rows=self.rows()
            for i,row in enumerate(rows):
                row['path']=f'rep-{i}/'+row['seat']+'/run.json'
                row['workload']['sha256']=digest(workload.read_bytes())
            parity=self.parity();parity.update(schema='bench27-summary.v1',runs=rows)
            paritypath=base/'parity.json';paritypath.write_text(json.dumps(parity))
            harness=base/'harness.json';harness.write_text(json.dumps({'spec':{'id':'pass-a','dropin':{'0':None,'1':'t.conf'}}}))
            args=SimpleNamespace(root=root,out=out,control='seat-0',treatment='seat-1',workload=workload,
                parity=paritypath,harness=harness,cards=[],exclude_repeat=[],card_map=[],raw_metrics=None,
                needle_grades=None,source_repo=base)
            collected={'runs':rows,'calibration':{},'reference_run':rows[0]['path'],'card_map':{'seat-0':'card2','seat-1':'card3'}}
            with patch('tools.ops.bench27_prepare_arm.summary.summarize',return_value=collected), patch('tools.ops.bench27_prepare_arm.sourcemap.build',return_value=SimpleNamespace(commit=pin,files=[])), patch('tools.ops.bench27_prepare_arm.sourcemap.render_for_model',return_value='pinned sources'):
                result=prepare(args)
            self.assertEqual(result['assignment']['experiment_id'],'pass-a')
            self.assertTrue(result['assignment']['verified'])
            self.assertTrue(result['comparison']['seconds_total']['exceeds_frozen_spread'])
            marker=json.loads((out/'COMPLETE.json').read_text())
            self.assertEqual(marker['metrics_sha256'],digest((out/'metrics.json').read_bytes()))
            self.assertFalse((out/'blind'/'blind-map.json').exists())
            self.assertEqual(len(json.loads((out/'blind-map.json').read_text())),6)

if __name__=='__main__': unittest.main()
