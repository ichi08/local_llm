"""Failure/restart protocol tests, including a real blocked socket read."""
import argparse
from contextlib import contextmanager, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from scripts import benchmark_models as bench
from scripts import page_inputs
from scripts.benchmark_reports import latest_rows, write_reports
from test_benchmark_models import Clock, Transport, Response


class ReliabilityTests(unittest.TestCase):
    def test_total_deadline_interrupts_blocked_stream_and_keeps_partial_answer(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
                self.wfile.flush()
                time.sleep(.8)
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            start = time.monotonic()
            result = bench.measure_response(f'http://127.0.0.1:{server.server_port}','m','q',timeout=.25,idle_timeout=2,connect_timeout=2)
            self.assertEqual(result['status'],'timeout')
            self.assertEqual(result['answer'],'partial')
            self.assertLess(time.monotonic()-start,.7)
        finally:
            server.shutdown()
            server.server_close()

    def test_idle_deadline_separate_from_total(self):
        class TransportIdle:
            def open(self,*a,**k):
                class Hanging(Response):
                    def __iter__(self):
                        time.sleep(1)
                        return iter([])
                return Hanging(Clock())
        with patch.object(bench,'HTTP',TransportIdle()):
            result = bench.measure_response('http://127.0.0.1:1','m','q',timeout=3,idle_timeout=.1,connect_timeout=1)
        self.assertEqual(result['status'],'timeout')
        self.assertIn('無通信',result['error'])

    def test_cancel_saves_reasoning_and_answer(self):
        class CancelTransport(Transport):
            def open(self,*a,**k):
                response = super().open(*a,**k)
                class CancelResponse(Response):
                    def __iter__(self):
                        yield from super().__iter__()
                        raise KeyboardInterrupt
                return CancelResponse(response.clock,response.events[:3])
        clock = Clock()
        with patch.object(bench,'HTTP',CancelTransport(clock)):
            result = bench.measure_response('http://127.0.0.1:1','m','q')
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['answer'],'答えです')
        self.assertEqual(result['reasoning'],'検討中')

    def test_oversize_input_never_calls_generation(self):
        info = {'input_tokens':5000,'effective_context_tokens':4096,'available_output_tokens':0}
        with patch.object(bench,'preflight_input',return_value=info), patch.object(bench,'clear_prompt_cache'), \
             patch.object(bench,'measure_response',return_value={'status':'ok'}) as measure, redirect_stdout(io.StringIO()):
            rows = list(bench.benchmark_loaded_model('local',{'id':'m','name':'M'},[{'id':'q','category':'要約','prompt':'FULL-PAGE-END'}],
                        repeats=1,max_tokens=-1,thinking='auto',timeout=1))
        self.assertEqual(measure.call_count,1)  # warmup only
        self.assertEqual(rows[0]['status'],'input_too_long')
        self.assertEqual(rows[0]['prompt'],'FULL-PAGE-END')

    def test_resume_skips_completed_and_retains_failed_attempts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = b'weights'
            (root/'m.gguf').write_bytes(data)
            model = {'id':'m','name':'M','url':'https://example.test/m.gguf','size_bytes':len(data),
                     'sha256':bench.sha256_file(root/'m.gguf'),'cpu_test':True,'thinking_switch':True}
            suite = root/'suite.json'
            questions = [{'id':'a','category':'要約','prompt':'A FULL TAIL'},{'id':'b','category':'要約','prompt':'B FULL TAIL'}]
            suite.write_text(json.dumps({'questions':questions}))
            args = argparse.Namespace(thinking='auto',model_dir=root,output_dir=root/'results',questions_file=suite,
                    server_bin=None,gpu_device=None,devices=['cpu'],all_cpu=False,port=8081,context_size=16384,
                    threads=8,repeats=1,max_tokens=-1,timeout=3,resume=None)
            @contextmanager
            def loaded(*a,**k): yield 'local',{'offloaded_layers':0}
            result = {'status':'ok','answer':'ANSWER','total_seconds':1}
            with patch.object(bench,'load_model',loaded), patch.object(bench,'server_prefix',return_value=[sys.executable]), \
                 patch.object(bench,'clear_prompt_cache'), patch.object(bench,'preflight_input',return_value={
                    'input_tokens':3,'effective_context_tokens':16384,'available_output_tokens':16381}), \
                 redirect_stdout(io.StringIO()):
                with patch.object(bench,'measure_response',side_effect=[result,result,{'status':'timeout','answer':'PARTIAL','total_seconds':3}]):
                    directory = bench.run_benchmark([model],questions,args)
                args.resume = directory
                with patch.object(bench,'measure_response',return_value=result) as measure:
                    bench.run_benchmark([model],questions,args)
                self.assertEqual(measure.call_count,2)  # warmup + failed B only
                rows = [json.loads(line) for line in (directory/'results.jsonl').read_text().splitlines()]
                self.assertEqual(len(rows),3)
                self.assertEqual(rows[1]['answer'],'PARTIAL')
                self.assertEqual(rows[2]['attempt'],2)
                self.assertTrue(all(r['status']=='ok' for r in latest_rows(rows)))
                args.context_size = 8192
                with self.assertRaisesRegex(ValueError,'再開条件'):
                    bench.run_benchmark([model],questions,args)

    def test_conversation_sends_history_and_measures_turns_separately(self):
        clock, transport = Clock(), Transport(Clock())
        with patch.object(bench,'HTTP',transport), redirect_stdout(io.StringIO()):
            rows = list(bench.benchmark_loaded_model('http://local',{'id':'m','name':'M'},
                    [{'id':'q','category':'要約','prompt':'FULL PAGE TAIL','followups':['修正してください']}],
                    repeats=1,max_tokens=-1,thinking='auto',timeout=3))
        sent = [json.loads(r.data) for r in transport.requests if '/chat/completions' in r.full_url]
        self.assertEqual(len(rows),2)
        self.assertEqual(sent[-1]['messages'],[{'role':'user','content':'FULL PAGE TAIL'},
                         {'role':'assistant','content':'答えです'},{'role':'user','content':'修正してください'}])
        self.assertEqual(rows[-1]['measurement_mode'],'conversation')
        self.assertEqual(rows[-1]['turn'],2)

    def test_report_escapes_model_content_and_preserves_human_review(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            row = {'model_id':'m','device':'cpu','question_id':'q','repeat':1,'status':'ok','answer':'<script>alert(1)</script>'}
            write_reports(directory,[row],{})
            report = (directory/'report.html').read_text()
            self.assertIn('&lt;script&gt;',report)
            self.assertNotIn('<script>alert(1)',report)
            review = json.loads((directory/'quality-review.json').read_text())
            review['reviews'][0]['task'] = 2
            (directory/'quality-review.json').write_text(json.dumps(review))
            write_reports(directory,[row],{})
            self.assertEqual(json.loads((directory/'quality-review.json').read_text())['reviews'][0]['task'],2)

    def test_rendered_capture_keeps_dynamic_noise_and_reuses_without_browser(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw = b'<html><head><title>dynamic</title></head><body><nav>MENU MENU</nav><p>JS TEXT</p><footer>TAIL</footer></body></html>'
            with patch.object(page_inputs,'fetch_rendered_html',return_value=(raw,'https://example.test','utf-8',{'version':'test'})):
                text, info = page_inputs.capture_page('https://example.test',directory,method='browser')
            self.assertIn('MENU MENU',text)
            self.assertTrue(text.endswith('TAIL\n'))
            with patch.object(page_inputs,'fetch_rendered_html',side_effect=AssertionError('no network')):
                self.assertEqual(page_inputs.capture_page('https://example.test',directory,method='browser')[0],text)
            with self.assertRaisesRegex(ValueError,'方式'):
                page_inputs.read_snapshot(directory,'https://example.test',method='static')

if __name__ == '__main__': unittest.main()
