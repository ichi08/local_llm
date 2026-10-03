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
import signal
import threading
import statistics
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

if __package__:
    from .page_inputs import capture_page, read_snapshot
    from .setup_environment import read_lock
    from .watch_benchmark import PHASES, STATUS
    from .benchmark_reports import write_reports, evaluate_answer, latest_rows
    from .resource_monitor import ResourceMonitor
else:
    from page_inputs import capture_page, read_snapshot
    from setup_environment import read_lock
    from watch_benchmark import PHASES, STATUS
    from benchmark_reports import write_reports, evaluate_answer, latest_rows
    from resource_monitor import ResourceMonitor

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


def load_question_suite(path: Path, *, fetch_pages: bool = False,
                        allow_missing_pages: bool = False) -> tuple[dict, list[dict]]:
    """Keep source text and evaluation hints editable without changing code."""
    suite = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict) or not isinstance(suite.get("questions"), list) or not suite["questions"]:
        raise ValueError("入力JSONには空でないquestionsリストが必要です。")
    materials = suite.get("materials", {})
    if not isinstance(materials, dict):
        raise ValueError("materialsはオブジェクトで指定してください。")
    materials = materials.copy()
    sources = suite.get("material_sources", {})
    if not isinstance(sources, dict):
        raise ValueError("material_sourcesはオブジェクトで指定してください。")
    snapshots = {}
    for key, source in sources.items():
        if not isinstance(source, dict) or not isinstance(source.get("url"), str) or not isinstance(source.get("cache_dir"), str):
            raise ValueError("ページ資料にはurlとcache_dirが必要です。")
        directory = (path.parent / source["cache_dir"]).resolve()
        if not (directory / "index.json").exists():
            if not fetch_pages:
                if allow_missing_pages:
                    continue
                raise ValueError("ページ全文が未取得です。benchmark.shの通常実行で取得してください。")
            print(f"入力ページを取得（計測外）: {source['url']}", flush=True)
        text, info = capture_page(source["url"], directory, method=source.get("capture_method", "static")) if fetch_pages else read_snapshot(directory, source["url"], method=source.get("capture_method", "static"))
        expected_title = source.get("expected_title")
        if expected_title and expected_title not in info["title"]:
            raise ValueError("取得ページのタイトルが予定した記事と一致しません。エラーページ等を確認してください。")
        materials[key], snapshots[key] = text, info
    suite = {**suite, "materials": materials, "material_snapshots": snapshots}
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
            if material is None and allow_missing_pages and question.get("material_key") in sources and isinstance(question.get("instruction"), str):
                question["input_pending"] = True
                ids.add(question["id"])
                questions.append(question)
                continue
            if not isinstance(material, str) or not isinstance(question.get("instruction"), str):
                raise ValueError(f"質問 {question['id']} のinstructionまたはmaterial_keyが不正です。")
            question["prompt"] = question["instruction"] + "\n\n資料：\n" + material
            if question["material_key"] in snapshots:
                question["material_snapshot"] = snapshots[question["material_key"]]
        if not isinstance(question["prompt"], str) or not question["prompt"].strip():
            raise ValueError("質問には空でないpromptが必要です。")
        followups = question.get('followups', [])
        if not isinstance(followups,list) or any(not isinstance(item,str) or not item.strip() for item in followups):
            raise ValueError('followupsは空でない文章のリストで指定してください。')
        ids.add(question["id"])
        questions.append(question)
    return suite, questions


# Page snapshots are resolved at execution time, never while importing the module.
HTTP = build_opener(ProxyHandler({}))


class ProgressReporter:
    """Publish lossless events and live state only outside timed requests."""
    def __init__(self, total: int, path: Path | None = None):
        self.path = path
        self.monitored = False
        self.accounted = {}
        self.state = {"total": total, "completed": 0, "succeeded": 0, "failed": 0, "not_run": 0, "setup_failures": 0}

    def log(self, message: str):
        if self.monitored:
            with self.path.with_suffix(".events.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps({"message": message}, ensure_ascii=False)+"\n")
        else:
            print(message, flush=True)

    def update(self, phase: str, detail: str = "", **fields) -> None:
        changed = self.state.get("phase") != phase or self.state.get("detail") != detail
        self.state.update(phase=phase, detail=detail, phase_started_at=time.time(), **fields)
        if changed and phase in PHASES:
            self.log(f"[{PHASES[phase]}] {detail}")
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, default=str)+"\n", encoding="utf-8")
            temporary.replace(self.path)

    def seed(self, rows):
        self.accounted = {(r['model_id'],r['device'],r['question_id'],r['repeat'],r.get('turn',1)):r['status'] for r in rows}
        self.state.update(completed=len(rows),succeeded=sum(r['status']=='ok' for r in rows),
                          not_run=sum(r['status']=='not_run' for r in rows),failed=sum(r['status'] not in {'ok','not_run'} for r in rows))

    def advance(self, row: dict) -> None:
        identity = (row['model_id'],row['device'],row['question_id'],row['repeat'],row.get('turn',1))
        counter = lambda status: 'succeeded' if status=='ok' else 'not_run' if status=='not_run' else 'failed'
        previous = self.accounted.get(identity)
        if previous is None:
            self.state['completed'] += 1
        else:
            self.state[counter(previous)] -= 1
        self.accounted[identity] = row['status']
        self.state[counter(row['status'])] += 1
        duration = f" / {row['total_seconds']:.2f}秒" if row.get('total_seconds') is not None else ""
        if row['status'] != 'not_run':
            self.log(f"[結果 {self.state['completed']}/{self.state['total']}] {row['model_id']} / {row['device'].upper()} / "
                     f"{row['question_id']} / {row['repeat']}回目 / 試行{row.get('attempt',1)}: {STATUS.get(row['status'], row['status'])}{duration}")
            if row.get('error'):
                self.log(f"  理由: {row['error']}")
        self.update('recorded',row.get('question_id',''),last_status=row['status'],last_total_seconds=row.get('total_seconds'))


@contextmanager
def progress_monitor(reporter: ProgressReporter, enabled: bool = True):
    process = None
    if enabled and reporter.path:
        reporter.path.parent.mkdir(parents=True, exist_ok=True)
        reporter.path.with_suffix('.events.jsonl').write_text('')
        reporter.monitored = True
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "scripts/watch_benchmark.py"),
                                    "--progress-file", str(reporter.path), '--parent-pid', str(os.getpid())],
                                   stdin=subprocess.DEVNULL, stdout=sys.stderr)
    try:
        yield
    finally:
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        reporter.monitored = False


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
    progress = progress or ProgressReporter(0)
    directory.mkdir(parents=True, exist_ok=True)
    for model in models:
        if progress:
            progress.update("verifying", model["name"])
        local = find_local_model(model, directory, cache_dirs)
        if local is not None:
            progress.log(f"[モデル取得] {model['name']}: 取得済みを再利用（SHA-256一致）")
            continue
        path = model_path(model, directory)
        partial = path.with_suffix(path.suffix + ".part")
        if partial.exists() and partial.stat().st_size == model['size_bytes']:
            progress.log(f"[モデル取得] {model['name']}: 前回取得した途中ファイルの全量とSHA-256を確認しています")
            verify_model(model, partial)
            partial.replace(path)
            progress.log(f"[モデル取得] {model['name']}: 前回の取得を復旧しました（100%・SHA-256一致）")
            continue
        remaining = max(0, model["size_bytes"] - (partial.stat().st_size if partial.exists() else 0))
        if shutil.disk_usage(directory).free < remaining + 1024**3:
            raise ValueError("重みの保存に必要な空き容量がありません。")
        progress.log(f"[モデル取得] {model['name']} / {model['size_bytes'] / 10**9:.2f} GB / {model['license']}")
        if progress:
            progress.update("downloading", model["name"], download_path=str(partial), download_bytes=model["size_bytes"],
                            download_initial_bytes=partial.stat().st_size if partial.exists() else 0)
        result = subprocess.run([
            "curl", "--fail", "--silent", "--show-error", "--location", "--retry", "2", "--connect-timeout", "30",
            "--continue-at", "-", "--output", str(partial), model["url"],
        ], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"{model['name']}のダウンロード失敗（curl {result.returncode}）: {result.stderr.strip()}")
        progress.log(f"[モデル取得] {model['name']}: 100%取得。SHA-256を確認しています")
        progress.update("verifying", model["name"])
        verify_model(model, partial)
        partial.replace(path)
        progress.log(f"[モデル取得] {model['name']}: 保存・整合性確認完了")


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


def request_payload(model_id, question, *, max_tokens=-1, thinking="auto", seed=42,
                    reasoning_effort=None, sampling=None, messages=None):
    payload = {"model": model_id, "messages": messages or [{"role": "user", "content": question}],
               "stream": True, "stream_options": {"include_usage": True}, "max_tokens": max_tokens,
               "temperature": .7, "top_p": .8, "top_k": 20, "repeat_penalty": 1., "seed": seed,
               "cache_prompt": False, "id_slot": 0, "timings_per_token": True, "reasoning_format": "deepseek"}
    if thinking != "auto":
        payload["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
        payload.setdefault("chat_template_kwargs", {}).update(enable_thinking=True, reasoning_effort=reasoning_effort)
    if sampling:
        payload.update(sampling)
    return payload


def preflight_input(base_url, payload):
    """Use the loaded model's actual template/tokenizer; never shorten input."""
    props = api_json(base_url, '/props')
    context = props.get('default_generation_settings',{}).get('n_ctx')
    if not isinstance(context,int) or context<=0:
        raise ValueError('実効文脈長をAPIから確認できません。setup.shで固定したllama.cppを使用してください。')
    rendered = api_json(base_url, '/apply-template', {"messages": payload['messages'],
                        'add_generation_prompt': True, 'chat_template_kwargs': payload.get('chat_template_kwargs', {})})
    if not isinstance(rendered.get('prompt'),str):
        raise ValueError('モデルのチャットテンプレートを適用できませんでした。')
    tokens = api_json(base_url, '/tokenize', {'content': rendered['prompt'], 'add_special': True, 'parse_special': True})['tokens']
    info = {'input_tokens': len(tokens), 'effective_context_tokens': context,
            'available_output_tokens': max(0, context-len(tokens)), 'template_sha256': hashlib.sha256(rendered['prompt'].encode()).hexdigest()}
    return info


def measure_response(base_url: str, model_id: str, question: str, *, max_tokens=-1, thinking="auto", seed=42,
                     timeout=300, connect_timeout=15, idle_timeout=120, reasoning_effort=None, sampling=None, messages=None):
    """Time only HTTP. A POSIX deadline interrupts even a blocked stream read."""
    payload = request_payload(model_id, question, max_tokens=max_tokens, thinking=thinking, seed=seed,
                              reasoning_effort=reasoning_effort, sampling=sampling, messages=messages)
    request = Request(base_url + '/v1/chat/completions', data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type':'application/json'})
    answer, reasoning, usage, timings = [], [], {}, {}
    ttft = ttfa = finish = None
    done, error, status = False, None, None
    start = time.perf_counter()
    last_data = start
    can_alarm = hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()
    old_handler = signal.getsignal(signal.SIGALRM) if can_alarm else None
    stage = '接続'
    def alarm_handler(*_):
        elapsed = time.perf_counter()-start
        kind = '総時間' if elapsed >= timeout else ('接続待ち' if stage == '接続' else '無通信')
        raise TimeoutError(f'{kind}の時間制限に到達しました。途中の本文・思考を保存しました。')
    def arm(limit):
        if can_alarm:
            signal.setitimer(signal.ITIMER_REAL, max(.001, min(limit, timeout-(time.perf_counter()-start))))
    try:
        if can_alarm:
            signal.signal(signal.SIGALRM, alarm_handler)
        arm(connect_timeout)
        with HTTP.open(request, timeout=min(timeout, connect_timeout)) as response:
            stage = '応答'
            # urllib's socket timeout must not leave the initial connect limit on reads.
            try:
                response.fp.raw._sock.settimeout(min(timeout, idle_timeout))
            except AttributeError:
                pass  # simulated transport
            arm(idle_timeout)
            for raw_line in response:
                elapsed = time.perf_counter()-start
                if elapsed >= timeout:
                    raise TimeoutError('総時間の時間制限に到達しました。')
                if time.perf_counter()-last_data >= idle_timeout:
                    raise TimeoutError('無通信の時間制限に到達しました。')
                last_data = time.perf_counter()
                arm(idle_timeout)
                line = raw_line.decode('utf-8').strip()
                if not line.startswith('data:'):
                    continue
                value = line[5:].strip()
                if value == '[DONE]':
                    done = True
                    break
                event = json.loads(value)
                if event.get('error'):
                    raise ValueError(f"API error: {event['error']}")
                usage, timings = event.get('usage') or usage, event.get('timings') or timings
                for choice in event.get('choices', []):
                    delta = choice.get('delta', {})
                    content = delta.get('content') or ''
                    thought = delta.get('reasoning_content') or delta.get('reasoning') or ''
                    if (content or thought) and ttft is None:
                        ttft = elapsed
                    if content and ttfa is None:
                        ttfa = elapsed
                    answer.append(content)
                    reasoning.append(thought)
                    finish = choice.get('finish_reason') or finish
        if not done or finish is None:
            status, error = 'disconnected', 'ストリームが完了通知前に切断されました。途中の本文・思考を保存しました。'
        elif timings.get('cache_n', 0) > 0:
            status, error = 'error', '前の質問のKVキャッシュ再利用が検出されました。'
        elif finish == 'length':
            status = 'truncated'
        elif finish != 'stop':
            status, error = 'error', f'想定外の終了理由: {finish}'
    except KeyboardInterrupt:
        status, error = 'cancelled', '利用者が中断しました。途中の本文・思考を保存しました。'
    except (TimeoutError, socket.timeout) as failure:
        status, error = 'timeout', str(failure)
    except (URLError, OSError, ValueError) as failure:
        status, error = ('timeout' if isinstance(getattr(failure,'reason',None), TimeoutError) else 'error'), str(failure)
    finally:
        if can_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
    text = ''.join(answer)
    return {'status': status or ('ok' if text else 'no_answer'), 'total_seconds': elapsed if done else time.perf_counter()-start,
            'ttft_seconds': ttft, 'ttfa_seconds': ttfa, 'answer': text, 'reasoning': ''.join(reasoning),
            'finish_reason': finish, 'stream_done': done, 'error': error, 'usage': usage, 'server_timings': timings,
            'generation_tokens_per_second': timings.get('predicted_per_second'), 'answer_characters': len(text),
            'request_settings': {key:value for key,value in payload.items() if key != 'messages'}}


def server_prefix(binary: str | None, *, use_lock=True) -> list[str]:
    if use_lock and not binary and (ROOT / '.local/environment.json').exists():
        lock = read_lock()
        return [str(ROOT / lock['runtime']['binary'])] + lock['runtime'].get('arguments', [])
    executable = shutil.which(binary) if binary else (shutil.which("llama-server") or shutil.which("llama"))
    if not executable and not binary and platform.system() == "Darwin":
        # Conda/Rosetta shells do not always include the native Homebrew bin directory.
        for directory in ("/opt/homebrew/bin", "/opt/homebrew/opt/llama.cpp/bin",
                          "/usr/local/bin", "/usr/local/opt/llama.cpp/bin"):
            executable = shutil.which("llama-server", path=directory) or shutil.which("llama", path=directory)
            if executable:
                break
    if not executable:
        detail = f"指定した実行ファイルが見つかりません: {binary}。" if binary else "llama-serverが必要です。"
        raise ValueError(detail + "docs/benchmark-design.md の導入手順を参照してください。")
    prefix = [executable, 'serve'] if Path(executable).name == 'llama' else [executable]
    lock_path = ROOT/'.local/environment.json'
    if lock_path.exists():
        stored = json.loads(lock_path.read_text())
        if Path(executable).resolve() == (ROOT/stored['runtime']['binary']).resolve():
            lock = read_lock()
            prefix = [executable] + lock['runtime'].get('arguments', [])
    return prefix


def ensure_runtime(binary: str | None, progress=None, *, use_lock=True) -> list[str]:
    """Find, install and check the runtime, including native Homebrew under Rosetta."""
    emit = progress.log if progress else lambda message: print(message, flush=True)
    try:
        prefix = server_prefix(binary) if use_lock else server_prefix(binary, use_lock=False)
    except ValueError:
        if binary or platform.system() != "Darwin":
            raise
        brew = (shutil.which("brew") or shutil.which("brew", path="/opt/homebrew/bin")
                or shutil.which("brew", path="/usr/local/bin"))
        if not brew:
            raise ValueError("llama.cppとHomebrewが見つかりません。Homebrewを導入するか、"
                             "--server-bin でllama-serverを指定してください。")
        brew = str(Path(brew).resolve())
        command = [brew, "install", "llama.cpp"]
        # /opt/homebrew is the ARM prefix; never inherit Rosetta's x86_64 architecture.
        if Path(brew).is_relative_to("/opt/homebrew"):
            command = ["/usr/bin/arch", "-arm64"] + command
        log_dir = ROOT / ".local/runtime"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"install-{datetime.now().strftime('%Y%m%dT%H%M%S-%f')}.log"
        emit(f"実行基盤を導入（計測外）: llama.cpp\n導入ログ: {log_path}")
        with log_path.open("w", encoding="utf-8") as log:
            try:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            except subprocess.CalledProcessError as error:
                raise ValueError(f"llama.cppの導入に失敗しました（終了コード{error.returncode}）。"
                                 f"詳細: {log_path}") from error
        try:
            prefix = server_prefix(None) if use_lock else server_prefix(None, use_lock=False)
        except ValueError as error:
            # Also handle a Homebrew installation in a user-selected prefix.
            executable = (shutil.which("llama-server", path=str(Path(brew).parent))
                          or shutil.which("llama", path=str(Path(brew).parent)))
            if not executable:
                raise ValueError(f"導入後もllama-serverが見つかりません。詳細: {log_path}") from error
            prefix = server_prefix(executable)
    try:
        version = subprocess.run(prefix + ["--version"], capture_output=True, text=True, check=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f"llama.cppの実行確認に失敗しました: {prefix[0]}。{error}") from error
    version_text = version.stdout + version.stderr
    readable = next((line for line in version_text.splitlines() if line.startswith("version:")), "バージョン確認済み")
    emit(f"実行基盤: llama.cpp / {readable}")
    return prefix


def select_gpu(prefix: list[str], requested: str | None) -> tuple[str, str]:
    result = subprocess.run(prefix + ["--list-devices"], capture_output=True, text=True, check=True, timeout=30)
    inventory = result.stdout + result.stderr
    devices = re.findall(r"^[ \t]+(?:\d+:[ \t]+)?([^ \t:]+):[ \t]+.+\([^\n)]*\)$", inventory, re.MULTILINE)
    # Accelerate/BLAS appears in recent llama.cpp inventories but runs on the CPU.
    devices = [device for device in devices if not device.lower().startswith(("cpu", "blas"))]
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
        # A previous session's closed connections may still be in TIME_WAIT.
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        check.bind(("127.0.0.1", port))
    slot_dir = (run_dir / f"{model['id']}-{device}-slots").resolve()
    slot_dir.mkdir(parents=True, exist_ok=True)
    command = prefix + [
        "--model", str(path), "--alias", model["id"], "--host", "127.0.0.1", "--port", str(port),
        "--ctx-size", str(context_size), "--parallel", "1", "--threads", str(threads),
        "--threads-batch", str(threads), "--batch-size", "512", "--ubatch-size", "128",
        "--cache-type-k", "f16", "--cache-type-v", "f16", "--cache-ram", "0",
        "--no-cache-idle-slots", "--slots", "--slot-save-path", str(slot_dir), "--jinja", "--fit", "off",
        "--log-verbosity", "4", "--no-context-shift",
    ]
    if device == "cpu":
        command += ["--device", "none", "--gpu-layers", "0", "--no-kv-offload", "--no-op-offload"]
        if platform.system() == "Darwin":
            # 0.5.0's repacked CPU path repeated MiniCPM5 output on this Mac.
            command += ["--no-repack"]
    else:
        command += ["--device", str(gpu), "--gpu-layers", "99"]
    log_path = run_dir / f"{model['id']}-{device}.log"
    if log_path.exists():
        log_path = run_dir / f"{model['id']}-{device}-{datetime.now().strftime('%H%M%S-%f')}.log"
    base_url = f"http://127.0.0.1:{port}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")}
    with log_path.open("w") as log:
        startup_start = time.perf_counter()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
        resources = ResourceMonitor(process.pid, log_path.with_suffix(".resources.jsonl"))
        resources.start()
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
            session = {"command": command, "offloaded_layers": offloaded, "gpu_device": gpu if device == "gpu" else None,
                       "startup_seconds": time.perf_counter()-startup_start}
            yield base_url, session
            session.update(resources.summary())
        finally:
            resources.stop()
            if "session" in locals():
                session.update(resources.summary())
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def benchmark_loaded_model(base_url: str, model: dict, questions: list[dict], *, repeats: int, max_tokens: int,
                           thinking: str, timeout: float, progress=None, device="", completed=None,
                           connect_timeout=15, idle_timeout=120):
    progress = progress or ProgressReporter(len(questions)*repeats)
    progress.update('warmup', f"{model['name']} / {device.upper()}（計測外）")
    clear_prompt_cache(base_url)
    model_settings = {key:model[key] for key in ('reasoning_effort','sampling') if key in model}
    warmup = measure_response(base_url, model['id'], '日本語でひとこと挨拶してください。', max_tokens=16,
                              thinking=thinking, timeout=timeout, connect_timeout=connect_timeout, idle_timeout=idle_timeout, **model_settings)
    if warmup['status'] in {'error','timeout','disconnected','cancelled'}:
        if warmup['status'] == 'cancelled':
            raise KeyboardInterrupt
        raise ValueError(f"慣らし運転に失敗: {warmup.get('error')}")
    for number, question in enumerate(questions, 1):
        for repeat in range(1, repeats+1):
            if completed and (question['id'],repeat) in completed:
                continue
            history = []
            prompts = [question['prompt']] + question.get('followups', [])
            for turn, prompt in enumerate(prompts, 1):
                history.append({'role':'user','content':prompt})
                progress.update('preflight', f"{question['id']} / 全文を切り詰めず確認")
                payload = request_payload(model['id'], prompt, max_tokens=max_tokens, thinking=thinking, messages=history, **model_settings)
                info = preflight_input(base_url, payload)
                progress.log(f"[入力] {question['id']} / {info['input_tokens']:,} tokens / 文脈長{info['effective_context_tokens']:,} / 出力に使える残り{info['available_output_tokens']:,}")
                common = {'question_id':question['id'], 'category':question['category'], 'prompt':question['prompt'],
                          'repeat':repeat, 'turn':turn, 'turns':len(prompts), 'preflight':info,
                          'request_messages':[message.copy() for message in history],
                          'measurement_mode':'conversation' if len(prompts)>1 else 'independent'}
                if not info['available_output_tokens']:
                    result = {'status':'input_too_long','error':'全文入力が実効文脈長に収まりません。--context-size を増やして別条件で実行してください。'}
                else:
                    clear_prompt_cache(base_url)
                    progress.update('waiting', f"{model['name']} / {device.upper()} / 問{number}/{len(questions)} {question['category']} ({question['id']}) / {repeat}/{repeats}回目 / 会話{turn}/{len(prompts)}",
                                    response_timeout=timeout,
                                    compact_detail=f"{model['name']} {device.upper()} | {question['category']}{number}/{len(questions)}・{repeat}/{repeats}回")
                    result = measure_response(base_url, model['id'], prompt, max_tokens=max_tokens, thinking=thinking,
                                              timeout=timeout, connect_timeout=connect_timeout, idle_timeout=idle_timeout,
                                              messages=history, **model_settings)
                row = {**common, **result}
                row['quality_checks'] = evaluate_answer(question, row)
                yield row
                if result['status'] == 'cancelled':
                    raise KeyboardInterrupt
                if result['status'] in {'timeout','error','disconnected'}:
                    return  # restart before resuming; don't measure pending inference
                if result['status'] != 'ok':
                    break
                history.append({'role':'assistant','content':result.get('answer','')})


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["model_id"], row["device"], row.get("question_id"), row.get("turn",1), row.get("measurement_mode","independent"))
        groups.setdefault(key, []).append(row)
    summaries = []
    for (model, device, question, turn, mode), group in groups.items():
        good = [row for row in group if row["status"] == "ok"]
        not_run = sum(row["status"] == "not_run" for row in group)
        summary = {"model_id": model, "device": device, "question_id": question,
                   "turn":turn, "measurement_mode":mode, "samples": len(group), "completed_answers": len(good),
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


def run_signature(models, questions, args, runtime_sha):
    settings = {key:getattr(args,key,default) for key,default in
                [('devices',['gpu','cpu']),('all_cpu',False),('context_size',16384),('threads',8),('repeats',5),
                 ('max_tokens',-1),('thinking','auto'),('timeout',300),('connect_timeout',15),('idle_timeout',120),('gpu_device',None)]}
    environment = json.loads((ROOT/'.local/environment.json').read_text()) if (ROOT/'.local/environment.json').exists() else {}
    data = {'runtime_files':environment.get('runtime',{}).get('files'), 'models':models, 'questions':[{k:q.get(k) for k in ('id','category','prompt','followups','checks')} for q in questions],
            'settings':settings, 'runtime_sha256':runtime_sha,
            'code':{str(p.relative_to(ROOT)):sha256_file(p) for p in sorted((ROOT/'scripts').glob('*.py'))}}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run_benchmark(models: list[dict], questions: list[dict], args: argparse.Namespace,
                  progress: ProgressReporter | None = None) -> Path:
    validate_run_settings(models, args)
    sessions = planned_sessions(models,args)
    total = len(sessions)*sum(1+len(q.get('followups',[])) for q in questions)*args.repeats
    progress = progress or ProgressReporter(total)
    paths = {}
    for model in models:
        if any(m['id']==model['id'] for m,_ in sessions):
            progress.update('verifying',model['name'])
            path = find_local_model(model,args.model_dir,getattr(args,'cache_dir',None))
            if path is None:
                raise ValueError(f"重みがありません。--models {model['id']} または --cache-dir 保存先を確認してください。")
            paths[model['id']] = path
    prefix = server_prefix(args.server_bin)
    gpu,inventory = select_gpu(prefix,args.gpu_device) if 'gpu' in args.devices else (None,None)
    runtime_sha = sha256_file(Path(prefix[0]))
    signature = run_signature(models,questions,args,runtime_sha)
    resume = getattr(args,'resume',None)
    run_dir = resume if resume else args.output_dir/datetime.now().astimezone().strftime('%Y%m%dT%H%M%S-%f')
    if resume:
        metadata = json.loads((run_dir/'metadata.json').read_text())
        if metadata.get('run_signature') != signature:
            raise ValueError('再開条件が一致しません（入力・モデル・実行基盤・コード・設定）。別の実行として開始してください。')
        rows = [json.loads(line) for line in (run_dir/'results.jsonl').read_text().splitlines()]
        progress.log('[再開] 完了済みの課題をスキップし、未完了の課題を再試行します。過去の試行も残します。')
    else:
        run_dir.mkdir(parents=True,exist_ok=False)
        suite_path = getattr(args,'questions_file',DEFAULT_QUESTION_FILE)
        suite_metadata = json.loads(suite_path.read_text())
        shutil.copyfile(suite_path,run_dir/'suite-definition.json')
        resolved = {**suite_metadata,'materials':{},'material_sources':{},'questions':questions}
        (run_dir/'input-suite.json').write_text(json.dumps(resolved,ensure_ascii=False,indent=2)+'\n')
        snapshots = {q['material_key']:q['material_snapshot'].copy() for q in questions if 'material_snapshot' in q}
        for key,snapshot in snapshots.items():
            destination = run_dir/'input-pages'/hashlib.sha256(key.encode()).hexdigest()[:16]
            destination.mkdir(parents=True)
            for filename in ('page.html','page.txt','snapshot.json'):
                shutil.copyfile(Path(snapshot['snapshot_dir'])/filename,destination/filename)
            snapshot['run_snapshot_dir'] = str(destination)
        version = subprocess.run(prefix[:1]+['--version'],capture_output=True,text=True,timeout=30)
        metadata = {'schema_version':4,'started_at':datetime.now().astimezone().isoformat(),'run_signature':signature,
                    'platform':{'system':platform.system(),'version':platform.mac_ver()[0] if platform.system()=='Darwin' else platform.release(),'architecture':platform.machine()},
                    'runtime_version':(version.stdout+version.stderr).strip(),'runtime_binary_sha256':runtime_sha,
                    'environment_lock':json.loads((ROOT/'.local/environment.json').read_text()) if (ROOT/'.local/environment.json').exists() else None,
                    'gpu_inventory':inventory,'models':models,'model_paths':paths,'questions':questions,'settings':vars(args).copy(),
                    'question_suite':{'path':suite_path,'sha256':sha256_file(suite_path),'resolved_sha256':sha256_file(run_dir/'input-suite.json'),
                                      'suite_id':suite_metadata.get('suite_id'),'source':suite_metadata.get('source'),'material_snapshots':snapshots},
                    'planned_responses':total,'sessions':[],
                    'timing_scope':'HTTP送信開始からSSE [DONE]まで。準備、tokenize、ロード、warmup、KV消去、停止、保存を除く。',
                    'resource_sampling':'別プロセスで2秒ごと。速度へ微小な影響があり得るため全実行で同条件。'}
        try:
            metadata['git_commit'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
            metadata['git_dirty'] = bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True))
        except subprocess.SubprocessError:
            metadata['git_commit'] = None
        rows = []
    effective = latest_rows(rows)
    complete = set()
    for model,device in sessions:
        for q in questions:
            for repeat in range(1,args.repeats+1):
                turns = [r for r in effective if (r['model_id'],r['device'],r['question_id'],r['repeat'])==(model['id'],device,q['id'],repeat)]
                if len(turns)==1+len(q.get('followups',[])) and all(r['status']=='ok' for r in turns):
                    complete.add((model['id'],device,q['id'],repeat))
    seeded = [r for r in effective if (r['model_id'],r['device'],r['question_id'],r['repeat']) in complete]
    progress.state.update(completed=len(seeded),succeeded=len(seeded),failed=0,not_run=0)
    progress.update('preparing',f"測定予定{total}応答 / 完了済み{len(seeded)}応答",result_dir=str(run_dir))
    progress.log(f'[保存先] {run_dir}')
    def persist():
        current = latest_rows(rows)
        progress.seed(current)
        metadata['progress'] = progress.state.copy()
        (run_dir/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str)+'\n')
        current = latest_rows(rows)
        (run_dir/'summary.json').write_text(json.dumps(summarize(current),ensure_ascii=False,indent=2)+'\n')
        write_reports(run_dir,rows,metadata)
    persist()
    with (run_dir/'results.jsonl').open('a',encoding='utf-8') as output, (run_dir/'answers.md').open('a',encoding='utf-8') as answers:
        if not resume:
            answers.write('# ローカルモデルの回答\n\n自然終了と品質評価は別。途中回答・未実行・過去の試行も残します。\n\n')
        def record(row):
            if 'question_id' in row:
                question = next(q for q in questions if q['id']==row['question_id'])
                row.setdefault('measurement_mode','conversation' if question.get('followups') else 'independent')
            rows.append(row)
            output.write(json.dumps(row,ensure_ascii=False)+'\n')
            output.flush()
            if 'question_id' not in row:
                progress.log(f"[起動・準備失敗] {row['model_id']} / {row['device'].upper()}: {row['error']}")
                return
            progress.advance(row)
            answers.write(f"## {row['model_id']} / {row['device'].upper()} / {row['question_id']} / {row['repeat']}回目 / 会話{row.get('turn',1)} / 試行{row['attempt']}\n\n")
            answers.write(f"状態: {STATUS.get(row['status'],row['status'])} / 全体: {row.get('total_seconds')}秒 / 本文開始: {row.get('ttfa_seconds')}秒\n\n")
            for label,body in [('本文',row.get('answer') or row.get('error') or '本文なし'),('思考',row.get('reasoning') or '思考なし')]:
                answers.write(f"### {label}\n\n> "+body.replace('\n','\n> ')+'\n\n')
            answers.flush()
            if getattr(args,'show_answers',False) and row.get('answer'):
                progress.log(row['answer'])
        try:
            for model,device in sessions:
                pending = [(q,r) for q in questions for r in range(1,args.repeats+1) if (model['id'],device,q['id'],r) not in complete]
                if not pending:
                    continue
                attempts = {(q['id'],r):1+max((row.get('attempt',1) for row in rows if
                            row.get('question_id')==q['id'] and row.get('repeat')==r and row['model_id']==model['id'] and row['device']==device),default=0) for q,r in pending}
                seen,failure = set(),'前の応答が停止したため未実行。保存先を --resume で再開できます。'
                progress.update('loading',f"{model['name']} / {device.upper()}（計測外）",log_file=str(run_dir/f"{model['id']}-{device}.log"))
                try:
                    with load_model(prefix,model,paths[model['id']],device,run_dir,port=args.port,context_size=args.context_size,threads=args.threads,gpu=gpu) as (url,session):
                        session.update(model_id=model['id'],device=device)
                        metadata['sessions'].append(session)
                        progress.log(f"[起動完了] {model['name']} / {device.upper()} / {session.get('startup_seconds',0):.2f}秒 / GPU配置{session['offloaded_layers']}層")
                        skip = {(q['id'],r) for q in questions for r in range(1,args.repeats+1) if (model['id'],device,q['id'],r) in complete}
                        for result in benchmark_loaded_model(url,model,questions,repeats=args.repeats,max_tokens=args.max_tokens,thinking=args.thinking,timeout=args.timeout,
                                                             connect_timeout=getattr(args,'connect_timeout',15),idle_timeout=getattr(args,'idle_timeout',120),
                                                             progress=progress,device=device,completed=skip):
                            seen.add((result['question_id'],result['repeat'],result.get('turn',1)))
                            record({'model_id':model['id'],'device':device,'thinking_setting':args.thinking,
                                    'attempt':attempts[(result['question_id'],result['repeat'])],**result})
                except (OSError,ValueError,TimeoutError,subprocess.SubprocessError) as error:
                    progress.state['setup_failures'] += 1
                    failure = str(error)
                    record({'model_id':model['id'],'device':device,'status':'setup_error','error':failure})
                finally:
                    unrun = 0
                    for question,repeat in pending:
                        for turn in range(1,2+len(question.get('followups',[]))):
                            if (question['id'],repeat,turn) not in seen:
                                record({'model_id':model['id'],'device':device,'question_id':question['id'],'category':question['category'],
                                        'prompt':question['prompt'],'repeat':repeat,'turn':turn,'attempt':attempts[(question['id'],repeat)],'status':'not_run','error':failure})
                                unrun += 1
                    if unrun:
                        progress.log(f"[未実行] {model['name']} / {device.upper()}: {unrun}応答。理由: {failure}")
                    persist()
        finally:
            existing = {(r['model_id'],r['device'],r['question_id'],r['repeat'],r.get('turn',1)) for r in latest_rows(rows)}
            for model,device in sessions:
                for question in questions:
                    for repeat in range(1,args.repeats+1):
                        for turn in range(1,2+len(question.get('followups',[]))):
                            if (model['id'],device,question['id'],repeat,turn) not in existing:
                                record({'model_id':model['id'],'device':device,'question_id':question['id'],'category':question['category'],
                                        'prompt':question['prompt'],'repeat':repeat,'turn':turn,'attempt':1,'status':'not_run',
                                        'error':'全体が中断したため未実行。--resumeで再開できます。'})
            persist()
    progress.log(f"[測定結果] 処理済み{progress.state['completed']}/{total} / 回答完了{progress.state['succeeded']} / 応答失敗・打切り{progress.state['failed']} / 未実行{progress.state['not_run']} / 起動・準備失敗{progress.state['setup_failures']}")
    progress.log(f"[確認] {run_dir/'report.html'}\n[再開] ./benchmark.sh --resume {run_dir}")
    progress.update('measurement_complete',str(run_dir))
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
    parser.add_argument("--context-size", type=int, default=16384, help="文脈長。全文を切り詰めず、収まらない入力はエラーにする")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=-1, help="生成数の上限。既定-1は上限なし（文脈長と時間制限は別）")
    parser.add_argument("--thinking", choices=("auto", "off", "on"), default="auto")
    parser.add_argument("--timeout", type=float, default=300, help="応答全体の時間制限（秒）")
    parser.add_argument("--connect-timeout", type=float, default=15, help="API接続待ちの上限（秒）")
    parser.add_argument("--idle-timeout", type=float, default=120, help="無通信の上限（秒）。総時間制限も同時に適用")
    parser.add_argument("--resume", type=Path, help="同じ入力・モデル・設定で未完了を再試行する実行フォルダ")
    parser.add_argument("--progress-file", type=Path, help="進捗JSONの保存先")
    parser.add_argument("--no-progress", action="store_true", help="別プロセスによる待ち時間の表示を止める")
    parser.add_argument("--show-answers", action="store_true", help="回答完了後に本文を端末にも表示する")
    args = parser.parse_args()
    if args.resume:
        args.resume = args.resume.resolve()
        try:
            original = json.loads((args.resume/'metadata.json').read_text())
        except (OSError,ValueError) as error:
            parser.error(f'再開する実行記録を読み込めません: {error}')
        if not isinstance(original,dict) or not isinstance(original.get('settings'),dict) or not isinstance(original.get('models'),list) or not original.get('run_signature'):
            parser.error('再開するmetadata.jsonの構造が不正です。schema_version 4の実行記録が必要です。')
        if any(not isinstance(m,dict) or m.get('id') not in {model['id'] for model in MODEL_URLS} for m in original['models']):
            parser.error('再開する実行記録のモデルIDが不正です。')
        allowed = {'--resume','--no-progress','--show-answers','--progress-file'}
        forbidden = [word.split('=')[0] for word in sys.argv[2:] if word.startswith('--') and word.split('=')[0] not in allowed]
        if forbidden:
            parser.error('再開時は設定変更できません: '+', '.join(forbidden))
        for key,value in original['settings'].items():
            if key not in {'resume','action','no_progress','show_answers','progress_file'} and hasattr(args,key):
                setattr(args,key, Path(value) if key in {'model_dir','output_dir','questions_file'} else value)
        if args.cache_dir:
            args.cache_dir = [Path(p) for p in args.cache_dir]
        args.models = [m['id'] for m in original['models']]
        args.questions_file = args.resume/'input-suite.json'
    models_by_id = {model["id"]: model for model in MODEL_URLS}
    model_ids = args.models if args.models is not None else [model["id"] for model in MODEL_URLS if model.get("default", True)]
    if args.include_challenge and "qwen3.8-27b-iq2-s" not in model_ids:
        model_ids = model_ids + ["qwen3.8-27b-iq2-s"]
    models = [models_by_id[model_id] for model_id in model_ids]
    if min(args.repeats, args.threads, args.context_size, args.timeout, args.connect_timeout, args.idle_timeout) <= 0 or (args.max_tokens != -1 and args.max_tokens <= 0):
        parser.error("繰り返し、文脈長等は正数、生成上限は-1または正数が必要です。")
    if not 1 <= args.port <= 65535 or (args.max_tokens != -1 and args.max_tokens >= args.context_size):
        parser.error("ポートまたは出力トークン数の設定が不正です。")
    progress = None
    try:
        suite, suite_questions = load_question_suite(args.questions_file,
            fetch_pages=args.action in ("all", "run"), allow_missing_pages=args.action in ("plan", "download"))
        questions_by_id = {question["id"]: question for question in suite_questions}
        question_ids = args.questions if args.questions is not None else list(questions_by_id)
        unknown = set(question_ids) - set(questions_by_id)
        if unknown:
            raise ValueError(f"不明な質問ID: {sorted(unknown)}。利用可能: {list(questions_by_id)}")
        for values in (model_ids, question_ids, args.devices):
            if len(values) != len(set(values)):
                raise ValueError("モデル・質問・デバイスの指定は重複させないでください。")
        questions = [questions_by_id[question_id] for question_id in question_ids]
        total = len(planned_sessions(models, args)) * sum(1+len(q.get("followups",[])) for q in questions) * args.repeats
        if args.action == "plan":
            print(json.dumps({"models": models, "questions": [{key: value for key, value in q.items() if key != "prompt"} for q in questions],
                              "source": suite.get("source"), "material_sources": suite.get("material_sources"),
                              "material_snapshots": suite.get("material_snapshots"),
                              "settings": vars(args), "planned_responses": total,
                              "note": "planはダウンロードもモデル起動も行いません。"}, ensure_ascii=False, indent=2, default=str))
        elif args.action == "download":
            download_models(models, args.model_dir, args.cache_dir)
        else:
            for key, snapshot in suite.get("material_snapshots", {}).items():
                print(f"入力ページ全文: {snapshot['text_characters']:,}文字 / SHA-256 {snapshot['text_sha256']}\n"
                      f"確認用テキスト: {Path(snapshot['snapshot_dir']) / 'page.txt'}", flush=True)
            validate_run_settings(models, args)
            args.progress_file = args.progress_file or ROOT / f".local/progress/benchmark-{os.getpid()}.json"
            progress = ProgressReporter(total, args.progress_file)
            progress.update("preparing", f"{len(questions)}問 × {args.repeats}回、合計{total}応答")
            with progress_monitor(progress, enabled=not args.no_progress):
                try:
                    if args.action == "all":
                        progress.update("runtime", "実行基盤の確認・導入")
                        lock_file = ROOT/'.local/environment.json'
                        stored = json.loads(lock_file.read_text()) if lock_file.exists() else None
                        if stored and (not args.server_bin or Path(args.server_bin).resolve()==(ROOT/stored['runtime']['binary']).resolve()):
                            lock = read_lock()
                            prefix = [str(ROOT/lock['runtime']['binary'])] + lock['runtime'].get('arguments', [])
                            progress.log('[実行環境] setup.shで固定したllama.cppを使用（整合性確認済み）')
                        else:
                            # Direct Python usage remains available to developers with a custom runtime.
                            prefix = ensure_runtime(args.server_bin, progress=progress)
                        args.server_bin = prefix[0]
                        if "gpu" in args.devices:
                            select_gpu(prefix, args.gpu_device)
                        active_ids = {model["id"] for model, _ in planned_sessions(models, args)}
                        download_models([model for model in models if model["id"] in active_ids], args.model_dir, args.cache_dir, progress)
                        if platform.system() == "Darwin":
                            progress.update("hardware", "測定前のPC状態を保存")
                            subprocess.run([sys.executable, str(ROOT / "scripts/probe_hardware.py"),
                                            "--output", str(ROOT / ".local/hardware-before-benchmark.json")], check=True, stdout=subprocess.DEVNULL)
                    run_dir = run_benchmark(models, questions, args, progress)
                    if args.action == "all" and platform.system() == "Darwin":
                        shutil.copyfile(ROOT / ".local/hardware-before-benchmark.json", run_dir / "hardware-before.json")
                        progress.update("hardware", "測定後のPC状態を保存")
                        subprocess.run([sys.executable, str(ROOT / "scripts/probe_hardware.py"),
                                        "--output", str(run_dir / "hardware-after.json")], check=True, stdout=subprocess.DEVNULL)
                    progress.update("finished", f"結果: {run_dir}")
                except BaseException:
                    directory = progress.state.get('result_dir')
                    if directory:
                        progress.log(f"[停止時の集計] 回答完了{progress.state['succeeded']} / 応答失敗・打切り{progress.state['failed']} / 未実行{progress.state['not_run']}")
                        progress.log(f"[保存済み結果] {directory}\n[再開] ./benchmark.sh --resume {directory}")
                    progress.update("failed", "測定は停止しました。保存済みの本文・思考・結果は残っています。")
                    raise
            rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()]
            return 1 if any(row["status"] != "ok" for row in latest_rows(rows)) else 0
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました。保存済みの結果は残っています。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
