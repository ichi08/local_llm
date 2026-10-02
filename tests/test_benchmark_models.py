"""Protocol/timing tests with simulated responses, never model benchmarks."""

import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import benchmark_models as bench
from scripts.watch_benchmark import format_progress


class Clock:
    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds


class Response:
    def __init__(self, clock, events=None, data=None):
        self.clock = clock
        self.events = events or []
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.data).encode()

    def __iter__(self):
        for delay, event in self.events:
            self.clock.advance(delay)
            payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
            yield ("data: " + payload + "\n\n").encode("utf-8")


class Transport:
    def __init__(self, clock, *, finish="stop", done=True, cache_n=0):
        self.clock, self.finish, self.done, self.cache_n = clock, finish, done, cache_n
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if "/slots/0?action=erase" in request.full_url:
            self.clock.advance(10)  # deliberately long, must not affect API times
            return Response(self.clock, data={"id_slot": 0, "n_erased": 20})
        self.clock.advance(0.1)  # HTTP connection overhead belongs in API timing
        events = [
            (0.1, {"choices": [{"delta": {"role": "assistant"}}]}),
            (0.2, {"choices": [{"delta": {"reasoning_content": "検討中"}}]}),
            (0.6, {"choices": [{"delta": {"content": "答えです"}}]}),
            (0.1, {"choices": [{"delta": {}, "finish_reason": self.finish}],
                   "usage": {"completion_tokens": 12},
                   "timings": {"cache_n": self.cache_n, "predicted_per_second": 30}}),
        ]
        if self.done:
            events.append((0.1, "[DONE]"))
        return Response(self.clock, events=events)


class BenchmarkTests(unittest.TestCase):
    def local_model(self, data=b"small test weights"):
        return {"id": "cached", "name": "cached model",
                "url": "https://huggingface.co/example/model/resolve/pinned/model.gguf",
                "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def test_download_reuses_verified_project_weight_without_network(self):
        data = b"small test weights"
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "model.gguf").write_bytes(data)
            with patch.object(bench.subprocess, "run", side_effect=AssertionError("must not download")), \
                 redirect_stdout(io.StringIO()):
                bench.download_models([self.local_model(data)], directory)

    def test_hugging_face_blob_is_reused_in_place_without_download_or_copy(self):
        data = b"small test weights"
        model = self.local_model(data)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "hub"
            blob = cache / "models--example--model/blobs" / model["sha256"]
            blob.parent.mkdir(parents=True)
            blob.write_bytes(data)
            project_models = root / "models"
            with patch.object(bench, "default_cache_dirs", return_value=[cache]), \
                 patch.object(bench.subprocess, "run", side_effect=AssertionError("must not download")), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(bench.find_local_model(model, project_models), blob)
                bench.download_models([model], project_models)
            self.assertFalse((project_models / "model.gguf").exists())
            self.assertEqual(blob.read_bytes(), data)

    def test_same_filename_and_size_with_wrong_hash_is_not_reused(self):
        data = b"correct"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            for name, content in (("a", b"invalid"), ("b", data)):
                folder = cache / name
                folder.mkdir(parents=True)
                (folder / "model.gguf").write_bytes(content)
            with patch.object(bench, "default_cache_dirs", return_value=[]):
                selected = bench.find_local_model(self.local_model(data), root / "models", [cache])
            self.assertEqual(selected, cache / "b/model.gguf")

    def measure(self, **transport_options):
        clock = Clock()
        transport = Transport(clock, **transport_options)
        with patch.object(bench, "HTTP", transport), patch.object(bench.time, "perf_counter", lambda: clock.now):
            result = bench.measure_response("http://127.0.0.1:1", "test-model", "質問です")
        return result, transport

    def test_stream_distinguishes_reasoning_first_answer_and_completion(self):
        result, transport = self.measure()
        self.assertAlmostEqual(result["ttft_seconds"], 0.4)
        self.assertAlmostEqual(result["ttfa_seconds"], 1.0)
        self.assertAlmostEqual(result["total_seconds"], 1.2)
        self.assertEqual(result["answer"], "答えです")
        self.assertEqual(result["reasoning"], "検討中")
        self.assertEqual(result["usage"]["completion_tokens"], 12)
        self.assertEqual(result["generation_tokens_per_second"], 30)
        sent = json.loads(transport.requests[0].data)
        self.assertFalse(sent["cache_prompt"])
        self.assertEqual(sent["messages"], [{"role": "user", "content": "質問です"}])

    def test_incomplete_stream_is_not_a_successful_fast_answer(self):
        with self.assertRaisesRegex(ValueError, "切断"):
            self.measure(done=False)

    def test_cached_prompt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "再利用"):
            self.measure(cache_n=3)

    def test_truncated_response_is_saved_but_excluded_from_completed_median(self):
        short, _ = self.measure(finish="length")
        self.assertEqual(short["status"], "truncated")
        rows = [dict(model_id="m", device="cpu", question_id="q", **short),
                {"model_id": "m", "device": "cpu", "question_id": "q", "status": "ok", "total_seconds": 8}]
        summary = bench.summarize(rows)[0]
        self.assertEqual(summary["completed_answers"], 1)
        self.assertEqual(summary["failures_or_truncations"], 1)
        self.assertEqual(summary["median_total_seconds"], 8)
        self.assertEqual(summary["mean_total_seconds"], 8)
        self.assertIsNone(summary["stddev_total_seconds"])

    def test_five_completed_times_have_stddev_and_keep_raw_values(self):
        rows = [{"model_id": "m", "device": "cpu", "question_id": "q", "status": "ok", "total_seconds": value}
                for value in (1, 2, 3, 4, 10)]
        rows.append({"model_id": "m", "device": "cpu", "question_id": "q", "status": "truncated", "total_seconds": 100})
        summary = bench.summarize(rows)[0]
        self.assertEqual(summary["values_total_seconds"], [1, 2, 3, 4, 10])
        self.assertEqual(summary["n_total_seconds"], 5)
        self.assertEqual(summary["median_total_seconds"], 3)
        self.assertEqual(summary["mean_total_seconds"], 4)
        self.assertEqual(summary["stddev_ddof"], 1)
        self.assertAlmostEqual(summary["stddev_total_seconds"], 12.5 ** 0.5)
        self.assertFalse(any("variance" in key for key in summary))
        self.assertEqual(summary["failures_or_truncations"], 1)

    def test_load_switch_warmup_and_erase_do_not_enter_api_measurements(self):
        clock = Clock()
        transport = Transport(clock)
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            models = []
            for name in ("model-a", "model-b"):
                data = name.encode()
                (directory / (name + ".gguf")).write_bytes(data)
                models.append({"id": name, "name": name, "url": "https://example.test/" + name + ".gguf",
                               "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "cpu_test": True, "thinking_switch": True})
            @contextmanager
            def fake_load(*args, **kwargs):
                clock.advance(1000)
                yield "http://127.0.0.1:1", {"offloaded_layers": 0}
                clock.advance(500)
            args = argparse.Namespace(thinking="off", model_dir=directory, output_dir=directory / "results",
                                      server_bin=None, gpu_device=None, devices=["cpu"], all_cpu=False,
                                      port=8081, context_size=4096, threads=8, repeats=2, max_tokens=128, timeout=300)
            with patch.object(bench, "HTTP", transport), patch.object(bench.time, "perf_counter", lambda: clock.now), \
                 patch.object(bench, "load_model", fake_load), patch.object(bench, "server_prefix", return_value=[sys.executable]), \
                 redirect_stdout(io.StringIO()):
                result_dir = bench.run_benchmark(models, [{"id": "q", "prompt": "質問", "category": "テスト"}], args)
            rows = [json.loads(line) for line in (result_dir / "results.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 4)  # warmup responses must not be saved as samples
            self.assertGreater(clock.now, 3000)
            for row in rows:
                self.assertAlmostEqual(row["total_seconds"], 1.2)
            requests = [json.loads(r.data) for r in transport.requests if "/chat/completions" in r.full_url]
            self.assertEqual(len(requests), 6)  # two warmups plus four measured calls

    def test_gpu_fallback_is_rejected_and_own_process_is_stopped(self):
        process = Mock()
        process.poll.return_value = None
        with TemporaryDirectory() as temporary:
            with patch.object(bench.subprocess, "Popen", return_value=process), \
                 patch.object(bench, "api_json", side_effect=[{"status": "ok"}, {"data": [{"id": "m"}]}]):
                with self.assertRaisesRegex(ValueError, "GPUへのレイヤー配置"):
                    with bench.load_model(["fake-server"], {"id": "m"}, Path("m.gguf"), "gpu", Path(temporary),
                                          port=0, context_size=4096, threads=8, gpu="Metal"):
                        self.fail("CPU fallback must not reach measurement")
            process.terminate.assert_called_once()

    def test_argon_inputs_are_balanced_and_source_text_reaches_the_prompt(self):
        suite, questions = bench.load_question_suite(bench.DEFAULT_QUESTION_FILE)
        self.assertEqual({category: sum(q["category"] == category for q in questions)
                          for category in ("要約", "翻訳", "解説", "自由創作")},
                         {"要約": 2, "翻訳": 2, "解説": 2, "自由創作": 2})
        self.assertIn(suite["materials"]["overview"], questions[0]["prompt"])
        self.assertTrue(all("120字" not in q["prompt"] and "140字" not in q["prompt"] for q in questions))

    def test_progress_updates_stay_outside_response_timing_and_report_errors(self):
        clock = Clock()
        transport = Transport(clock)
        with TemporaryDirectory() as temporary:
            progress = bench.ProgressReporter(1, Path(temporary) / "progress.json")
            original_update = progress.update
            def slow_update(*args, **kwargs):
                clock.advance(50)
                original_update(*args, **kwargs)
            progress.update = slow_update
            with patch.object(bench, "HTTP", transport), patch.object(bench.time, "perf_counter", lambda: clock.now):
                rows = list(bench.benchmark_loaded_model("http://127.0.0.1:1", {"id": "m", "name": "model"},
                            [{"id": "q", "category": "要約", "prompt": "質問"}], repeats=1,
                            max_tokens=100, thinking="auto", timeout=300, progress=progress, device="cpu"))
            self.assertAlmostEqual(rows[0]["total_seconds"], 1.2)
            progress.advance(dict(model_id="m", device="cpu", **rows[0]))
            state = json.loads(progress.path.read_text())
            self.assertEqual(state["completed"], 1)
            self.assertEqual(state["succeeded"], 1)
            displayed = format_progress(state, state["phase_started_at"] + 12)
            self.assertIn("1/1 100.0%", displayed)
            self.assertIn("経過12秒", displayed)

    def test_unattempted_calls_are_reported_separately_from_failed_attempts(self):
        rows = [{"model_id": "m", "device": "cpu", "question_id": "q", "status": "error"},
                {"model_id": "m", "device": "cpu", "question_id": "q", "status": "not_run"}]
        summary = bench.summarize(rows)[0]
        self.assertEqual(summary["attempted_samples"], 1)
        self.assertEqual(summary["failures_or_truncations"], 1)
        self.assertEqual(summary["not_run_samples"], 1)
        self.assertIsNone(summary["mean_total_seconds"])

    def test_all_prepares_runtime_and_weights_before_running(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "results.jsonl").write_text('{"status":"ok"}\n')
            calls = []
            with patch.object(sys, "argv", ["benchmark_models.py", "all", "--models", "minicpm5-1b", "--devices", "cpu", "--no-progress", "--progress-file", str(root / "progress.json")]), \
                 patch.object(bench.platform, "system", return_value="Linux"), \
                 patch.object(bench, "ensure_runtime", side_effect=lambda *a: calls.append("runtime") or ["fake"]), \
                 patch.object(bench, "download_models", side_effect=lambda *a: calls.append("weights")), \
                 patch.object(bench, "run_benchmark", side_effect=lambda *a: calls.append("run") or root), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(bench.main(), 0)
            self.assertEqual(calls, ["runtime", "weights", "run"])
            self.assertEqual(json.loads((root / "progress.json").read_text())["total"], 40)

    def test_failed_model_records_remaining_calls_and_continues_next_model(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            models = []
            for name in ("bad", "good"):
                content = name.encode()
                (root / f"{name}.gguf").write_bytes(content)
                models.append({"id": name, "name": name, "url": f"https://example.test/{name}.gguf",
                               "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                               "cpu_test": True, "thinking_switch": True})
            @contextmanager
            def fake_load(prefix, model, *args, **kwargs):
                if model["id"] == "bad":
                    raise ValueError("起動できません")
                yield "http://127.0.0.1:1", {"offloaded_layers": 0}
            args = argparse.Namespace(thinking="auto", model_dir=root, output_dir=root / "results",
                                      server_bin=None, gpu_device=None, devices=["cpu"], all_cpu=False,
                                      port=8081, context_size=4096, threads=8, repeats=5, max_tokens=2048, timeout=300)
            progress = bench.ProgressReporter(10)
            with patch.object(bench, "load_model", fake_load), patch.object(bench, "clear_prompt_cache"), \
                 patch.object(bench, "measure_response", return_value={"status": "ok", "total_seconds": 1, "answer": "回答"}) as measure, \
                 patch.object(bench, "server_prefix", return_value=[sys.executable]), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result_dir = bench.run_benchmark(models, [{"id": "q", "category": "要約", "prompt": "質問"}], args, progress)
            rows = [json.loads(line) for line in (result_dir / "results.jsonl").read_text().splitlines()]
            self.assertEqual(sum(row["status"] == "setup_error" for row in rows), 1)
            self.assertEqual(sum(row["status"] == "not_run" for row in rows), 5)
            self.assertEqual(sum(row["status"] == "ok" for row in rows), 5)
            self.assertEqual(measure.call_count, 6)  # good model: warmup + five measured responses
            self.assertEqual(progress.state["completed"], 10)
            self.assertEqual(progress.state["setup_failures"], 1)
            self.assertTrue((result_dir / "answers.md").is_file())

    def test_xhigh_and_vendor_sampling_are_sent_to_the_api(self):
        clock = Clock()
        transport = Transport(clock)
        with patch.object(bench, "HTTP", transport), patch.object(bench.time, "perf_counter", lambda: clock.now):
            bench.measure_response("http://127.0.0.1:1", "qwen38", "質問", reasoning_effort="xhigh",
                                   sampling={"temperature": 1.0, "top_p": 0.95})
        sent = json.loads(transport.requests[0].data)
        self.assertEqual(sent["reasoning_effort"], "xhigh")
        self.assertEqual(sent["chat_template_kwargs"]["reasoning_effort"], "xhigh")
        self.assertTrue(sent["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(sent["temperature"], 1.0)

    def test_gpu_is_discovered_from_llama_cpp_device_inventory(self):
        inventory = "Available devices:\n  Metal: Apple M1 Pro (16384 MiB, 10240 MiB free)\n"
        result = Mock(stdout=inventory, stderr="")
        with patch.object(bench.subprocess, "run", return_value=result):
            device, recorded = bench.select_gpu(["llama-server"], None)
        self.assertEqual(device, "Metal")
        self.assertIn("Apple M1 Pro", recorded)

    def test_plan_preserves_requested_model_order_without_loading(self):
        captured = io.StringIO()
        with patch.object(sys, "argv", ["benchmark_models.py", "plan", "--models", "qwen3.5-2b", "minicpm5-1b"]), \
             patch.object(bench, "load_model", side_effect=AssertionError("plan must not load")), \
             patch.object(bench, "download_models", side_effect=AssertionError("plan must not download")), \
             redirect_stdout(captured):
            self.assertEqual(bench.main(), 0)
        self.assertEqual([model["id"] for model in json.loads(captured.getvalue())["models"]], ["qwen3.5-2b", "minicpm5-1b"])


if __name__ == "__main__":
    unittest.main()
