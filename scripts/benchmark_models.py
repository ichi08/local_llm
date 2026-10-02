#!/usr/bin/env python3
"""Benchmark local HTTP responses; model setup stays outside the timer."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

# Direct URLs pinned to Hugging Face revisions. Metadata checked 2026-10-03.
MODEL_URLS = [
    {
        "id": "minicpm5-1b", "name": "MiniCPM5-1B", "cpu_test": True, "quantization": "Q4_K_M",
        "url": "https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/resolve/3d55fac80935ae6456986ad2384b5cbcc4d6c948/MiniCPM5-1B-Q4_K_M.gguf",
        "size_bytes": 688065920,
        "sha256": "81b64d05a23b17b34c475f42b3e72fbde62d4b92cc34541f7a8031d0752deafa",
        "license": "Apache-2.0", "thinking_switch": True,
    },
    {
        "id": "qwen3.5-2b", "name": "Qwen3.5-2B", "cpu_test": True, "quantization": "Q4_K_M",
        "url": "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf",
        "size_bytes": 1280835840,
        "sha256": "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
        "license": "Apache-2.0", "thinking_switch": True,
    },
    {
        "id": "lfm2.5-2.6b", "name": "LFM2.5-2.6B", "cpu_test": True, "quantization": "Q4_K_M",
        "url": "https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/e7caca5d835a3901a8e0d63e94009429bafafdfc/LFM2.5-2.6B-Q4_K_M.gguf",
        "size_bytes": 1674455040,
        "sha256": "02a8b7e17487d326e46d68ce0ba24211e1b80a14c4cd0597fa73c1cd697f52ed",
        "license": "LFM Open License v1.0 (see upstream LICENSE)", "thinking_switch": False,
    },
    {
        "id": "qwen3.5-9b", "name": "Qwen3.5-9B", "cpu_test": False, "quantization": "Q4_K_M",
        "url": "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf",
        "size_bytes": 5680522464,
        "sha256": "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8",
        "license": "Apache-2.0", "thinking_switch": True,
    },
    {
        "id": "qwen3.8-27b-iq2-s", "name": "Qwen3.8-27B (xhigh / IQ2_S)",
        "cpu_test": False, "default": False, "quantization": "UD-IQ2_S",
        "url": "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/4ca720788d1e01f1bff70c033e0d0028fd02e502/Qwen3.8-27B-UD-IQ2_S.gguf",
        "size_bytes": 8371970048,
        "sha256": "7897d2c5a5cee46aef50895141b2c8a0803c1185f3d03c4fda4cd137a7ad77fe",
        "license": "Apache-2.0", "thinking_switch": True, "reasoning_effort": "xhigh",
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
                     "presence_penalty": 0.0, "repeat_penalty": 1.0},
    },
    {
        "id": "qwen3.8-27b-iq3-xxs", "name": "Qwen3.8-27B (xhigh / IQ3_XXS)",
        "cpu_test": False, "default": False, "quantization": "UD-IQ3_XXS",
        "url": "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/4ca720788d1e01f1bff70c033e0d0028fd02e502/Qwen3.8-27B-UD-IQ3_XXS.gguf",
        "size_bytes": 10934860704,
        "sha256": "c0b7c3038681ed2e3040456c1dd45f9858b6c2290bed172c70388a94874f3eee",
        "license": "Apache-2.0", "thinking_switch": True, "reasoning_effort": "xhigh",
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
                     "presence_penalty": 0.0, "repeat_penalty": 1.0},
    },
]

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTION_FILE = ROOT / "benchmarks/google_argon.json"


def load_question_suite(path: Path) -> tuple[dict, list[dict]]:
    """Keep source text and evaluation hints editable without changing code."""
    suite = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict) or not isinstance(suite.get("questions"), list) or not suite["questions"]:
        raise ValueError("入力JSONには空でないquestionsリストが必要です。")
    materials = suite.get("materials", {})
    if not isinstance(materials, dict):
        raise ValueError("materialsはオブジェクトで指定してください。")
    questions, ids = [], set()
    for original in suite["questions"]:
        if not isinstance(original, dict):
            raise ValueError("questionsの各項目はオブジェクトで指定してください。")
        question = original.copy()
        if not isinstance(question.get("id"), str) or not re.fullmatch(r"[a-z0-9_-]+", question["id"]) or question["id"] in ids:
            raise ValueError("質問IDは一意の英小文字・数字・ハイフン・アンダースコアで指定してください。")
        if not isinstance(question.get("category"), str) or not question["category"]:
            raise ValueError("質問にはcategoryが必要です。")
        if "prompt" not in question:
            material = materials.get(question.get("material_key"))
            if not isinstance(material, str) or not isinstance(question.get("instruction"), str):
                raise ValueError(f"質問 {question['id']} のinstructionまたはmaterial_keyが不正です。")
            question["prompt"] = question["instruction"] + "\n\n資料：\n" + material
        if not isinstance(question["prompt"], str) or not question["prompt"].strip():
            raise ValueError("質問には空でないpromptが必要です。")
        ids.add(question["id"])
        questions.append(question)
    return suite, questions


# Independent questions in four categories; source material is fixed locally.
QUESTION_SUITE, QUESTIONS = load_question_suite(DEFAULT_QUESTION_FILE)
HTTP = build_opener(ProxyHandler({}))


class ProgressReporter:
    """Write state between timed calls; a separate process handles live display."""
    def __init__(self, total: int, path: Path | None = None):
        self.path = path
        self.state = {"total": total, "completed": 0, "succeeded": 0, "failed": 0, "not_run": 0, "setup_failures": 0}

    def update(self, phase: str, detail: str = "", **fields) -> None:
        self.state.update(phase=phase, detail=detail, phase_started_at=time.time(), **fields)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            temporary.replace(self.path)

    def advance(self, row: dict) -> None:
        self.state["completed"] += 1
        key = "succeeded" if row["status"] == "ok" else ("not_run" if row["status"] == "not_run" else "failed")
        self.state[key] += 1
        self.update("recorded", f"{row['model_id']} / {row['device']} / {row['question_id']} #{row['repeat']}",
                    last_status=row["status"], last_total_seconds=row.get("total_seconds"))


@contextmanager
def progress_monitor(reporter: ProgressReporter, enabled: bool = True):
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "scripts/watch_benchmark.py"),
                                "--progress-file", str(reporter.path)], stdin=subprocess.DEVNULL,
                               stdout=sys.stderr) if enabled and reporter.path else None
    try:
        yield
    finally:
        if process is not None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_path(model: dict, directory: Path) -> Path:
    return directory / model["url"].rsplit("/", 1)[-1]


def verify_model(model: dict, path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"重みがありません。先に download --models {model['id']} を実行してください。")
    if path.stat().st_size != model["size_bytes"] or sha256_file(path) != model["sha256"]:
        raise ValueError(f"重みのサイズまたはSHA-256が一致しません: {path.name}")


def default_cache_dirs() -> list[Path]:
    """Known cache roots only; never search the whole home directory."""
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    hf_home = Path(os.environ.get("HF_HOME", str(cache_home / "huggingface")))
    hf_cache = Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))))
    return [hf_cache, cache_home / "llama.cpp", Path.home() / ".lmstudio/models",
            cache_home / "lm-studio/models"]


def find_local_model(model: dict, directory: Path, cache_dirs: list[Path] | None = None) -> Path | None:
    """Reuse identical weights in place, offline and before any timing."""
    target = model_path(model, directory)
    if target.exists():
        verify_model(model, target)  # a corrupt project file must not be silently replaced
        return target
    roots = list(dict.fromkeys(Path(root).expanduser() for root in [directory] + (cache_dirs or []) + default_cache_dirs()))
    parts = urlsplit(model["url"]).path.strip("/").split("/")
    checked = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = []
        if len(parts) >= 5 and parts[2] == "resolve":
            repo = root / f"models--{parts[0]}--{parts[1]}"
            candidates.extend([repo / "snapshots" / parts[3] / target.name,
                               repo / "blobs" / model["sha256"]])
        candidates.extend(sorted(root.rglob(f"*{target.name}")))
        for path in candidates:
            if not path.is_file() or path.resolve() in checked:
                continue
            checked.add(path.resolve())
            try:
                verify_model(model, path)
            except ValueError:
                continue  # same name with another quantization/revision is not equivalent
            return path
    return None


def download_models(models: list[dict], directory: Path, cache_dirs: list[Path] | None = None,
                    progress: ProgressReporter | None = None) -> None:
    """Download separately from benchmarking, then verify the pinned file."""
    directory.mkdir(parents=True, exist_ok=True)
    for model in models:
        if progress:
            progress.update("verifying", model["name"])
        local = find_local_model(model, directory, cache_dirs)
        if local is not None:
            print(f"取得済みを再利用: {model['name']} / {local}")
            continue
        path = model_path(model, directory)
        partial = path.with_suffix(path.suffix + ".part")
        remaining = max(0, model["size_bytes"] - (partial.stat().st_size if partial.exists() else 0))
        if shutil.disk_usage(directory).free < remaining + 1024**3:
            raise ValueError("重みの保存に必要な空き容量がありません。")
        print(f"ダウンロード: {model['name']} / {model['size_bytes'] / 10**9:.2f} GB / {model['license']}")
        if progress:
            progress.update("downloading", model["name"])
        subprocess.run([
            "curl", "--fail", "--location", "--retry", "2", "--connect-timeout", "30",
            "--continue-at", "-", "--output", str(partial), model["url"],
        ], check=True)
        verify_model(model, partial)
        partial.replace(path)


def api_json(base_url: str, endpoint: str, data: dict | None = None, timeout: float = 10) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    request = Request(base_url + endpoint, data=body, headers={"Content-Type": "application/json"})
    with HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


def clear_prompt_cache(base_url: str) -> None:
    """Erase slot 0 BEFORE the timed call; OS file caches are left alone."""
    result = api_json(base_url, "/slots/0?action=erase", {})
    if result.get("id_slot") != 0 or "n_erased" not in result:
        raise ValueError("KVキャッシュの消去を確認できませんでした。")


def measure_response(base_url: str, model_id: str, question: str, *,
                     max_tokens: int = 512, thinking: str = "auto", seed: int = 42,
                     timeout: float = 300, reasoning_effort: str | None = None,
                     sampling: dict | None = None) -> dict:
    """Time HTTP response only, including prefill, reasoning and generation."""
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": question}],
        "stream": True, "stream_options": {"include_usage": True},
        "max_tokens": max_tokens, "temperature": 0.7, "top_p": 0.8,
        "top_k": 20, "repeat_penalty": 1.0, "seed": seed,
        "cache_prompt": False, "id_slot": 0, "timings_per_token": True,
        "reasoning_format": "deepseek",
    }
    if thinking != "auto":
        payload["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
        payload.setdefault("chat_template_kwargs", {}).update({"enable_thinking": True, "reasoning_effort": reasoning_effort})
    if sampling:
        payload.update(sampling)
    request = Request(base_url + "/v1/chat/completions",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    answer, reasoning = [], []
    ttft = ttfa = finish_reason = None
    usage, timings = {}, {}
    done = False
    start = time.perf_counter()
    with HTTP.open(request, timeout=timeout) as response:
        # Role-only frames are not tokens. SSE chunk counts are not token counts.
        for raw_line in response:
            elapsed = time.perf_counter() - start
            if elapsed > timeout:
                raise TimeoutError("APIリクエストの総時間が制限を超えました。")
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if value == "[DONE]":
                total = elapsed
                done = True
                break
            event = json.loads(value)
            if event.get("error"):
                raise ValueError(f"API error: {event['error']}")
            usage = event.get("usage") or usage
            timings = event.get("timings") or timings
            for choice in event.get("choices", []):
                delta = choice.get("delta", {})
                content = delta.get("content") or ""
                thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if (content or thought) and ttft is None:
                    ttft = elapsed
                if content and ttfa is None:
                    ttfa = elapsed
                answer.append(content)
                reasoning.append(thought)
                finish_reason = choice.get("finish_reason") or finish_reason
    if not done or finish_reason is None:
        raise ValueError("ストリームが完了通知前に切断されました。")
    if timings.get("cache_n", 0) > 0:
        raise ValueError("前の質問のKVキャッシュ再利用が検出されました。計測条件を確認してください。")
    text = "".join(answer)
    return {
        "status": "truncated" if finish_reason == "length" else ("ok" if text else "no_answer"),
        "total_seconds": total, "ttft_seconds": ttft, "ttfa_seconds": ttfa,
        "answer": text, "reasoning": "".join(reasoning), "finish_reason": finish_reason,
        "usage": usage, "server_timings": timings,
        "generation_tokens_per_second": timings.get("predicted_per_second"),
        "answer_characters": len(text),
        "request_settings": {key: value for key, value in payload.items() if key != "messages"},
    }


def server_prefix(binary: str | None) -> list[str]:
    executable = shutil.which(binary) if binary else (shutil.which("llama-server") or shutil.which("llama"))
    if not executable:
        raise ValueError("llama-serverが必要です。docs/benchmark-design.md の導入手順を参照してください。")
    return [executable, "serve"] if Path(executable).name == "llama" else [executable]


def ensure_runtime(binary: str | None) -> list[str]:
    """The all action can prepare macOS dependencies before downloading weights."""
    try:
        return server_prefix(binary)
    except ValueError:
        brew = shutil.which("brew")
        if binary or platform.system() != "Darwin" or not brew:
            raise
        print("実行基盤を導入（計測外）: brew install llama.cpp", flush=True)
        subprocess.run([brew, "install", "llama.cpp"], check=True)
        return server_prefix(binary)


def select_gpu(prefix: list[str], requested: str | None) -> tuple[str, str]:
    result = subprocess.run(prefix + ["--list-devices"], capture_output=True, text=True, check=True, timeout=30)
    inventory = result.stdout + result.stderr
    devices = re.findall(r"^[ \t]+(?:\d+:[ \t]+)?([^ \t:]+):[ \t]+.+\([^\n)]*\)$", inventory, re.MULTILINE)
    devices = [device for device in devices if not device.lower().startswith("cpu")]
    selected = requested or (devices[0] if devices else None)
    if selected not in devices:
        raise ValueError(f"利用可能なGPUを確認できません。候補: {devices}。CPUのみなら --devices cpu を指定してください。")
    return selected, inventory


@contextmanager
def load_model(prefix: list[str], model: dict, path: Path, device: str, run_dir: Path,
               *, port: int, context_size: int, threads: int, gpu: str | None,
               startup_timeout: float = 180):
    """One subprocess per model/device; load and cleanup stay outside API timing."""
    with socket.socket() as check:
        check.bind(("127.0.0.1", port))
    command = prefix + [
        "--model", str(path), "--alias", model["id"], "--host", "127.0.0.1", "--port", str(port),
        "--ctx-size", str(context_size), "--parallel", "1", "--threads", str(threads),
        "--threads-batch", str(threads), "--batch-size", "512", "--ubatch-size", "128",
        "--cache-type-k", "f16", "--cache-type-v", "f16", "--cache-ram", "0",
        "--no-cache-idle-slots", "--slots", "--jinja", "--fit", "off",
    ]
    if device == "cpu":
        command += ["--device", "none", "--gpu-layers", "0", "--no-kv-offload", "--no-op-offload"]
    else:
        command += ["--device", str(gpu), "--gpu-layers", "99"]
    log_path = run_dir / f"{model['id']}-{device}.log"
    base_url = f"http://127.0.0.1:{port}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")}
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ValueError(f"モデルの起動に失敗しました。ログ: {log_path}")
                try:
                    ready = api_json(base_url, "/health", timeout=2).get("status") == "ok"
                    served = api_json(base_url, "/v1/models", timeout=2).get("data", []) if ready else []
                    if ready and any(entry.get("id") == model["id"] for entry in served):
                        break
                except (URLError, TimeoutError, OSError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError(f"ロード待機が制限時間を超えました。ログ: {log_path}")
            offloads = re.findall(r"offloaded\s+(\d+)(?:/\d+)?\s+layers to GPU", log_path.read_text(errors="replace"))
            offloaded = int(offloads[-1]) if offloads else 0
            if device == "gpu" and offloaded == 0:
                raise ValueError(f"GPUへのレイヤー配置を確認できません。ログ: {log_path}")
            if device == "cpu" and offloaded:
                raise ValueError(f"CPU測定なのにGPU配置が検出されました。ログ: {log_path}")
            yield base_url, {"command": command, "offloaded_layers": offloaded, "gpu_device": gpu if device == "gpu" else None}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def benchmark_loaded_model(base_url: str, model: dict, questions: list[dict], *,
                           repeats: int, max_tokens: int, thinking: str, timeout: float,
                           progress: ProgressReporter | None = None, device: str = ""):
    """Warm up once, then reset each independent question before timing it."""
    if progress:
        progress.update("warmup", f"{model['name']} / {device}")
    clear_prompt_cache(base_url)
    model_settings = {key: model[key] for key in ("reasoning_effort", "sampling") if key in model}
    measure_response(base_url, model["id"], "日本語でひとこと挨拶してください。", max_tokens=16,
                     thinking=thinking, timeout=timeout, **model_settings)
    for question in questions:
        for repeat in range(repeats):
            if progress:
                progress.update("clearing_cache", f"{model['name']} / {device} / {question['id']}")
            clear_prompt_cache(base_url)
            if progress:
                progress.update("waiting", f"{model['name']} / {device} / {question['id']} #{repeat + 1}/{repeats}")
            try:
                result = measure_response(base_url, model["id"], question["prompt"],
                                          max_tokens=max_tokens, thinking=thinking, timeout=timeout, **model_settings)
            except (URLError, TimeoutError, OSError, ValueError) as error:
                yield {"question_id": question["id"], "repeat": repeat + 1, "status": "error", "error": str(error)}
                return  # timeout may leave work pending; restart the process
            yield {"question_id": question["id"], "category": question["category"],
                   "prompt": question["prompt"], "repeat": repeat + 1, **result}


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["model_id"], row["device"], row.get("question_id"))
        groups.setdefault(key, []).append(row)
    summaries = []
    for (model, device, question), group in groups.items():
        good = [row for row in group if row["status"] == "ok"]
        not_run = sum(row["status"] == "not_run" for row in group)
        summary = {"model_id": model, "device": device, "question_id": question,
                   "samples": len(group), "completed_answers": len(good),
                   "attempted_samples": len(group) - not_run, "not_run_samples": not_run,
                   "failures_or_truncations": len(group) - len(good) - not_run, "stddev_ddof": 1}
        for metric in ("total_seconds", "ttft_seconds", "ttfa_seconds", "generation_tokens_per_second"):
            values = [row[metric] for row in good if row.get(metric) is not None]
            summary["values_" + metric] = values
            summary["n_" + metric] = len(values)
            summary["median_" + metric] = statistics.median(values) if values else None
            summary["mean_" + metric] = statistics.mean(values) if values else None
            summary["stddev_" + metric] = statistics.stdev(values) if len(values) >= 2 else None
        summaries.append(summary)
    return summaries


def validate_run_settings(models: list[dict], args: argparse.Namespace) -> None:
    if any(not model["thinking_switch"] for model in models) and args.thinking != "auto":
        raise ValueError("LFM2.5-2.6Bは常時思考モデルです。含める場合は --thinking auto を指定してください。")
    if args.thinking == "off" and any(model.get("reasoning_effort") for model in models):
        raise ValueError("Qwen3.8 xhighの測定では思考を無効にできません。--thinking auto または on を指定してください。")
    if args.devices == ["cpu"] and not args.all_cpu and not any(model["cpu_test"] for model in models):
        raise ValueError("選んだモデルのCPU測定には --all-cpu を指定してください。")


def planned_sessions(models: list[dict], args: argparse.Namespace) -> list[tuple[dict, str]]:
    return [(model, device) for model in models for device in args.devices
            if device == "gpu" or model["cpu_test"] or args.all_cpu]


def run_benchmark(models: list[dict], questions: list[dict], args: argparse.Namespace,
                  progress: ProgressReporter | None = None) -> Path:
    validate_run_settings(models, args)
    sessions = planned_sessions(models, args)
    progress = progress or ProgressReporter(len(sessions) * len(questions) * args.repeats)
    paths = {}
    for model in models:
        if not any(session_model["id"] == model["id"] for session_model, _ in sessions):
            continue
        progress.update("verifying", model["name"])
        path = find_local_model(model, args.model_dir, getattr(args, "cache_dir", None))
        if path is None:
            raise ValueError(f"重みがありません。download --models {model['id']} または --cache-dir 保存先 を指定してください。")
        paths[model["id"]] = path
    prefix = server_prefix(args.server_bin)
    gpu, inventory = select_gpu(prefix, args.gpu_device) if "gpu" in args.devices else (None, None)
    version = subprocess.run(prefix[:1] + ["--version"], capture_output=True, text=True, timeout=30)
    run_dir = args.output_dir / datetime.now().astimezone().strftime("%Y%m%dT%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=False)
    suite_path = getattr(args, "questions_file", DEFAULT_QUESTION_FILE)
    suite_metadata, _ = load_question_suite(suite_path)
    shutil.copyfile(suite_path, run_dir / "input-suite.json")
    metadata = {
        "schema_version": 2, "started_at": datetime.now().astimezone().isoformat(),
        "platform": {"system": platform.system(), "version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release(), "architecture": platform.machine()},
        "runtime_version": (version.stdout + version.stderr).strip(),
        "runtime_binary_sha256": sha256_file(Path(prefix[0])), "gpu_inventory": inventory,
        "models": models, "model_paths": paths, "questions": questions, "settings": vars(args).copy(),
        "question_suite": {"path": suite_path, "sha256": sha256_file(suite_path),
                           "suite_id": suite_metadata.get("suite_id"), "source": suite_metadata.get("source")},
        "planned_responses": progress.state["total"],
        "sessions": [], "timing_scope": "HTTP送信開始からSSE [DONE]まで。準備、ロード、warmup、KV消去、停止、ファイル保存を除く。",
    }
    try:
        metadata["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        metadata["git_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    except subprocess.SubprocessError:
        metadata["git_commit"] = None
    rows = []
    progress.update("preparing", "測定の準備", result_dir=str(run_dir))
    print(f"保存先: {run_dir}\n測定予定: {progress.state['total']}応答", flush=True)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as output, \
         (run_dir / "answers.md").open("w", encoding="utf-8") as answers:
        answers.write("# ローカルモデルの回答\n\nモデル名・デバイス・質問・繰り返しごとの記録。未実行も明記します。\n\n")

        def record(row):
            rows.append(row)
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            if "question_id" not in row:
                return
            progress.advance(row)
            answers.write(f"## {row['model_id']} / {row['device']} / {row['question_id']} / #{row['repeat']}\n\n")
            answers.write(f"状態: {row['status']} / 全体: {row.get('total_seconds')}秒 / 本文開始: {row.get('ttfa_seconds')}秒\n\n")
            body = row.get("answer") or row.get("error") or "本文なし"
            answers.write("> " + body.replace("\n", "\n> ") + "\n\n")
            answers.flush()
            if row["status"] != "not_run":
                print(f"[{progress.state['completed']}/{progress.state['total']}] {row['model_id']} / {row['device']} / "
                      f"{row['question_id']} #{row['repeat']} / {row['status']} / {row.get('total_seconds')}秒", flush=True)
            if getattr(args, "show_answers", False) and row.get("answer"):
                print(row["answer"], flush=True)

        for model, device in sessions:
            progress.update("loading", f"{model['name']} / {device}", log_file=str(run_dir / f"{model['id']}-{device}.log"))
            print(f"ロード（計測外）: {model['name']} / {device}", flush=True)
            seen, failure_reason = set(), "セッションが停止したため未実行"
            try:
                with load_model(prefix, model, paths[model["id"]], device, run_dir,
                                port=args.port, context_size=args.context_size, threads=args.threads, gpu=gpu) as (base_url, session):
                    metadata["sessions"].append({"model_id": model["id"], "device": device, **session})
                    for result in benchmark_loaded_model(base_url, model, questions, repeats=args.repeats,
                                                         max_tokens=args.max_tokens, thinking=args.thinking, timeout=args.timeout,
                                                         progress=progress, device=device):
                        seen.add((result["question_id"], result["repeat"]))
                        record({"model_id": model["id"], "device": device, "thinking_setting": args.thinking, **result})
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
                progress.state["setup_failures"] += 1
                failure_reason = str(error)
                record({"model_id": model["id"], "device": device, "status": "setup_error", "error": failure_reason})
                print(f"起動・準備エラー: {error}", file=sys.stderr, flush=True)
            finally:
                for question in questions:
                    for repeat in range(1, args.repeats + 1):
                        if (question["id"], repeat) not in seen:
                            record({"model_id": model["id"], "device": device, "question_id": question["id"],
                                    "category": question["category"], "prompt": question["prompt"], "repeat": repeat,
                                    "status": "not_run", "error": failure_reason})
                metadata["progress"] = progress.state.copy()
                (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
                (run_dir / "summary.json").write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    progress.update("measurement_complete", f"結果: {run_dir}")
    print(f"結果: {run_dir}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="日本語タスクでローカルLLMのGPU/CPU API応答を比較します。")
    parser.add_argument("action", choices=("plan", "download", "run", "all"),
                        help="allは実行基盤の準備→既存重みの再利用/不足分取得→測定を一括実行")
    parser.add_argument("--models", nargs="+", choices=[model["id"] for model in MODEL_URLS],
                        help="省略時は基本4モデル。Qwen3.8-27Bの挑戦枠は明示的に指定する")
    parser.add_argument("--include-challenge", action="store_true", help="Qwen3.8-27B xhigh / IQ2_Sも追加する")
    parser.add_argument("--questions-file", type=Path, default=DEFAULT_QUESTION_FILE, help="入力資料・質問のJSON")
    parser.add_argument("--questions", nargs="+", help="質問IDを指定。planで一覧を確認")
    parser.add_argument("--devices", nargs="+", choices=("gpu", "cpu"), default=["gpu", "cpu"])
    parser.add_argument("--all-cpu", action="store_true", help="大きなモデルもCPUで計測する")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--cache-dir", type=Path, action="append", help="取得済みGGUFの探索先を追加する（複数回指定可）")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".local/benchmarks")
    parser.add_argument("--server-bin", help="llama-server または llama 実行ファイル")
    parser.add_argument("--gpu-device", help="--list-devicesで確認したGPU名（省略時は先頭のGPU）")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--context-size", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--thinking", choices=("auto", "off", "on"), default="auto")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--progress-file", type=Path, help="進捗JSONの保存先")
    parser.add_argument("--no-progress", action="store_true", help="別プロセスによる待ち時間の表示を止める")
    parser.add_argument("--show-answers", action="store_true", help="回答完了後に本文を端末にも表示する")
    args = parser.parse_args()
    models_by_id = {model["id"]: model for model in MODEL_URLS}
    model_ids = args.models if args.models is not None else [model["id"] for model in MODEL_URLS if model.get("default", True)]
    if args.include_challenge and "qwen3.8-27b-iq2-s" not in model_ids:
        model_ids = model_ids + ["qwen3.8-27b-iq2-s"]
    models = [models_by_id[model_id] for model_id in model_ids]
    if min(args.repeats, args.threads, args.max_tokens, args.context_size, args.timeout) <= 0:
        parser.error("繰り返し、スレッド、トークン数、文脈長、制限時間は正数が必要です。")
    if not 1 <= args.port <= 65535 or args.max_tokens >= args.context_size:
        parser.error("ポートまたは出力トークン数の設定が不正です。")
    progress = None
    try:
        suite, suite_questions = load_question_suite(args.questions_file)
        questions_by_id = {question["id"]: question for question in suite_questions}
        question_ids = args.questions if args.questions is not None else list(questions_by_id)
        unknown = set(question_ids) - set(questions_by_id)
        if unknown:
            raise ValueError(f"不明な質問ID: {sorted(unknown)}。利用可能: {list(questions_by_id)}")
        for values in (model_ids, question_ids, args.devices):
            if len(values) != len(set(values)):
                raise ValueError("モデル・質問・デバイスの指定は重複させないでください。")
        questions = [questions_by_id[question_id] for question_id in question_ids]
        total = len(planned_sessions(models, args)) * len(questions) * args.repeats
        if args.action == "plan":
            print(json.dumps({"models": models, "questions": questions, "source": suite.get("source"),
                              "settings": vars(args), "planned_responses": total,
                              "note": "planはダウンロードもモデル起動も行いません。"}, ensure_ascii=False, indent=2, default=str))
        elif args.action == "download":
            download_models(models, args.model_dir, args.cache_dir)
        else:
            validate_run_settings(models, args)
            args.progress_file = args.progress_file or ROOT / f".local/progress/benchmark-{os.getpid()}.json"
            progress = ProgressReporter(total, args.progress_file)
            progress.update("preparing", f"{len(questions)}問 × {args.repeats}回、合計{total}応答")
            with progress_monitor(progress, enabled=not args.no_progress):
                try:
                    if args.action == "all":
                        progress.update("runtime", "実行基盤の確認・導入")
                        prefix = ensure_runtime(args.server_bin)
                        if "gpu" in args.devices:
                            select_gpu(prefix, args.gpu_device)
                        active_ids = {model["id"] for model, _ in planned_sessions(models, args)}
                        download_models([model for model in models if model["id"] in active_ids], args.model_dir, args.cache_dir, progress)
                        if platform.system() == "Darwin":
                            progress.update("hardware", "測定前のPC状態を保存")
                            subprocess.run([sys.executable, str(ROOT / "scripts/probe_hardware.py"),
                                            "--output", str(ROOT / ".local/hardware-before-benchmark.json")], check=True)
                    run_dir = run_benchmark(models, questions, args, progress)
                    if args.action == "all" and platform.system() == "Darwin":
                        shutil.copyfile(ROOT / ".local/hardware-before-benchmark.json", run_dir / "hardware-before.json")
                        progress.update("hardware", "測定後のPC状態を保存")
                        subprocess.run([sys.executable, str(ROOT / "scripts/probe_hardware.py"),
                                        "--output", str(run_dir / "hardware-after.json")], check=True)
                    progress.update("finished", f"結果: {run_dir}")
                except BaseException:
                    progress.update("failed", "測定は停止しました。端末のエラーと保存済み結果を確認してください。")
                    raise
            rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
            return 1 if any(row["status"] != "ok" for row in rows) else 0
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました。保存済みの結果は残っています。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
