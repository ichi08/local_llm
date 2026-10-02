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

# Independent Japanese tasks; no previous question/answer is carried forward.
QUESTIONS = [
    {"id": "explain", "category": "説明", "prompt": "ローカルLLMを初めて使う人へ、量子化とメモリの関係を日本語で説明してください。150字以内で、身近なたとえを1つ使ってください。"},
    {"id": "summary", "category": "要約", "prompt": "次の資料だけを使って、重要な点を日本語の箇条書き3つで要約してください。資料：架空の社内検索システム『灯』は、2026年8月に試験導入された。対象は社員20人。検索時間の中央値は12秒から5秒になった。回答の正確さはまだ評価していない。外部ネットワークへ質問を送信しない。"},
    {"id": "json", "category": "構造化", "prompt": "文章から値を抽出し、JSONオブジェクトだけを返してください。nameは会の名前、dateは開催日、attendeesは参加人数、locationは会場。日付はYYYY-MM-DD形式。資料にない値はnull。文章：青空会の勉強会は2026年10月12日に開催します。参加者は18人です。会場は未定です。"},
    {"id": "reasoning", "category": "推論", "prompt": "3台のPCで同じ処理をします。Aは1件6秒、Bは1件10秒、Cは1件15秒です。各PCは同時に1件だけ処理できます。60秒で3台合計何件を完了できますか。待ち時間はなく、60秒ちょうどに完了した処理も数えます。答えと短い計算式を日本語で示してください。"},
    {"id": "rewrite", "category": "日本語", "prompt": "次の文章を自然な日本語のメールに書き直してください。丁寧で簡潔にし、事実を追加せず、件名と本文を出してください。文章：昨日もらった資料の3ページ、数字が合わない気がする。元データを明日の15時までに送ってほしい。難しければいつ送れるか知りたい。"},
    {"id": "grounded", "category": "根拠", "prompt": "次の資料だけに基づいて質問に答えてください。分からないことは『資料からは分かりません』と答えてください。資料：このPCはM1 Pro、メモリ16GB。モデルXを起動できた。生成速度は測っていない。質問：モデルXは1秒に何トークン生成できますか。モデルYより速いですか。"},
    {"id": "code", "category": "コード", "prompt": "Pythonの関数count_statuses(lines)を書いてください。入力は文字列のリストで、各行は『時刻 HTTPステータス』の形式です。ステータスごとの件数を整数キーのdictで返してください。空行と形式が不正な行は無視してください。外部ライブラリは使わず、コードだけを返してください。"},
    {"id": "x_post", "category": "記事素材", "prompt": "次の架空の実験結果を紹介するX投稿を日本語で1つ書いてください。120字以内で、断定を避け、興味を引く問いを含めてください。架空の結果：16GBのMacで2種類のローカルLLMを比較。モデルAは軽快だが要約で情報を落とした。モデルBは遅いが資料に忠実だった。数値はまだ集計していない。"},
]

ROOT = Path(__file__).resolve().parent.parent
HTTP = build_opener(ProxyHandler({}))


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


def download_models(models: list[dict], directory: Path) -> None:
    """Download separately from benchmarking, then verify the pinned file."""
    directory.mkdir(parents=True, exist_ok=True)
    for model in models:
        path = model_path(model, directory)
        if path.exists():
            verify_model(model, path)
            print(f"取得済み: {model['name']}")
            continue
        partial = path.with_suffix(path.suffix + ".part")
        remaining = max(0, model["size_bytes"] - (partial.stat().st_size if partial.exists() else 0))
        if shutil.disk_usage(directory).free < remaining + 1024**3:
            raise ValueError("重みの保存に必要な空き容量がありません。")
        print(f"ダウンロード: {model['name']} / {model['size_bytes'] / 10**9:.2f} GB / {model['license']}")
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
                           repeats: int, max_tokens: int, thinking: str, timeout: float):
    """Warm up once, then reset each independent question before timing it."""
    clear_prompt_cache(base_url)
    model_settings = {key: model[key] for key in ("reasoning_effort", "sampling") if key in model}
    measure_response(base_url, model["id"], "日本語でひとこと挨拶してください。", max_tokens=16,
                     thinking=thinking, timeout=timeout, **model_settings)
    for question in questions:
        for repeat in range(repeats):
            clear_prompt_cache(base_url)
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
        summary = {"model_id": model, "device": device, "question_id": question,
                   "samples": len(group), "completed_answers": len(good),
                   "failures_or_truncations": len(group) - len(good)}
        for metric in ("total_seconds", "ttft_seconds", "ttfa_seconds", "generation_tokens_per_second"):
            values = [row[metric] for row in good if row.get(metric) is not None]
            summary["median_" + metric] = statistics.median(values) if values else None
        summaries.append(summary)
    return summaries


def run_benchmark(models: list[dict], questions: list[dict], args: argparse.Namespace) -> Path:
    if any(not model["thinking_switch"] for model in models) and args.thinking != "auto":
        raise ValueError("LFM2.5-2.6Bは常時思考モデルです。含める場合は --thinking auto を指定してください。")
    if args.thinking == "off" and any(model.get("reasoning_effort") for model in models):
        raise ValueError("Qwen3.8 xhighの測定では思考を無効にできません。--thinking auto または on を指定してください。")
    if args.devices == ["cpu"] and not args.all_cpu and not any(model["cpu_test"] for model in models):
        raise ValueError("選んだモデルのCPU測定には --all-cpu を指定してください。")
    paths = {model["id"]: model_path(model, args.model_dir) for model in models}
    for model in models:
        verify_model(model, paths[model["id"]])
    prefix = server_prefix(args.server_bin)
    gpu, inventory = select_gpu(prefix, args.gpu_device) if "gpu" in args.devices else (None, None)
    version = subprocess.run(prefix[:1] + ["--version"], capture_output=True, text=True, timeout=30)
    run_dir = args.output_dir / datetime.now().astimezone().strftime("%Y%m%dT%H%M%S-%f")
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1, "started_at": datetime.now().astimezone().isoformat(),
        "platform": {"system": platform.system(), "version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release(), "architecture": platform.machine()},
        "runtime_version": (version.stdout + version.stderr).strip(),
        "runtime_binary_sha256": sha256_file(Path(prefix[0])), "gpu_inventory": inventory,
        "models": models, "questions": questions, "settings": vars(args).copy(),
        "sessions": [], "timing_scope": "HTTP送信開始からSSE [DONE]まで。準備、ロード、warmup、KV消去、停止、ファイル保存を除く。",
    }
    try:
        metadata["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        metadata["git_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    except subprocess.SubprocessError:
        metadata["git_commit"] = None
    rows = []
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as output:
        for model in models:
            for device in args.devices:
                if device == "cpu" and not model["cpu_test"] and not args.all_cpu:
                    continue
                print(f"ロード（計測外）: {model['name']} / {device}", flush=True)
                try:
                    with load_model(prefix, model, paths[model["id"]], device, run_dir,
                                    port=args.port, context_size=args.context_size, threads=args.threads, gpu=gpu) as (base_url, session):
                        metadata["sessions"].append({"model_id": model["id"], "device": device, **session})
                        for result in benchmark_loaded_model(base_url, model, questions, repeats=args.repeats,
                                                             max_tokens=args.max_tokens, thinking=args.thinking, timeout=args.timeout):
                            row = {"model_id": model["id"], "device": device, "thinking_setting": args.thinking, **result}
                            rows.append(row)
                            output.write(json.dumps(row, ensure_ascii=False) + "\n")
                            output.flush()
                            print(f"  {row.get('question_id')} #{row.get('repeat')} / {row['status']} / {row.get('total_seconds')}秒")
                            if row.get("repeat") == 1 and row.get("answer"):
                                print(row["answer"])
                except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
                    row = {"model_id": model["id"], "device": device, "status": "setup_error", "error": str(error)}
                    rows.append(row)
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")
                    output.flush()
                    print(f"  起動・準備エラー: {error}", file=sys.stderr)
                finally:
                    (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n")
                    (run_dir / "summary.json").write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2) + "\n")
    print(f"結果: {run_dir}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="日本語タスクでローカルLLMのGPU/CPU API応答を比較します。")
    parser.add_argument("action", choices=("plan", "download", "run"))
    parser.add_argument("--models", nargs="+", choices=[model["id"] for model in MODEL_URLS],
                        help="省略時は基本4モデル。Qwen3.8-27Bの挑戦枠は明示的に指定する")
    parser.add_argument("--questions", nargs="+", choices=[question["id"] for question in QUESTIONS])
    parser.add_argument("--devices", nargs="+", choices=("gpu", "cpu"), default=["gpu", "cpu"])
    parser.add_argument("--all-cpu", action="store_true", help="大きなモデルもCPUで計測する")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "models")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".local/benchmarks")
    parser.add_argument("--server-bin", help="llama-server または llama 実行ファイル")
    parser.add_argument("--gpu-device", help="--list-devicesで確認したGPU名（省略時は先頭のGPU）")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--context-size", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--thinking", choices=("auto", "off", "on"), default="auto")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    models_by_id = {model["id"]: model for model in MODEL_URLS}
    model_ids = args.models if args.models is not None else [model["id"] for model in MODEL_URLS if model.get("default", True)]
    models = [models_by_id[model_id] for model_id in model_ids]
    questions_by_id = {question["id"]: question for question in QUESTIONS}
    question_ids = args.questions if args.questions is not None else list(questions_by_id)
    questions = [questions_by_id[question_id] for question_id in question_ids]
    if min(args.repeats, args.threads, args.max_tokens, args.context_size, args.timeout) <= 0:
        parser.error("繰り返し、スレッド、トークン数、文脈長、制限時間は正数が必要です。")
    if not 1 <= args.port <= 65535 or args.max_tokens >= args.context_size:
        parser.error("ポートまたは出力トークン数の設定が不正です。")
    try:
        if args.action == "plan":
            print(json.dumps({"models": models, "questions": questions, "note": "planはダウンロードもモデル起動も行いません。"}, ensure_ascii=False, indent=2))
        elif args.action == "download":
            download_models(models, args.model_dir)
        else:
            run_dir = run_benchmark(models, questions, args)
            rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
            return 1 if any(row["status"] != "ok" for row in rows) else 0
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
