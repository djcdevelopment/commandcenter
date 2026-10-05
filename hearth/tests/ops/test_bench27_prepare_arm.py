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

if __name__=='__main__': unittest.main()
