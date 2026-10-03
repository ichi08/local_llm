#!/usr/bin/env python3
"""Prepare a project venv and freeze a native runtime, outside measurements."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / '.local/environment.json'


def sha(path):
    with path.open('rb') as source:
        digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def read_lock():
    if not LOCK.exists():
        raise ValueError('セットアップがありません。先に ./setup.sh を実行してください。')
    lock = json.loads(LOCK.read_text())
    for relative, expected in lock['runtime']['files'].items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise ValueError('固定した実行環境が変更されています。./setup.sh --update-runtime で明示的に更新してください。')
    if platform.python_version() != lock['python']['version']:
        raise ValueError('固定したPythonのバージョンが変わりました。./setup.sh --update-runtime を再実行してください。')
    if platform.machine() != lock['python']['architecture']:
        raise ValueError('セットアップ時とPythonの実行アーキテクチャが異なります。./setup.sh を再実行してください。')
    libs = ROOT / lock['runtime']['library_dir']
    if platform.system() == 'Darwin':
        os.environ['DYLD_LIBRARY_PATH'] = str(libs)
    return lock


def freeze_runtime(binary):
    if __package__:
        from .benchmark_models import ensure_runtime
    else:
        from benchmark_models import ensure_runtime
    prefix = ensure_runtime(binary, use_lock=False)
    target = ROOT / '.local/runtime/frozen'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        stage = Path(temporary)
        (stage/'bin').mkdir()
        (stage/'lib').mkdir()
        shutil.copy2(prefix[0], stage/'bin/llama-server')
        # Freeze the whole transitive dylib closure; loader search uses this local directory.
        if platform.system() == 'Darwin':
            pending = [Path(prefix[0]).resolve()]
            seen = set()
            search = [pending[0].parent.parent/'lib']
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                listing = subprocess.check_output(['otool','-L',str(current)], text=True)
                for line in listing.splitlines()[1:]:
                    dependency = line.strip().split(' (')[0]
                    if dependency.startswith(('/usr/lib/', '/System/')):
                        continue
                    path = Path(dependency)
                    if dependency.startswith('@'):
                        candidates = [current.parent/path.name] + [directory/path.name for directory in search]
                        path = next((p for p in candidates if p.exists()), None)
                        if path is None:
                            raise ValueError(f'依存ライブラリを固定できません: {dependency}')
                    if not path.exists():
                        raise ValueError(f'依存ライブラリがありません: {dependency}')
                    resolved = path.resolve()
                    search.append(resolved.parent)
                    destination = stage/'lib'/path.name
                    if destination.exists():
                        if sha(destination) != sha(resolved):
                            raise ValueError(f'同名の依存ライブラリが競合: {path.name}')
                    else:
                        shutil.copy2(resolved, destination)
                    pending.append(resolved)
        elif platform.system() != 'Linux':
            raise ValueError('現在のネイティブセットアップはmacOS/Linux向けです。')
        env = os.environ.copy()
        if platform.system() == 'Darwin':
            env['DYLD_LIBRARY_PATH'] = str(stage/'lib')
        version = subprocess.check_output([str(stage/'bin/llama-server'),'--version'], env=env, stderr=subprocess.STDOUT, text=True)
        backup = target.with_name('previous-'+datetime.now().strftime('%Y%m%dT%H%M%S-%f'))
        if target.exists():
            target.rename(backup)
        try:
            shutil.copytree(stage, target)
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                backup.rename(target)
            raise
    return {'binary': str((target/'bin/llama-server').relative_to(ROOT)),
            'library_dir': str((target/'lib').relative_to(ROOT)), 'version': version.strip(), 'arguments':prefix[1:],
            'files': {str(p.relative_to(ROOT)): sha(p) for p in sorted(target.rglob('*')) if p.is_file()},
            'dependency_policy': 'frozen-dylib-closure' if platform.system() == 'Darwin' else 'host-system-libraries'}


def main():
    parser = argparse.ArgumentParser(description='PC調査、.venv作成、ネイティブllama.cppの固定。モデルの取得はbenchmark.shで行います。')
    parser.add_argument('--server-bin')
    parser.add_argument('--update-runtime', action='store_true', help='固定済み実行基盤を明示的に入れ替える')
    parser.add_argument('--browser', action='store_true', help='JavaScript実行後のページ取得用Chromiumを追加する')
    parser.add_argument('--output', type=Path, default=ROOT/'.local/hardware.json')
    args = parser.parse_args()
    print('[1/3] PCを調査しています', flush=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/probe_hardware.py'), '--output', str(args.output)], check=True)
    print('[2/3] プロジェクト専用の仮想環境 .venv を準備しています', flush=True)
    venv = ROOT/'.venv'
    python = venv/'bin/python'
    if not python.exists():
        subprocess.run([sys.executable,'-m','venv',str(venv)], check=True)
    check = subprocess.run([str(python),'-c','import sys, platform; assert sys.version_info >= (3,10); print(platform.machine())'], capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != platform.machine():
        raise ValueError('.venvのPythonまたはアーキテクチャが不適合です。.venvを別名へ退避して ./setup.sh を再実行してください。')
    # The core runner uses only stdlib; browser support is deliberately opt-in.
    if args.browser:
        log = ROOT/'.local/browser-install.log'
        print(f'[追加準備] JavaScriptページ取得用のChromiumを導入しています。詳細ログ: {log}', flush=True)
        with log.open('w') as output:
            for command in ([str(python),'-m','pip','install','-r',str(ROOT/'requirements-browser.txt')],
                            [str(python),'-m','playwright','install','chromium']):
                try:
                    subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)
                except subprocess.CalledProcessError as error:
                    raise ValueError(f'ブラウザー導入に失敗しました。詳細: {log}') from error
    print('[3/3] llama.cppと依存ライブラリをプロジェクト内へ固定しています', flush=True)
    if LOCK.exists() and not args.update_runtime and not args.server_bin:
        lock = read_lock()
        print('固定済みの実行基盤を再利用しました', flush=True)
    else:
        runtime = freeze_runtime(args.server_bin)
        lock = {'schema_version':1, 'created_at':datetime.now(timezone.utc).isoformat(), 'runtime':runtime,
                'python':{'version':subprocess.check_output([str(python),'-c','import platform; print(platform.python_version())'], text=True).strip(),
                          'architecture':subprocess.check_output([str(python),'-c','import platform; print(platform.machine())'], text=True).strip()},
                'browser': bool(args.browser)}
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n')
    if args.browser:
        lock['browser'] = True
        lock['browser_packages'] = subprocess.check_output([str(python),'-m','pip','freeze'], text=True).splitlines()
        LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n')
    print('セットアップ完了。次は ./benchmark.sh を実行してください。\n仮想環境のactivateは不要です。計画の確認: ./benchmark.sh --plan', flush=True)
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'セットアップ失敗: {error}', file=sys.stderr)
        raise SystemExit(1)
