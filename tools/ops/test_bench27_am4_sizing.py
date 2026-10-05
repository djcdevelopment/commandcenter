import importlib.util
import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('sizing', Path(__file__).with_name('bench27_am4_sizing.py'))
sizing = importlib.util.module_from_spec(spec); spec.loader.exec_module(sizing)
spec = importlib.util.spec_from_file_location('helper', '/home/derek/work/commandcenter-linux-flash/tools/ops/bench27_am4_probe.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.log = self.root/'guard.jsonl'; self.trip = Path(str(self.log)+'.tripped')
        self.log.write_text(json.dumps({'utc':datetime.now(timezone.utc).isoformat(), 'gpus':[
            {'pci':'0000:09:00.0','gpu_core_c':70}, {'pci':'0000:0a:00.0','gpu_core_c':70}]})+'\n')
        self.db = self.root/'coord.sqlite'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE capacity_leases(scope TEXT,expires_at REAL)')
        self.posts=[];self.count=24521;self.block=False;self.started=threading.Event();self.unblock=threading.Event()
        self.reasoning='private reasoning'; self.visible='draft'; self.usage_prompt=None; self.usage_completion=5; self.foreign=0; self.window=65536
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                self.send_response(200);self.end_headers()
                if self.path == '/v1/models':
                    self.wfile.write(json.dumps({'data':[{'id':'qwen3-27b','max_model_len':outer.window}]}).encode());return
                self.wfile.write(f'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\nvllm:request_success_total {len(outer.posts)+(outer.foreign if outer.posts else 0)}\n'.encode())
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200);self.end_headers()
                if self.path=='/tokenize':
                    self.wfile.write(json.dumps({'count':outer.count}).encode());return
                outer.posts.append(body);self.wfile.flush();outer.started.set()
                if outer.block: outer.unblock.wait(4)
                try:
                    event={'choices':[{'delta':{'content':outer.visible,'reasoning':outer.reasoning},'finish_reason':'stop'}],
                           'usage':{'prompt_tokens':outer.count if outer.usage_prompt is None else outer.usage_prompt,'completion_tokens':outer.usage_completion}}
                    self.wfile.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode())
                except OSError: pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown);self.addCleanup(self.unblock.set)
        args=SimpleNamespace(coordination_db=self.db,timeout=20,port=self.server.server_port,guard_log=self.log,trip=self.trip)
        self.client=sizing.client_class(helper)(args)
        self.addCleanup(self.close)
        self.body={'messages':[{'role':'user','content':'source'}],'max_tokens':24000,'chat_template_kwargs':{'enable_thinking':True}}
    def close(self):
        self.client.abort('test complete');self.client.closed.set();self.client.release();self.client.watcher.join(1)
    def test_32k_refuses_unchanged_budget(self):
        self.client.expected_prompt=24521
        r=self.client.stream(self.body,self.root/'work',32768)
        self.assertIn('exceeds observed window',r['error']);self.assertEqual(self.client.calls,0)
        self.assertEqual(json.loads((self.root/'work/request.json').read_text())['max_tokens'],24000)
    def test_prompt_mismatch_refuses_without_send(self):
        self.client.expected_prompt=24521;self.count=24522
        with self.assertRaises(helper.InfraError):self.client.stream(self.body,self.root/'work',65536)
        self.assertEqual(self.posts,[])
    def test_admissible_preserves_prompt_and_output_budget(self):
        self.client.expected_prompt=24521
        r=self.client.stream(self.body,self.root/'work',49152)
        self.assertTrue(r['done']);self.assertEqual(self.posts[0]['messages'],self.body['messages'])
        self.assertEqual(self.posts[0]['max_tokens'],24000);self.assertEqual(self.client.calls,1)
    def test_active_lease_blocks_completion(self):
        with sqlite3.connect(self.db) as c:c.execute('INSERT INTO capacity_leases VALUES (?,?)',('provider:am4-vllm',time.time()+60))
        with self.assertRaises(RuntimeError):self.client.stream(self.body,self.root/'work',65536)
        self.assertEqual(self.posts,[])
    def test_missing_guard_cancels_owned_stream(self):
        self.block=True
        with ThreadPoolExecutor(1) as pool:
            f=pool.submit(self.client.stream,self.body,self.root/'work',65536)
            self.assertTrue(self.started.wait(2));self.log.unlink()
            with self.assertRaises(Exception):f.result(timeout=3)
        self.assertEqual(self.client.calls,1)
    def run_args(self):
        manifest={'arm':'fake-reviewed-65k', 'backend':'am4-vllm','recipe':{'window':65536},
                  'argv':['vllm','serve','/home/derek/models/qwen3-27b-gptq-int4','--max-model-len','65536','--reasoning-parser','qwen3']}
        path=self.root/'recipe.json';path.write_text(json.dumps(manifest))
        return SimpleNamespace(helper=Path('/home/derek/work/commandcenter-linux-flash/tools/ops/bench27_am4_probe.py'),
            workload=Path('/home/derek/work/lab-rnd/research/evidence/qwen38-levers-20261003/sizing-workload.json'),
            recipe_manifest=path,recipe_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            out=self.root/'run',port=self.server.server_port,timeout=20,coordination_db=self.db,guard_log=self.log,trip=self.trip)
    def test_whole_run_exact_final_and_foreign_counter_void(self):
        args=self.run_args()
        self.assertEqual(sizing.run(args),0)
        workload=json.loads(args.workload.read_text());final=self.posts[1]
        self.assertEqual(final['max_tokens'],4096)
        self.assertEqual(final['response_format'],workload['final_request_template']['response_format'])
        self.assertEqual(final['messages'],workload['work_request']['messages']+[
            {'role':'assistant','content':'draft'},workload['final_user_message']])
        self.assertNotIn(self.reasoning,json.dumps(final['messages']))
        self.posts.clear();self.foreign=1;args.out=self.root/'void'
        self.assertEqual(sizing.run(args),3)
        summary=json.loads((args.out/'summary.json').read_text())
        self.assertEqual(summary['foreign_requests'],1);self.assertFalse(summary['transport_ok'])
        self.assertEqual(len(summary['measurements']),2)
    def test_reasoning_separation_blocks_final(self):
        for i, (reasoning,visible) in enumerate([('', 'draft'),('reasoning','<think>hidden</think>draft')]):
            args=self.run_args();args.out=self.root/f'reasoning-{i}';self.posts.clear()
            self.reasoning=reasoning;self.visible=visible
            self.assertEqual(sizing.run(args),4);self.assertEqual(len(self.posts),1)
            summary=json.loads((args.out/'summary.json').read_text())
            self.assertIn('recipe mismatch',summary['measurements']['work']['comparison_errors'][0])
    def test_usage_mismatch_and_overbudget_block_final(self):
        for i,(prompt,completion) in enumerate([(24520,5),(24521,24001)]):
            args=self.run_args();args.out=self.root/f'usage-{i}';self.posts.clear()
            self.usage_prompt=prompt;self.usage_completion=completion
            self.assertEqual(sizing.run(args),4);self.assertEqual(len(self.posts),1)
    def test_missing_usage_and_final_budget_validation(self):
        record={'prompt_tokens_exact':24521,'reasoning':'reason','content':'draft'}
        self.assertFalse(sizing.validate_turn(record,24000,work=True))
        record['usage']={'prompt_tokens':24521,'completion_tokens':4097}
        self.assertFalse(sizing.validate_turn(record,4096))
    def test_recipe_pin_and_observed_window(self):
        args=self.run_args();args.recipe_sha256='0'*64
        with self.assertRaises(RuntimeError):sizing.run(args)
        self.assertEqual(self.posts,[])
        args=self.run_args();self.window=49152
        self.assertEqual(sizing.run(args),3);self.assertEqual(self.posts,[])
        original=json.loads(args.recipe_manifest.read_text())
        for field,value in [('backend','wrong'),('argv',['vllm','serve','wrong']),('argv',original['argv'][:-2]),('recipe',{'window':32768})]:
            d={**original,field:value}
            args.recipe_manifest.write_text(json.dumps(d));args.recipe_sha256=hashlib.sha256(args.recipe_manifest.read_bytes()).hexdigest()
            with self.assertRaises(RuntimeError):sizing.run(args)
    def test_metrics_missing_rejected(self):
        with self.assertRaises(RuntimeError):sizing.counters(b'vllm:num_requests_running 0\n')

if __name__=='__main__':unittest.main()
