import importlib.util
import json
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
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'vllm:num_requests_running 0\nvllm:num_requests_waiting 0\nvllm:request_success_total 0\n')
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200);self.end_headers()
                if self.path=='/tokenize':
                    self.wfile.write(json.dumps({'count':outer.count}).encode());return
                outer.posts.append(body);self.wfile.flush();outer.started.set()
                if outer.block: outer.unblock.wait(4)
                try:
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"draft"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
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
    def test_metrics_missing_rejected(self):
        with self.assertRaises(RuntimeError):sizing.counters(b'vllm:num_requests_running 0\n')

if __name__=='__main__':unittest.main()
