#!/usr/bin/env python3
"""Capture native Herdr with invented projects in an isolated server. Dev: pyte."""
import os
from pathlib import Path
import pty
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE / 'src'), str(SOURCE / 'tools')]

import pyte  # noqa: E402

import agent_info  # noqa: E402
import capture  # noqa: E402
import installation  # noqa: E402
import manage  # noqa: E402
import runtime  # noqa: E402
import sidebar_settings as prefs  # noqa: E402
import space_colors  # noqa: E402

SESSION = 'demo'
COLUMNS, ROWS = 120, 38
SIDEBAR_WIDTH, SIDEBAR_ROWS = 48, 35
SIDEBAR_TITLE = 'Native Herdr sidebar with fictional spaces and sample agent details'
CONFIG = '''onboarding = false
[terminal]
default_shell = "/bin/sh"
shell_mode = "non_login"
[update]
version_check = false
[ui]
sidebar_width = {width}
sidebar_min_width = {width}
sidebar_max_width = {width}
'''.format(width=SIDEBAR_WIDTH)
# (label, agent, state, space color, agent details)
SPACES = [
    ('Website', 'claude', 'working', 'blue_medium', 'Claude Sonnet · high · 25%/200k'),
    ('API', 'codex', 'done', 'teal_medium', 'Codex GPT · medium · 42%/258k'),
    ('Docs', 'claude', 'idle', 'violet_medium', 'Claude Haiku · 8%/200k'),
]
CURSOR_POSITION_QUERY = b'\x1b[6n'
CURSOR_POSITION_REPLY = b'\x1b[1;1R'


class CaptureScreen(pyte.Screen):
    """Ignores terminal queries; the PTY reader answers the cursor query itself."""

    def report_device_status(self, *args, **kwargs):
        pass

    def report_device_attributes(self, *args, **kwargs):
        pass


def isolated_env(root, binary):
    """An environment whose Herdr config and state live only under root."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('HERDR_')}
    # A private HOME and XDG directories keep the server, client and worker away
    # from the developer's real files, including agent sessions under HOME.
    for name in ('home', 'data', 'cache', 'runtime'):
        (root / name).mkdir(mode=0o700)
    env.update(HOME=str(root / 'home'),
               XDG_CONFIG_HOME=str(root / 'config'),
               XDG_STATE_HOME=str(root / 'state'),
               XDG_DATA_HOME=str(root / 'data'),
               XDG_CACHE_HOME=str(root / 'cache'),
               XDG_RUNTIME_DIR=str(root / 'runtime'),
               HERDR_CONFIG_PATH=str(root / 'config/herdr/config.toml'),
               HERDR_BIN_PATH=binary,
               SHELL='/bin/sh',
               TERM='xterm-256color',
               CLAUDE_CONFIG_DIR=str(root / 'claude'))
    return env


def wait_for_server(server, endpoint, log_path, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            break
        try:
            agent_info.rpc(endpoint, 'workspace.list')
            return
        except (OSError, RuntimeError):
            time.sleep(.1)
    sys.exit(f'Herdr server did not start. Server output:\n{log_path.read_text()}')


def add_spaces(root, endpoint):
    """Create synthetic Git spaces with reported agents and colors."""
    directory = agent_info.endpoint_state(runtime.state_root(), endpoint)
    for label, agent, state, color, details in SPACES:
        cwd = root / label.lower()
        cwd.mkdir()
        subprocess.run(['git', 'init', '--initial-branch=main', str(cwd)],
                       check=True, capture_output=True)
        result = agent_info.rpc(endpoint, 'workspace.create',
                                {'label': label, 'cwd': str(cwd), 'focus': False})
        wid = result['workspace']['workspace_id']
        pane = agent_info.rpc(endpoint, 'pane.list', {'workspace_id': wid})['panes'][0]['pane_id']
        report = {'pane_id': pane, 'source': 'synthetic-demo', 'agent': agent}
        # Herdr shows "done" for an agent that worked and then went idle.
        if state == 'done':
            agent_info.rpc(endpoint, 'pane.report_agent', report | {'state': 'working'})
            state = 'idle'
        agent_info.rpc(endpoint, 'pane.report_agent', report | {'state': state})
        agent_info.rpc(endpoint, 'pane.report_metadata',
                       {'pane_id': pane, 'source': 'synthetic-demo',
                        'tokens': {'agent_info': details}})
        space_colors.sync(endpoint, directory, change=(wid, color))


def configure_plugin(endpoint):
    manage.configure(endpoint, 'managed', False)
    directory = agent_info.endpoint_state(runtime.state_root(), endpoint)
    old = prefs.read_settings()
    new = old | {'animation': '20', 'spaces_gap': 1, 'agents_gap': 1, 'show_tab': False}
    prefs.apply_settings(endpoint, directory, new, old)


def capture_client(root, binary, env):
    """Run a Herdr client in a PTY for a few seconds and return its screen."""
    master, slave = pty.openpty()
    capture.set_size(master, ROWS, COLUMNS)
    client_env = {key: value for key, value in env.items()
                  if key not in ('HERDR_ENV', 'HERDR_SOCKET_PATH')}
    client = subprocess.Popen([binary, '--session', SESSION], env=client_env, cwd=root,
                              stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    terminal = capture.Terminal(master, CaptureScreen(COLUMNS, ROWS))

    def answer_queries(data):
        if CURSOR_POSITION_QUERY in data:
            os.write(master, CURSOR_POSITION_REPLY)

    try:
        terminal.drain(4, on_data=answer_queries)
    finally:
        stop(client)
        os.close(master)
    return terminal.screen


def crop(screen, columns, rows):
    cropped = pyte.Screen(columns, rows)
    for y in range(rows):
        for x in range(columns):
            cropped.buffer[y][x] = screen.buffer[y][x]
    return cropped


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    binary = runtime.herdr_binary()
    # A short base path keeps the server socket under the Unix socket path limit.
    with tempfile.TemporaryDirectory(prefix='hsc-demo-', dir='/tmp') as temporary:
        root = Path(temporary)
        env = isolated_env(root, binary)
        config = Path(env['HERDR_CONFIG_PATH'])
        config.parent.mkdir(parents=True)
        config.write_text(CONFIG)
        endpoint = str(root / 'config/herdr/sessions' / SESSION / 'herdr.sock')
        log_path = root / 'server.log'
        with log_path.open('w') as log:
            server = subprocess.Popen([binary, '--session', SESSION, 'server'], env=env, cwd=root,
                                      stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_for_server(server, endpoint, log_path)
            env.update(HERDR_ENV='1', HERDR_SOCKET_PATH=endpoint,
                       HERDR_PLUGIN_CONFIG_DIR=str(root / 'config/herdr/plugins/config' / runtime.PLUGIN),
                       HERDR_PLUGIN_STATE_DIR=str(root / 'state/herdr/plugins' / runtime.PLUGIN))
            with patch.dict(os.environ, env, clear=True):
                agent_info.rpc(endpoint, 'plugin.link', {'path': str(SOURCE)})
                add_spaces(root, endpoint)
                configure_plugin(endpoint)
                screen = capture_client(root, binary, env)
                print('\n'.join(row[:SIDEBAR_WIDTH] for row in screen.display))
                # Crop to the sidebar's content columns.
                sidebar = crop(screen, SIDEBAR_WIDTH - 1, SIDEBAR_ROWS)
                (SOURCE / 'assets/sidebar.svg').write_text(capture.svg(sidebar, title=SIDEBAR_TITLE))
                installation.restore(endpoint)
        finally:
            stop(server)


if __name__ == '__main__':
    main()
