import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from scripts import setup_environment as env
from scripts import benchmark_models as bench

class EnvironmentLockTests(unittest.TestCase):
    def test_transitive_readonly_libraries_are_frozen_and_hash_checked(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'installed/bin/llama-server'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'EXECUTABLE')
            binary.chmod(0o755)
            lib = root/'installed/lib'
            lib.mkdir()
            for name in ('a.dylib','b.dylib'):
                (lib/name).write_bytes(name.encode())
                (lib/name).chmod(0o444)
            def output(command,**kwargs):
                if command[0] == 'otool':
                    current = Path(command[-1])
                    deps = ['@rpath/a.dylib', str(lib/'b.dylib')] if current.name == 'llama-server' else [str(lib/'b.dylib')] if current.name=='a.dylib' else []
                    return str(current)+':\n'+''.join('\t'+dep+' (compatibility version 0)\n' for dep in deps)
                return 'version: pinned test'
            with patch.object(env,'ROOT',root), patch.object(env,'LOCK',root/'.local/environment.json'), \
                 patch.object(env.platform,'system',return_value='Darwin'), \
                 patch.object(bench,'ensure_runtime',return_value=[str(binary)]), \
                 patch.object(env.subprocess,'check_output',side_effect=output):
                runtime = env.freeze_runtime(None)
                self.assertEqual(len(runtime['files']),3)
                lock = {'runtime':runtime,'python':{'version':platform.python_version(),'architecture':platform.machine()}}
                env.LOCK.write_text(json.dumps(lock))
                self.assertEqual(env.read_lock()['runtime']['version'],'version: pinned test')
                frozen = root/runtime['binary']
                frozen.write_bytes(b'CHANGED')
                with self.assertRaisesRegex(ValueError,'実行環境が変更'):
                    env.read_lock()

if __name__ == '__main__': unittest.main()
