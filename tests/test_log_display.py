"""Validate human-readable progress and actual watcher event delivery."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import signal
from scripts import watch_benchmark as watch
from scripts import benchmark_models as bench
from scripts.watch_benchmark import display_width, fit_line, format_progress

class LogTests(unittest.TestCase):
    def test_download_shows_byte_progress_not_response_progress(self):
        with TemporaryDirectory() as temporary:
            partial = Path(temporary)/'model.part'
            partial.write_bytes(b'x'*500)
            text = format_progress({'phase':'downloading','detail':'Example','download_path':str(partial),
                                   'download_bytes':1000,'download_initial_bytes':100,'phase_started_at':0},10)
            self.assertIn('モデルをダウンロード中',text)
            self.assertIn('50.0%',text)
            self.assertIn('MB/秒',text)
            self.assertNotIn('0/280',text)

    def test_live_line_fits_japanese_terminal_width_and_keeps_deadline(self):
        text = '回答待ち | モデルの長い名前 / GPU / 日本語の長い課題名 | 経過123秒 / 時間制限300秒'
        for width in (40,80,120):
            fitted = fit_line(text,width)
            self.assertLessEqual(display_width(fitted),width-1)
            self.assertIn('300秒',fitted)

    def test_non_tty_watcher_preserves_fast_events_and_final_message(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'progress.json'
            progress = bench.ProgressReporter(100,path)
            with bench.progress_monitor(progress):
                for number in range(100):
                    progress.log(f'EVENT-{number}')
                progress.update('finished','FINAL')
            # Launch a second reader over the exact captured queue, redirected to a file.
            result = subprocess.run([sys.executable,str(bench.ROOT/'scripts/watch_benchmark.py'),
                                     '--progress-file',str(path)],capture_output=True,text=True,timeout=3)
            for number in range(100):
                self.assertEqual(result.stdout.splitlines().count(f'EVENT-{number}'),1)
            self.assertIn('[終了] FINAL',result.stdout)
            self.assertNotIn('\x1b',result.stdout)
            self.assertNotIn('\r',result.stdout)
    def test_finished_published_between_event_read_and_state_read_is_drained(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary)/'progress.json'
            events = path.with_suffix('.events.jsonl')
            events.write_text(json.dumps({'message':'START'})+'\n')
            path.write_text('{}')
            def read(*a,**k):
                with events.open('a') as output:
                    output.write(json.dumps({'message':'FINAL-RACE'})+'\n')
                return '{"phase":"finished"}'
            captured = io.StringIO()
            with patch.object(sys,'argv',['watch','--progress-file',str(path)]), \
                 patch.object(Path,'read_text',side_effect=read), redirect_stdout(captured):
                watch.main()
            self.assertEqual(captured.getvalue().splitlines(),['START','FINAL-RACE'])

    def test_completed_download_rename_does_not_crash_watcher(self):
        with patch.object(Path,'stat',side_effect=FileNotFoundError):
            result = format_progress({'phase':'downloading','download_path':'gone.part','download_bytes':1000},1)
        self.assertIn('モデルをダウンロード中',result)

    def test_resume_replaces_count_without_exceeding_total(self):
        reporter = bench.ProgressReporter(1)
        row = {'model_id':'m','device':'cpu','question_id':'q','repeat':1,'status':'timeout'}
        reporter.seed([row])
        with redirect_stdout(io.StringIO()) as output:
            reporter.advance({**row,'status':'ok','attempt':2})
        self.assertEqual(reporter.state['completed'],1)
        self.assertEqual(reporter.state['succeeded'],1)
        self.assertEqual(reporter.state['failed'],0)
        self.assertIn('[結果 1/1]',output.getvalue())


if __name__ == '__main__': unittest.main()
