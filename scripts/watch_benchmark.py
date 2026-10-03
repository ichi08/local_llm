#!/usr/bin/env python3
"""The sole console writer while benchmarking; never touches the API."""
import argparse
import json
import os
from pathlib import Path
import sys
import shutil
import signal
import unicodedata
import time

PHASES = {"preparing": "準備", "runtime": "実行環境", "verifying": "モデルの整合性確認",
          "downloading": "モデルをダウンロード中", "hardware": "PC状態を記録", "loading": "モデルを起動中",
          "warmup": "慣らし運転", "preflight": "入力の長さを確認", "waiting": "回答待ち",
          "finished": "終了", "failed": "停止"}
STATUS = {"ok": "自然終了（品質は別途評価）", "truncated": "文脈／出力上限で終了", "timeout": "時間制限",
          "disconnected": "通信切断", "cancelled": "中断", "error": "応答エラー", "no_answer": "本文なし",
          "not_run": "未実行", "input_too_long": "入力が文脈長を超過", "setup_error": "起動・準備失敗"}


def format_progress(state, now, *, compact=False):
    phase = state.get("phase", "preparing")
    elapsed = max(0, now - state.get("phase_started_at", now))
    detail = state.get("detail", "")
    if phase == "downloading":
        path = Path(state["download_path"])
        try:
            size = path.stat().st_size
        except OSError:
            size = 0  # .part may have just been atomically renamed after verification
        total = state.get("download_bytes", 0)
        rate = max(0, size - state.get("download_initial_bytes", 0)) / max(elapsed, .001)
        fraction = min(1, size / total) if total else 0
        remaining = f" / 残り約{(total-size)/rate:.0f}秒" if rate > 0 else ""
        if compact:
            return f"取得 {fraction:.1%} {size/10**9:.2f}/{total/10**9:.2f}GB | {detail} | {rate/10**6:.1f}MB/秒"
        return (f"モデルをダウンロード中 | {detail} | {size/10**9:.2f} / {total/10**9:.2f} GB "
                f"({fraction:.1%}) | {rate/10**6:.1f} MB/秒{remaining}")
    if phase == "waiting":
        if compact:
            return f"回答待ち | {state.get('compact_detail',detail)} | {elapsed:.0f}/{state.get('response_timeout',300):g}秒"
        return f"回答待ち | {detail} | 経過{elapsed:.0f}秒 / 時間制限{state.get('response_timeout', 300):g}秒"
    if phase in ("recorded", "finished", "failed", "measurement_complete"):
        n, total = state.get("completed", 0), state.get("total", 0)
        fraction = n / total if total else 0
        return (f"処理済み {n}/{total} {fraction:.1%} | 回答完了{state.get('succeeded', 0)} / "
                f"応答失敗・打切り{state.get('failed', 0)} / 未実行{state.get('not_run', 0)} / "
                f"起動・準備失敗{state.get('setup_failures', 0)} | 経過{elapsed:.0f}秒")
    return f"{PHASES.get(phase, phase)} | {detail} | 経過{elapsed:.0f}秒"


def display_width(text):
    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)


def fit_line(text, columns):
    """Keep the deadline/transfer tail while avoiding wrapped live-line remnants."""
    width = max(10, columns-1)
    if display_width(text) <= width:
        return text
    tail = text.rsplit(' | ',1)[-1]
    while display_width(tail) > min(28,width//2):
        tail = tail[1:]
    budget = width-display_width(tail)-3
    prefix = ''
    for char in text:
        if display_width(prefix+char) > budget:
            break
        prefix += char
    return prefix+' … '+tail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--progress-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    tty = sys.stdout.isatty()
    offset, last, last_print, live = 0, None, 0, False
    event_path = args.progress_file.with_suffix(".events.jsonl")
    def clear():
        nonlocal live
        if live:
            sys.stdout.write("\r\033[2K")
            live = False
    def drain_events():
        nonlocal offset
        with event_path.open(encoding='utf-8') as events:
            events.seek(offset)
            while True:
                line_start = events.tell()
                line = events.readline()
                if not line or not line.endswith('\n'):
                    offset = line_start
                    break
                clear()
                print(json.loads(line)['message'], flush=True)
                offset = events.tell()
    try:
        while True:
            try:
                drain_events()
                state = json.loads(args.progress_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(.1)
                continue
            now = time.time()
            phase = state.get("phase")
            key = (phase, state.get("phase_started_at"))
            # Static events preserve every transition. Only long waits get heartbeat output.
            if phase in {"downloading", "waiting", "loading", "runtime", "verifying", "warmup", "preflight"}:
                if tty and (key != last or now-last_print >= 1):
                    clear()
                    sys.stdout.write("\r" + fit_line(format_progress(state, now, compact=True), shutil.get_terminal_size().columns))
                    sys.stdout.flush()
                    live = True
                    last_print = now
                elif not tty and now-last_print >= 30 and now-state.get("phase_started_at", now) >= 30:
                    print(format_progress(state, now), flush=True)
                    last_print = now
            else:
                clear()
            last = key
            if phase in {"finished", "failed"}:
                # A terminal state may arrive between the first event read and state read.
                drain_events()
                clear()
                return
            if args.parent_pid:
                try:
                    os.kill(args.parent_pid, 0)
                except ProcessLookupError:
                    clear()
                    return
            time.sleep(.1)
    finally:
        clear()
        signal.signal(signal.SIGINT, previous_sigint)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
