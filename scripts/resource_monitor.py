#!/usr/bin/env python3
"""Separate-process sampling. RSS is not unified/GPU memory consumption."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import time


class ResourceMonitor:
    def __init__(self, pid, path):
        self.pid, self.path, self.process = pid, path, None
    def start(self):
        if isinstance(self.pid, int):
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(self.pid), str(self.path)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
    def summary(self):
        rows = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return {'peak_process_rss_bytes': max((r['rss_bytes'] for r in rows), default=None),
                'resource_samples':len(rows), 'resource_sampling_seconds':2,
                'memory_note':'RSSはプロセス常駐量。GPU/共有メモリの全使用量ではない。システム値はPC全体。'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pid', type=int)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    with args.path.open('a') as output:
        while True:
            result = subprocess.run(['ps','-o','rss=','-p',str(args.pid)], capture_output=True, text=True)
            if not result.stdout.strip():
                return
            row = {'time':time.time(), 'rss_bytes':int(result.stdout.strip())*1024}
            if platform.system() == 'Darwin':
                for name, command in (('swap',['sysctl','-n','vm.swapusage']),
                                      ('pressure',['sysctl','-n','kern.memorystatus_vm_pressure_level'])):
                    value = subprocess.run(command, capture_output=True, text=True)
                    row[name] = value.stdout.strip() if value.returncode == 0 else None
            output.write(json.dumps(row)+'\n')
            output.flush()
            time.sleep(2)

if __name__ == '__main__':
    main()
