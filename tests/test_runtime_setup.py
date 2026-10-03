"""Runtime preparation regressions; no package installs or model downloads."""

from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from scripts import benchmark_models as bench


class RuntimeSetupTests(unittest.TestCase):
    def test_installed_runtime_is_checked_without_installing(self):
        with patch.object(bench, "server_prefix", return_value=["/existing/llama-server"]), \
             patch.object(bench.subprocess, "run", return_value=Mock(stdout="version: test", stderr="")) as run, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(bench.ensure_runtime(None), ["/existing/llama-server"])
        self.assertEqual(run.call_args.args[0], ["/existing/llama-server", "--version"])
        self.assertTrue(run.call_args.kwargs["check"])

    def test_native_homebrew_is_found_outside_path(self):
        def which(name, path=None):
            if name == "llama-server" and path == "/opt/homebrew/bin":
                return "/opt/homebrew/bin/llama-server"
            return None
        with patch.object(bench.platform, "system", return_value="Darwin"), \
             patch.object(bench.shutil, "which", side_effect=which):
            self.assertEqual(bench.server_prefix(None, use_lock=False), ["/opt/homebrew/bin/llama-server"])
            with self.assertRaisesRegex(ValueError, "指定した実行ファイル"):
                bench.server_prefix("missing-explicit-server")

    def test_rosetta_install_runs_homebrew_as_arm64_then_checks_runtime(self):
        with TemporaryDirectory() as temporary:
            with patch.object(bench, "ROOT", Path(temporary)), \
                 patch.object(bench.platform, "system", return_value="Darwin"), \
                 patch.object(bench.platform, "machine", return_value="x86_64"), \
                 patch.object(bench, "server_prefix", side_effect=[ValueError("missing"), ["/opt/homebrew/bin/llama-server"]]), \
                 patch.object(bench.shutil, "which", return_value="/opt/homebrew/bin/brew"), \
                 patch.object(bench.subprocess, "run", return_value=Mock(stdout="version: test", stderr="")) as run, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(bench.ensure_runtime(None), ["/opt/homebrew/bin/llama-server"])
            self.assertEqual(run.call_args_list[0].args[0],
                             ["/usr/bin/arch", "-arm64", "/opt/homebrew/bin/brew", "install", "llama.cpp"])
            self.assertEqual(run.call_args_list[1].args[0], ["/opt/homebrew/bin/llama-server", "--version"])

    def test_intel_homebrew_is_not_forced_to_arm64(self):
        with TemporaryDirectory() as temporary:
            # A temporary prefix also covers user-selected Homebrew installations.
            brew = str(Path(temporary) / "bin/brew")
            with patch.object(bench, "ROOT", Path(temporary)), \
                 patch.object(bench.platform, "system", return_value="Darwin"), \
                 patch.object(bench, "server_prefix", side_effect=[ValueError("missing"), ["/existing/llama-server"]]), \
                 patch.object(bench.shutil, "which", return_value=brew), \
                 patch.object(bench.subprocess, "run", return_value=Mock(stdout="version: test", stderr="")) as run, \
                 redirect_stdout(io.StringIO()):
                bench.ensure_runtime(None)
            self.assertEqual(run.call_args_list[0].args[0], [str(Path(brew).resolve()), "install", "llama.cpp"])

    def test_install_failure_keeps_homebrew_reason_in_local_log(self):
        def fail(command, **kwargs):
            kwargs["stdout"].write("Error: test download failed\n")
            raise subprocess.CalledProcessError(1, command)
        with TemporaryDirectory() as temporary:
            with patch.object(bench, "ROOT", Path(temporary)), \
                 patch.object(bench.platform, "system", return_value="Darwin"), \
                 patch.object(bench, "server_prefix", side_effect=ValueError("missing")), \
                 patch.object(bench.shutil, "which", return_value="/opt/homebrew/bin/brew"), \
                 patch.object(bench.subprocess, "run", side_effect=fail), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "終了コード1.*詳細:"):
                    bench.ensure_runtime(None)
            logs = list((Path(temporary) / ".local/runtime").glob("*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("test download failed", logs[0].read_text())

    def test_explicit_missing_runtime_never_installs(self):
        with patch.object(bench, "server_prefix", side_effect=ValueError("missing")), \
             patch.object(bench.subprocess, "run", side_effect=AssertionError("must not install")):
            with self.assertRaises(ValueError):
                bench.ensure_runtime("missing-server")

    def test_missing_homebrew_has_actionable_error(self):
        with patch.object(bench.platform, "system", return_value="Darwin"), \
             patch.object(bench, "server_prefix", side_effect=ValueError("missing")), \
             patch.object(bench.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Homebrew.*--server-bin"):
                bench.ensure_runtime(None)

    def test_install_without_server_reports_local_log(self):
        with TemporaryDirectory() as temporary:
            with patch.object(bench, "ROOT", Path(temporary)), \
                 patch.object(bench.platform, "system", return_value="Darwin"), \
                 patch.object(bench, "server_prefix", side_effect=ValueError("missing")), \
                 patch.object(bench.shutil, "which", side_effect=lambda name, **kwargs:
                              "/opt/homebrew/bin/brew" if name == "brew" else None), \
                 patch.object(bench.subprocess, "run", return_value=Mock()), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "導入後も.*詳細:"):
                    bench.ensure_runtime(None)

    def test_non_macos_never_installs_homebrew(self):
        with patch.object(bench.platform, "system", return_value="Linux"), \
             patch.object(bench, "server_prefix", side_effect=ValueError("missing")), \
             patch.object(bench.subprocess, "run", side_effect=AssertionError("must not install")):
            with self.assertRaises(ValueError):
                bench.ensure_runtime(None)

    def test_broken_installed_runtime_is_rejected(self):
        with patch.object(bench, "server_prefix", return_value=["/broken/llama-server"]), \
             patch.object(bench.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "version")):
            with self.assertRaisesRegex(ValueError, "実行確認に失敗"):
                bench.ensure_runtime(None)

    def shell_fixture(self, root):
        shutil.copyfile(bench.ROOT / 'benchmark.sh', root / 'benchmark.sh')
        (root / 'scripts').mkdir()
        shutil.copyfile(bench.ROOT / 'scripts/python_entry.sh', root / 'scripts/python_entry.sh')
        (root / '.venv/bin').mkdir(parents=True)
        python = root / '.venv/bin/python'
        python.write_text("#!/bin/sh\ncase \"$*\" in *-c*) exit 0 ;; *) printf 'PROJECT_VENV\\n' ;; esac\n")
        python.chmod(0o755)
        (root / '.local').mkdir()
        (root / '.local/environment.json').write_text('{}')

    def test_shell_uses_project_venv_even_with_broken_active_python(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.shell_fixture(root)
            env = os.environ.copy()
            env['BENCHMARK_PYTHON'] = '/nonexistent/old-python'
            result = subprocess.run(['/bin/sh', str(root/'benchmark.sh'),'--plan'], env=env,
                                    capture_output=True,text=True,timeout=30,cwd='/')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('PROJECT_VENV',result.stdout)

    def test_shell_before_setup_explains_next_command(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.shell_fixture(root)
            (root/'.local/environment.json').unlink()
            result = subprocess.run(['/bin/sh',str(root/'benchmark.sh')],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,1)
            self.assertIn('先に ./setup.sh',result.stderr)


if __name__ == "__main__":
    unittest.main()
