"""The plugin launcher picks Python 3.11+ even when python3 on PATH is older."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'src/python.sh'


def fake_python(directory, name, version):
    """A stand-in interpreter: answers the version probe, otherwise echoes its name."""
    path = Path(directory) / name
    path.write_text('#!/bin/sh\n'
                    'if [ "$1" = -c ]; then [ ' + str(int(version >= (3, 11))) + ' = 1 ]; exit $?; fi\n'
                    'echo ' + name + ' "$@"\n')
    path.chmod(0o755)
    return path


class LauncherTests(unittest.TestCase):
    def run_launcher(self, directory, launcher=LAUNCHER, **env):
        # Only the fixture is on PATH, so a real python3.14 in /usr/bin cannot be
        # picked; HOME points into it too, for the ~/.local/bin fallback.
        environment = {'PATH': str(directory), 'HOME': str(directory)} | env
        return subprocess.run(['/bin/sh', str(launcher), 'entry.py', 'start'], env=environment,
                              capture_output=True, text=True, timeout=10)

    def test_skips_an_old_python3_for_a_versioned_one(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_python(directory, 'python3', (3, 9))
            fake_python(directory, 'python3.13', (3, 13))
            fake_python(directory, 'python3.12', (3, 12))
            result = self.run_launcher(directory)
            self.assertEqual(result.stdout.strip(), 'python3.13 entry.py start')

    def test_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen = fake_python(directory, 'custom-python', (3, 12))
            fake_python(directory, 'python3', (3, 14))
            result = self.run_launcher(directory, HERDR_SIDEBAR_PYTHON=str(chosen))
            self.assertEqual(result.stdout.strip(), 'custom-python entry.py start')

    def test_reports_when_no_suitable_python_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_python(directory, 'python3', (3, 9))
            # A copy whose fixed fallbacks point into the fixture, where none exist.
            fixed = '/opt/homebrew/bin/python3 /usr/local/bin/python3'
            script = LAUNCHER.read_text()
            self.assertIn(fixed, script)
            launcher = Path(directory) / 'python.sh'
            launcher.write_text(script.replace(fixed, f'{directory}/homebrew/python3 {directory}/local/python3'))
            result = self.run_launcher(directory, launcher)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Python 3.11 or newer', result.stderr)

    def test_manifest_and_hook_use_the_launcher(self):
        import tomllib
        manifest = tomllib.loads((LAUNCHER.parents[1] / 'herdr-plugin.toml').read_text())
        commands = [item['command'] for group in ('startup', 'actions', 'panes', 'events')
                    for item in manifest.get(group, []) if item.get('platforms') != ['windows']]
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command[:2], ['/bin/sh', './src/python.sh'], command)
        self.assertTrue(os.access(LAUNCHER, os.X_OK))

    def test_windows_runs_only_the_worker_through_its_own_launcher(self):
        import tomllib
        manifest = tomllib.loads((LAUNCHER.parents[1] / 'herdr-plugin.toml').read_text())
        self.assertIn('windows', manifest['platforms'])
        entries = [(group, item) for group in ('startup', 'actions', 'panes', 'events')
                   for item in manifest.get(group, [])]
        windows = [(g, i) for g, i in entries if i.get('platforms') == ['windows']]
        unix = [(g, i) for g, i in entries if i.get('platforms') == ['linux', 'macos']]
        self.assertEqual(len(windows) + len(unix), len(entries))
        # Actions and panes have ids, which Herdr requires to be unique across platforms.
        self.assertEqual({g for g, _ in windows}, {'startup', 'events'})
        for _, item in windows:
            self.assertEqual(item['command'], ['cmd.exe', '/d', '/c', 'src\\python.cmd', 'src\\agent_info.py', 'start'])
        hooks = lambda side: sorted(i.get('on', 'startup') for g, i in side if g in ('startup', 'events'))
        self.assertEqual(hooks(windows), hooks(unix))
        batch = (LAUNCHER.parent / 'python.cmd').read_bytes()
        self.assertIn(b'\r\n', batch)
        self.assertNotIn(b'\n', batch.replace(b'\r\n', b''))


if __name__ == '__main__':
    unittest.main()
