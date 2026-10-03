#!/usr/bin/env python3
"""Collect an allowlist of macOS specs without saving machine identifiers."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


def run(command: list[str], warnings: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=30
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        # Raw stderr/stdout may contain identifiers; do not copy it into reports.
        warnings.append(f"{command[0]} による {command[1]} の取得に失敗しました。")
        return None


def integer_sysctl(name: str, warnings: list[str]) -> int | None:
    value = run(["/usr/sbin/sysctl", "-n", name], warnings)
    try:
        return int(value) if value is not None else None
    except ValueError:
        warnings.append(f"{name} を数値として読み取れませんでした。")
        return None


def collect(storage_path: Path) -> dict:
    warnings: list[str] = []
    raw = run(
        ["/usr/sbin/system_profiler", "SPHardwareDataType", "SPDisplaysDataType", "-json"],
        warnings,
    )
    try:
        profiler = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        warnings.append("system_profiler のJSONを読み取れませんでした。")
        profiler = {}
    hardware = (profiler.get("SPHardwareDataType") or [{}])[0]
    # Select only public hardware attributes; never serialize the raw profiler.
    gpus = [
        {
            "name": gpu.get("sppci_model", gpu.get("_name")),
            "cores": int(gpu["sppci_cores"])
            if str(gpu.get("sppci_cores", "")).isdigit() else None,
            "metal_support": str(gpu["spdisplays_mtlgpufamilysupport"]).replace("spdisplays_metal", "Metal ")
            if gpu.get("spdisplays_mtlgpufamilysupport") else None,
        }
        for gpu in profiler.get("SPDisplaysDataType", [])
    ]
    cpu_description = str(hardware.get("number_processors", ""))
    # Apple Silicon profiler encoding: proc total:unused:performance:efficiency.
    cpu_match = re.fullmatch(r"proc (\d+):\d+:(\d+):(\d+)", cpu_description)
    swap_raw = run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], warnings)
    swap_match = re.search(r"used\s*=\s*([\d.]+)M", swap_raw or "")
    memory_raw = run(["/usr/bin/memory_pressure"], warnings)
    free_match = re.search(r"System-wide memory free percentage:\s*(\d+)%", memory_raw or "")
    disk = shutil.disk_usage(storage_path)
    return {
        "schema_version": 1,
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "os": {
            "name": "macOS",
            "version": run(["/usr/bin/sw_vers", "-productVersion"], warnings),
            "build": run(["/usr/bin/sw_vers", "-buildVersion"], warnings),
        },
        "hardware": {
            "model_name": hardware.get("machine_name"),
            "model_identifier": hardware.get("machine_model"),
            "chip": hardware.get("chip_type"),
            "physical_cpu_cores": integer_sysctl("hw.physicalcpu", warnings),
            "logical_cpu_cores": integer_sysctl("hw.logicalcpu", warnings),
            "performance_cpu_cores": int(cpu_match[2]) if cpu_match else None,
            "efficiency_cpu_cores": int(cpu_match[3]) if cpu_match else None,
            "memory_bytes": integer_sysctl("hw.memsize", warnings),
            "gpus": gpus,
        },
        "storage": {
            "scope": "プロジェクトを置いたファイルシステム。物理SSDの公称容量とは異なります。",
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        },
        "memory_snapshot": {
            "swap_used_bytes": round(float(swap_match[1]) * 1024**2) if swap_match else None,
            "memory_pressure_free_percent": int(free_match[1]) if free_match else None,
            "note": "瞬間値です。free_percentはモデルに割り当てられる空き容量ではありません。",
        },
        "tools": {
            "python_version": platform.python_version(),
            "python_process_architecture": platform.machine(),
            "commands_available": {
                name: shutil.which(name) is not None
                for name in ("brew", "uv", "ollama", "llama", "llama-server")
            },
        },
        "warnings": warnings,
    }


def gib(value: int | None) -> str:
    return f"{value / 1024**3:.2f} GiB" if value is not None else "未取得"


def main() -> int:
    parser = argparse.ArgumentParser(description="macOSのPCスペックを調査します（導入・ダウンロードなし）。")
    parser.add_argument("--output", type=Path, default=Path(".local/hardware.json"), help="調査JSONの保存先")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("現在の調査スクリプトはmacOSのみ対応しています。")
    try:
        report = collect(Path(__file__).resolve().parent.parent)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as error:
        print(f"調査結果を作成できませんでした: {error}", file=sys.stderr)
        return 1

    hardware = report["hardware"]
    os_info = report["os"]
    storage = report["storage"]
    snapshot = report["memory_snapshot"]
    print("段階1: PCスペック調査")
    print(f"調査日時: {report['collected_at']}")
    print(f"PC: {hardware['model_name'] or '未取得'} / {hardware['chip'] or '未取得'}")
    print(f"OS: macOS {os_info['version']} ({os_info['build']})")
    print(f"CPU: {hardware['physical_cpu_cores'] or '未取得'}コア / メモリ: {gib(hardware['memory_bytes'])}")
    for gpu in hardware["gpus"]:
        print(f"GPU: {gpu['name'] or '未取得'} / {gpu['cores'] or '未取得'}コア / 対応: {gpu['metal_support'] or '未取得'}")
    print(f"ファイルシステム容量: {storage['total_bytes'] / 10**9:.1f} GB / 空き: {storage['free_bytes'] / 10**9:.1f} GB")
    print(f"現在のスワップ使用量: {gib(snapshot['swap_used_bytes'])}")
    print("メモリとスワップは測定時の状態です。速度比較の前に不要なアプリを閉じて再測定してください。")
    print(f"保存先: {args.output}")
    for warning in report["warnings"]:
        print(f"注意: {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
