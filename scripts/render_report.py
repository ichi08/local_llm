#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from benchmark_reports import write_reports
parser = argparse.ArgumentParser(description='人間の評価を反映してローカルHTMLレポートを更新します。')
parser.add_argument('run_dir', type=Path)
args = parser.parse_args()
write_reports(args.run_dir, [json.loads(line) for line in (args.run_dir/'results.jsonl').read_text().splitlines()],
              json.loads((args.run_dir/'metadata.json').read_text()))
print(args.run_dir/'report.html')
