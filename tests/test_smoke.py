"""Opt-in integration tests against an isolated real Herdr server.

Run with HERDR_SMOKE=1. Never connects to the calling session or reads agent logs:
HOME and every agent data directory point inside a temporary directory.
"""
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_info
import installation
import manage
import runtime
import space_rows
import sidebar_settings as prefs


@unittest.skipUnless(os.environ.get('HERDR_SMOKE') == '1', 'set HERDR_SMOKE=1 for real Herdr smoke test')
class HerdrSmokeTest(unittest.TestCase):
    def test_install_apply_restore_and_worker_recovery(self):
        binary = runtime.herdr_binary()
        source = Path(__file__).resolve().parents[1]
        github = os.environ.get('HERDR_SMOKE_GITHUB')
        with tempfile.TemporaryDirectory(prefix='hsc-', dir='/tmp') as temporary:
            root = Path(temporary)
            env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
            env.pop('GH_CONFIG_DIR', None)
            env.update(HOME=str(root / 'home'), XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                       HERDR_CONFIG_PATH=str(root / 'config/herdr/config.toml'),
                       HERDR_BIN_PATH=binary, CLAUDE_CONFIG_DIR=str(root / 'claude'),
                       CODEX_HOME=str(root / 'codex'), PI_CODING_AGENT_DIR=str(root / 'pi'),
                       SHELL='/bin/sh', TERM='xterm-256color')
            # The repository is public, so installing from GitHub needs no credentials.
            # Pass gh's config only when the caller chose one explicitly.
            if os.environ.get('GH_CONFIG_DIR'):
                env['GH_CONFIG_DIR'] = os.environ['GH_CONFIG_DIR']
            Path(env['HOME']).mkdir()
            target = Path(env['HERDR_CONFIG_PATH'])
            target.parent.mkdir(parents=True)
            original = ('onboarding = false\n[terminal]\ndefault_shell = "/bin/sh"\n'
                        'shell_mode = "non_login"\n[update]\nversion_check = false\n')
            target.write_text(original)
            endpoint = str(root / 'config/herdr/sessions/smoke/herdr.sock')
            with (root / 'server.log').open('w') as logfile:
                server = subprocess.Popen([binary, '--session', 'smoke', 'server'], env=env,
                                          cwd=root, stdout=logfile, stderr=logfile)
                try:
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            agent_info.rpc(endpoint, 'workspace.list')
                            break
                        except (OSError, RuntimeError):
                            if server.poll() is not None or time.monotonic() > deadline:
                                self.fail('Isolated server did not start: ' + (root / 'server.log').read_text()[-2000:])
                            time.sleep(.1)
                    env.update(HERDR_ENV='1', HERDR_SOCKET_PATH=endpoint,
                               HERDR_PLUGIN_CONFIG_DIR=str(root / 'config/herdr/plugins/config' / runtime.PLUGIN),
                               HERDR_PLUGIN_STATE_DIR=str(root / 'state/herdr/plugins' / runtime.PLUGIN))
                    with patch.dict(os.environ, env, clear=True):
                        def command(*args):
                            result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=60)
                            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                        if github:
                            command(binary, 'plugin', 'install', github, '--yes')
                            plugin = next(p for p in agent_info.rpc(endpoint, 'plugin.list')['plugins'] if p['plugin_id'] == runtime.PLUGIN)
                            source = Path(plugin['plugin_root'])
                            self.assertFalse(plugin.get('warnings'), plugin)
                        else:
                            linked = agent_info.rpc(endpoint, 'plugin.link', {'path': str(source)})
                            self.assertFalse(linked.get('warnings'), linked)
                        def configure():
                            command(sys.executable, str(source / 'src/manage.py'), 'configure')

                        agent_info.rpc(endpoint, 'workspace.create', {'label': 'Website', 'cwd': str(root), 'focus': False})
                        # Empty/no-agent session must work with collection disabled.
                        configure()
                        self.wait_healthy(endpoint)
                        spaces = agent_info.rpc(endpoint, 'workspace.list')['workspaces']
                        self.assertTrue(all(w.get('tokens', {}).get(space_rows.TOKEN) == space_rows.BLANK for w in spaces))
                        self.assertFalse(prefs.read_settings()['collect_details'])
                        self.assertEqual(prefs.read_settings()['shortcut_spacing'], '')
                        directory = agent_info.endpoint_state(runtime.state_root(), endpoint)
                        # Terminate only the worker this temporary setup launched.
                        worker_pid = agent_info.read_json(directory / 'status.json')['pid']
                        os.kill(worker_pid, signal.SIGTERM)
                        deadline = time.monotonic() + 5
                        while runtime.worker_running(directory) and time.monotonic() < deadline:
                            time.sleep(.05)
                        self.assertFalse(manage.doctor(endpoint)['worker_healthy'])
                        command(sys.executable, str(source / 'src/agent_info.py'), 'sync')
                        self.wait_healthy(endpoint)
                        self.assertNotEqual(agent_info.read_json(directory / 'status.json')['pid'], worker_pid)
                        previous = prefs.read_settings()
                        prefs.apply_settings(endpoint, directory, previous | {'animation': '20', 'speed': 2, 'git_icon': '01'}, previous)
                        self.assertIn('$space_blue', target.read_text())
                        previous = prefs.read_settings()
                        prefs.apply_settings(endpoint, directory, previous | {'empty_git_row': 'hide'}, previous)
                        space_rows.SpaceRows(endpoint).sync(prefs.read_settings())
                        self.assertNotIn('$space_git_padding', target.read_text())
                        self.assertTrue(all(space_rows.TOKEN not in w.get('tokens', {}) for w in
                                            agent_info.rpc(endpoint, 'workspace.list')['workspaces']))

                        self.assertEqual(installation.restore(endpoint), [])
                        self.assertEqual(target.read_text(), original)
                        self.assertFalse(installation.active())
                        # Reinstall retains choices and safely starts a new worker.
                        if github: command(binary, 'plugin', 'install', github, '--yes')
                        configure()
                        self.wait_healthy(endpoint)
                        self.assertEqual(prefs.read_settings()['animation'], '20')
                        self.assertEqual(prefs.read_settings()['git_icon'], '01')
                        installation.restore(endpoint)
                        if github: command(binary, 'plugin', 'uninstall', runtime.PLUGIN)
                        else: agent_info.rpc(endpoint, 'plugin.unlink', {'plugin_id': runtime.PLUGIN})
                        self.assertEqual(target.read_text(), original)
                        print('Real Herdr smoke test:', runtime.check_prerequisites(), 'OK')
                finally:
                    # Only this process, launched above with private XDG paths.
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)

    def wait_healthy(self, endpoint):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            report = manage.doctor(endpoint)
            if not report['issues']:
                return
            time.sleep(.2)
        self.fail('Isolated worker failed: ' + str(report))
