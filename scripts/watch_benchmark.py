#!/usr/bin/env python3
"""Display elapsed time without adding console I/O to timed HTTP requests."""

import argparse
import json
from pathlib import Path
import time

PHASES = {
    "preparing": "準備", "runtime": "実行基盤", "verifying": "重み確認",
    "downloading": "ダウンロード", "hardware": "PC状態の記録", "loading": "モデルロード",
    "warmup": "ウォームアップ", "clearing_cache": "キャッシュ消去", "waiting": "回答待ち",
    "recorded": "回答保存", "measurement_complete": "測定完了", "finished": "終了", "failed": "停止",
}


def format_progress(state, now):
    total, completed = state.get("total", 0), state.get("completed", 0)
    fraction = min(1, completed / total) if total else 0
    filled = int(fraction * 24)
    bar = "#" * filled + "-" * (24 - filled)
    elapsed = max(0, now - state.get("phase_started_at", now))
    phase = PHASES.get(state.get("phase"), state.get("phase", "準備"))
    counts = (f"成功{state.get('succeeded', 0)} / 応答失敗{state.get('failed', 0)} / "
              f"未実行{state.get('not_run', 0)} / 起動・準備失敗{state.get('setup_failures', 0)}")
    return f"[{bar}] {completed}/{total} {fraction:.1%} | {phase}: {state.get('detail', '')} | 経過{elapsed:.0f}秒 | {counts}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--progress-file", type=Path, required=True)
    args = parser.parse_args()
    last_key, last_print = None, 0
    while True:
        try:
            state = json.loads(args.progress_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.25)
            continue
        now = time.time()
        key = (state.get("phase"), state.get("phase_started_at"), state.get("completed"))
        if key != last_key or now - last_print >= 5:
            print(format_progress(state, now), flush=True)
            last_key, last_print = key, now
        if state.get("phase") in ("finished", "failed"):
            return
        time.sleep(0.25)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
