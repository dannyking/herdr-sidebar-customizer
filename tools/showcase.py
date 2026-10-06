#!/usr/bin/env python3
"""Capture a showcase screenshot of real Herdr with fictional spaces and agents.

Everything runs in an isolated Herdr server under /tmp/hsc-shot-*: its own HOME,
XDG directories, config and session. Spaces are temporary Git repositories with
a local bare upstream; agents and their details are reported over the socket
API by this script. The real plugin worker publishes colors, animation and Git
rows; the real Herdr client is drawn into pyte and rendered to SVG and PNG.

Dev dependencies (install into a throwaway venv): pyte cairosvg pillow fonttools.
Usage: python tools/showcase.py [--out DIR] [--all] [--font-dir DIR] [--keep]
Writes assets/showcase.png by default; --all adds showcase.svg and a sidebar crop.
"""
import argparse
import io
import json
import os
from pathlib import Path
import pty
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.request
from html import escape
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
COLUMNS, ROWS = 200, 58
SIDEBAR_WIDTH = 41
SESSION = 'dev'
SOURCE_ID = 'showcase:demo'
FONT_URL = 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/JetBrainsMono.tar.xz'
FONT_FILES = ('JetBrainsMonoNerdFontMono-Regular.ttf', 'JetBrainsMonoNerdFontMono-Bold.ttf')
PRIMARY_FAMILY = 'JetBrainsMono Nerd Font Mono'
FALLBACK_FAMILIES = ('DejaVu Sans Mono', 'DejaVu Sans', 'Noto Sans Symbols2', 'Noto Sans Symbols',
                     'FreeMono', 'FreeSerif')
# Used only when no installed font has the glyph.
SUBSTITUTES = {'⏺': '●', '⎿': '└', '⏵': '▸'}
GIT_ICON = '\U000f062c'  # Branch (Material), the plugin's default Git icon
CONFIG = '''onboarding = false
[terminal]
default_shell = "/bin/sh"
shell_mode = "non_login"
[update]
version_check = false
[theme]
name = "catppuccin"
[ui]
sidebar_width = {width}
sidebar_min_width = {width}
sidebar_max_width = {width}
'''.format(width=SIDEBAR_WIDTH)

# label, color, branch (None: no repo), (ahead, behind), agents
# agent: (tab name, agent, state, details)
SPACES = [
    ('api-gateway', 'blue_medium', 'feat/rate-limiting', (3, 0), [
        ('Add rate limiting', 'claude', 'working', 'Claude Opus 5.5 · high · 34%/1M'),
        ('Review PR #482', 'codex', 'idle', 'Codex GPT-6 Astra · medium · 21%/258k')]),
    ('web-app', 'violet_medium', 'fix/flaky-login-test', (1, 2), [
        ('Fix flaky login test', 'codex', 'done', 'Codex GPT-6 Astra · medium · 43%/258k')]),
    ('data-pipeline', 'teal_medium', 'chore/postgres-17', (0, 4), [
        ('Postgres 17 upgrade', 'claude', 'blocked', 'Claude Opus 5.5 · medium · 61%/1M')]),
    ('docs-site', 'orange_medium', 'docs/onboarding', (5, 0), [
        ('Write onboarding docs', 'claude', 'working', 'Claude Opus 5.5 · medium · 16%/1M')]),
    ('mobile-client', 'rose_medium', 'feat/offline-sync', (2, 1), [
        ('Offline draft sync', 'codex', 'working', 'Codex GPT-6 Astra · high · 37%/258k')]),
    ('design-system', 'magenta_medium', 'feat/tokens-v2', (1, 0), [
        ('Dark mode tokens', 'claude', 'done', 'Claude Sonnet 5 · medium · 28%/200k')]),
    ('infra', 'green_medium', 'main', (0, 0), [
        ('Terraform plan review', 'codex', 'idle', 'Codex GPT-6 Astra · low · 8%/258k')]),
    ('sandbox', 'yellow_medium', None, (0, 0), []),
]
COMMITS = ['Initial project layout', 'Add CI workflow', 'Add README and license',
           'Configure linting', 'Add health check endpoint', 'Tidy dependencies']
FEATURE_COMMITS = ['WIP: first pass', 'Add tests', 'Address review notes', 'Update docs',
                   'Refactor helpers', 'Fix typo in config']
UPSTREAM_COMMITS = ['Merge pull request #311', 'Bump dependencies', 'Fix flaky CI cache',
                    'Update CODEOWNERS', 'Rename env variables']
SETTINGS = {
    'animation': '03', 'speed': 1.0, 'space_colours': True, 'name_bold': True,
    'collect_details': False, 'empty_git_row': 'placeholder', 'git_icon': '41',
    'spaces_gap': 1, 'agents_gap': 1, 'show_tab': True, 'show_machine': True,
}
CURSOR_POSITION_QUERY = b'\x1b[6n'
CURSOR_POSITION_REPLY = b'\x1b[1;1R'


def capture_screen_class(pyte):
    """A pyte screen that ignores terminal queries the PTY reader answers itself."""
    class CaptureScreen(pyte.Screen):
        def report_device_status(self, *args, **kwargs):
            pass

        def report_device_attributes(self, *args, **kwargs):
            pass
    return CaptureScreen


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


def wait_for_server(server, endpoint, log_path, rpc, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            break
        try:
            rpc(endpoint, 'workspace.list')
            return
        except (OSError, RuntimeError):
            time.sleep(.1)
    sys.exit(f'Herdr server did not start. Server output:\n{log_path.read_text()}')


def log(*args):
    print('[showcase]', *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- fonts

def prepare_fonts(font_dir, root):
    """Ensure JetBrains Mono Nerd Font is available; return (fontconfig file, fonts)."""
    font_dir = Path(font_dir) if font_dir else root / 'fonts'
    font_dir.mkdir(parents=True, exist_ok=True)
    if not all((font_dir / name).exists() for name in FONT_FILES):
        try:
            log('downloading JetBrains Mono Nerd Font')
            data = urllib.request.urlopen(FONT_URL, timeout=60).read()
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
                for name in FONT_FILES:
                    archive.extract(name, font_dir, filter='data')
        except Exception as error:  # noqa: BLE001 - fall back to system fonts
            log('font download failed, using system fonts:', error)
    conf = root / 'fonts.conf'
    conf.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                    '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
                    f'<dir>{escape(str(font_dir))}</dir>'
                    f'<cachedir>{escape(str(root / "fontcache"))}</cachedir></fontconfig>')
    os.environ['FONTCONFIG_FILE'] = str(conf)
    from fontTools.ttLib import TTFont
    fonts = []
    for family in (PRIMARY_FAMILY,) + FALLBACK_FAMILIES:
        found = subprocess.run(['fc-match', '-f', '%{family}\n%{file}', family + ':style=Regular'],
                               capture_output=True, text=True).stdout.split('\n')
        if len(found) < 2 or family not in found[0].split(','):
            continue
        try:
            fonts.append((family, set(TTFont(found[1], fontNumber=0).getBestCmap())))
        except Exception:  # noqa: BLE001 - skip unreadable fonts
            continue
    if not fonts:
        sys.exit('No usable monospace font found.')
    log('fonts:', ', '.join(f for f, _ in fonts))
    return fonts


class Glyphs:
    def __init__(self, fonts):
        self.fonts = fonts
        self.primary = fonts[0][0]
        self.missing = set()

    def has(self, char):
        return any(ord(char) in cmap for _, cmap in self.fonts)

    def pick(self, char):
        """(family, char) for a cell's text; substitutes glyphs no font has."""
        for family, cmap in self.fonts:
            if ord(char) in cmap:
                return family, char
        substitute = SUBSTITUTES.get(char)
        if substitute and substitute != char:
            return self.pick(substitute)
        self.missing.add(char)
        return self.primary, ' '


# ---------------------------------------------------------------- git

def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='Demo Developer', GIT_AUTHOR_EMAIL='dev@example.com',
               GIT_COMMITTER_NAME='Demo Developer', GIT_COMMITTER_EMAIL='dev@example.com',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(['git', *args], cwd=cwd, env=env, check=True, capture_output=True)


def commit(cwd, message, n):
    (Path(cwd) / f'change-{n}.txt').write_text(message + '\n')
    git(cwd, 'add', '-A')
    git(cwd, 'commit', '-q', '-m', message)


def make_repo(base, name, branch, ahead, behind):
    """A clone of a local bare upstream with the given ahead/behind counts."""
    upstream, work, other = base / (name + '.git'), base / name, base / (name + '-other')
    git(base, 'init', '-q', '--bare', '--initial-branch=main', str(upstream))
    git(base, 'clone', '-q', str(upstream), str(work))
    git(work, 'checkout', '-q', '-B', 'main')
    for i, message in enumerate(COMMITS[:4]):
        commit(work, message, i)
    git(work, 'push', '-q', '-u', 'origin', 'main')
    if branch != 'main':
        git(work, 'checkout', '-q', '-b', branch)
        commit(work, FEATURE_COMMITS[0], 10)
        git(work, 'push', '-q', '-u', 'origin', branch)
    if behind:
        git(base, 'clone', '-q', '--branch', branch, str(upstream), str(other))
        for i in range(behind):
            commit(other, UPSTREAM_COMMITS[i], 20 + i)
        git(other, 'push', '-q', 'origin', branch)
        shutil.rmtree(other)
        git(work, 'fetch', '-q', 'origin')
    for i in range(ahead):
        commit(work, FEATURE_COMMITS[1 + i % 5], 30 + i)
    return work


# ---------------------------------------------------------------- herdr

def build_spaces(root, endpoint, rpc, space_colors, directory):
    """Create spaces, tabs and agents. Returns the pane that shows the transcript."""
    repos = root / 'repos'
    repos.mkdir()
    focus_pane = None
    for label, color, branch, (ahead, behind), agents in SPACES:
        if branch:
            cwd = make_repo(repos, label, branch, ahead, behind)
        else:
            cwd = repos / label
            cwd.mkdir()
        result = rpc(endpoint, 'workspace.create', {'label': label, 'cwd': str(cwd), 'focus': False})
        wid = result['workspace']['workspace_id']
        space_colors.sync(endpoint, directory, change=(wid, color))
        for index, (tab, agent, state, details) in enumerate(agents):
            if index:
                rpc(endpoint, 'tab.create', {'workspace_id': wid, 'cwd': str(cwd), 'focus': False})
            tabs = rpc(endpoint, 'tab.list', {'workspace_id': wid})['tabs']
            tab_id = tabs[index]['tab_id']
            rpc(endpoint, 'tab.rename', {'tab_id': tab_id, 'label': tab})
            panes = [p for p in rpc(endpoint, 'pane.list', {'workspace_id': wid})['panes']
                     if p['tab_id'] == tab_id]
            pane = panes[0]['pane_id']
            report = {'pane_id': pane, 'source': SOURCE_ID, 'agent': agent}
            if state == 'done':
                # Herdr shows "done" for an unseen agent that worked and went idle.
                rpc(endpoint, 'pane.report_agent', report | {'state': 'working'})
                time.sleep(.05)
                state = 'idle'
            rpc(endpoint, 'pane.report_agent', report | {'state': state})
            rpc(endpoint, 'pane.report_metadata',
                {'pane_id': pane, 'source': SOURCE_ID, 'tokens': {'agent_info': details}})
            if focus_pane is None:
                focus_pane = (wid, tab_id, pane)
    return focus_pane


def first_workspace_cleanup(endpoint, rpc, keep):
    """Close any startup workspace Herdr created before ours."""
    for workspace in rpc(endpoint, 'workspace.list')['workspaces']:
        if workspace['workspace_id'] not in keep:
            rpc(endpoint, 'workspace.close', {'workspace_id': workspace['workspace_id']})


# ---------------------------------------------------------------- render

THEME_BG = '#11111b'  # terminal default; Herdr draws its separators and highlights in #1e1e2e
THEME_FG = '#cdd6f4'
ANSI = {'black': '#45475a', 'red': '#f38ba8', 'green': '#a6e3a1', 'brown': '#f9e2af',
        'blue': '#89b4fa', 'magenta': '#f5c2e7', 'cyan': '#94e2d5', 'white': '#bac2de',
        'brightblack': '#585b70', 'brightred': '#f38ba8', 'brightgreen': '#a6e3a1',
        'brightbrown': '#f9e2af', 'brightblue': '#89b4fa', 'brightmagenta': '#f5c2e7',
        'brightcyan': '#94e2d5', 'brightwhite': '#a6adc8'}
FONT_SIZE = 8
CELL_W = FONT_SIZE * 0.6
CELL_H = 10
BOX = {  # light box drawing: (up, down, left, right)
    '─': (0, 0, 1, 1), '│': (1, 1, 0, 0), '┌': (0, 1, 0, 1), '┐': (0, 1, 1, 0),
    '└': (1, 0, 0, 1), '┘': (1, 0, 1, 0), '├': (1, 1, 0, 1), '┤': (1, 1, 1, 0),
    '┬': (0, 1, 1, 1), '┴': (1, 0, 1, 1), '┼': (1, 1, 1, 1),
}
ROUND = {'╭': (0, 1, 0, 1), '╮': (0, 1, 1, 0), '╰': (1, 0, 0, 1), '╯': (1, 0, 1, 0)}


def color(value, default):
    if value == 'default':
        return default
    if value in ANSI:
        return ANSI[value]
    if len(value) == 6:
        return '#' + value
    return default


def visible(data):
    return ''.join(c for c in data if unicodedata.category(c) not in ('Cf', 'Mn', 'Me'))


def cell_svg(cell, x, y, glyphs, out):
    front = color(cell.fg, THEME_FG)
    back = color(cell.bg, THEME_BG)
    if cell.reverse:
        front, back = back, front
    if back != THEME_BG:
        out.append(f'<rect x="{x:.2f}" y="{y}" width="{CELL_W + .05:.2f}" height="{CELL_H + .05}" '
                   f'fill="{back}"/>')
    text = visible(cell.data)
    if not text.strip():
        return
    char = text[0]
    cx, cy = x + CELL_W / 2, y + CELL_H / 2
    if char in BOX or char in ROUND:
        up, down, left, right = BOX.get(char) or ROUND[char]
        w = .7
        if char in ROUND:
            # Rounded corner: quarter arc between the two arms.
            r = CELL_W / 2
            sx = cx + (r if right else -r)
            ey = cy + (r if down else -r)
            path = (f'M{sx:.2f},{cy:.2f} Q{cx:.2f},{cy:.2f} {cx:.2f},{ey:.2f} '
                    f'L{cx:.2f},{(y + CELL_H if down else y):.2f} M{sx:.2f},{cy:.2f} '
                    f'L{(x + CELL_W if right else x):.2f},{cy:.2f}')
        else:
            path = ''
            if up:
                path += f'M{cx:.2f},{y} L{cx:.2f},{cy:.2f} '
            if down:
                path += f'M{cx:.2f},{cy:.2f} L{cx:.2f},{y + CELL_H} '
            if left:
                path += f'M{x:.2f},{cy:.2f} L{cx:.2f},{cy:.2f} '
            if right:
                path += f'M{cx:.2f},{cy:.2f} L{x + CELL_W + .05:.2f},{cy:.2f} '
        out.append(f'<path d="{path}" stroke="{front}" stroke-width="{w}" fill="none"/>')
        return
    family, char = glyphs.pick(char)
    if not char.strip():
        return
    attrs = ' font-weight="bold"' if cell.bold else ''
    if family != glyphs.primary:
        attrs += f' font-family="{family}"'
    if cell.italics:
        attrs += ' font-style="italic"'
    baseline = y + CELL_H * .76
    out.append(f'<text x="{cx:.2f}" y="{baseline:.2f}" fill="{front}"{attrs}>{escape(char)}</text>')
    if cell.underscore:
        out.append(f'<rect x="{x:.2f}" y="{y + CELL_H - 1.2:.2f}" width="{CELL_W:.2f}" height=".5" '
                   f'fill="{front}"/>')


def svg(screen, glyphs, title, columns=None, frame=True):
    columns = columns or screen.columns
    pad = 14 if frame else 8
    bar = 26 if frame else 0
    width = columns * CELL_W + pad * 2
    height = screen.lines * CELL_H + pad * 2 + bar
    ox, oy = pad, pad + bar
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
             f'viewBox="0 0 {width:.2f} {height:.2f}">',
             f'<title>{escape(title)}</title>']
    if frame:
        parts += [
            '<defs><linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#2b2b3c"/><stop offset="1" stop-color="#232334"/>'
            '</linearGradient></defs>',
            f'<rect x=".5" y=".5" width="{width - 1:.2f}" height="{height - 1:.2f}" rx="10" '
            f'fill="{THEME_BG}" stroke="#45475a" stroke-width="1"/>',
            f'<path d="M.5,{bar + .5} V10.5 a10,10 0 0 1 10,-10 H{width - 10.5:.2f} '
            f'a10,10 0 0 1 10,10 V{bar + .5}Z" fill="url(#bar)"/>',
            f'<line x1=".5" y1="{bar + .5}" x2="{width - .5:.2f}" y2="{bar + .5}" stroke="#11111b"/>',
            f'<circle cx="18" cy="{bar / 2 + .5}" r="5.5" fill="#ff5f57"/>',
            f'<circle cx="36" cy="{bar / 2 + .5}" r="5.5" fill="#febc2e"/>',
            f'<circle cx="54" cy="{bar / 2 + .5}" r="5.5" fill="#28c840"/>',
            f'<text x="{width / 2:.2f}" y="{bar / 2 + 4.5}" fill="#9399b2" font-size="11" '
            f'text-anchor="middle" font-family="DejaVu Sans,sans-serif">herdr — {SESSION}</text>']
    else:
        parts.append(f'<rect width="{width:.2f}" height="{height:.2f}" rx="8" fill="{THEME_BG}"/>')
    parts.append(f'<g font-family="{glyphs.primary}" font-size="{FONT_SIZE}" text-anchor="middle">')
    for row in range(screen.lines):
        line = screen.buffer[row]
        for col in range(columns):
            cell_svg(line[col], ox + col * CELL_W, oy + row * CELL_H, glyphs, parts)
    return '\n'.join(parts + ['</g></svg>']) + '\n'


def save_png(svg_text, path, scale=2):
    import cairosvg
    cairosvg.svg2png(bytestring=svg_text.encode(), write_to=str(path), scale=scale)


# ---------------------------------------------------------------- capture

def capture(root, binary, env, expected, capture_module, screen_class, timeout=40):
    master, slave = pty.openpty()
    capture_module.set_size(master, ROWS, COLUMNS)
    client_env = {k: v for k, v in env.items() if k not in ('HERDR_ENV', 'HERDR_SOCKET_PATH')}
    client = subprocess.Popen([binary, '--session', SESSION], env=client_env, cwd=root,
                              stdin=slave, stdout=slave, stderr=slave)
    log('client pid', client.pid)
    os.close(slave)
    terminal = capture_module.Terminal(master, screen_class(COLUMNS, ROWS))

    def answer(data):
        if CURSOR_POSITION_QUERY in data:
            os.write(master, CURSOR_POSITION_REPLY)

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            terminal.drain(.5, on_data=answer)
            text = '\n'.join(terminal.screen.display)
            missing = [e for e in expected if e not in text]
            if not missing:
                break
        else:
            log('timed out waiting for:', missing)
        terminal.drain(1.5, on_data=answer)  # let the animation settle mid-spin
        return terminal.screen
    finally:
        stop(client)
        os.close(master)


def stop(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def crop(screen, columns, pyte):
    cropped = pyte.Screen(columns, screen.lines)
    for y in range(screen.lines):
        for x in range(columns):
            cropped.buffer[y][x] = screen.buffer[y][x]
    return cropped


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_repo = HERE.parent if (HERE.parent / 'herdr-plugin.toml').exists() else None
    parser.add_argument('--repo', type=Path, default=default_repo,
                        help='plugin checkout (default: parent of this script when inside tools/)')
    parser.add_argument('--out', type=Path, help='output directory (default: the repo\'s assets/)')
    parser.add_argument('--all', action='store_true', help='also write showcase.svg and sidebar.png')
    parser.add_argument('--font-dir', help='directory holding JetBrains Mono Nerd Font files')
    parser.add_argument('--keep', action='store_true', help='keep the temporary directory')
    args = parser.parse_args()
    if not args.repo:
        parser.error('--repo is required')
    repo = args.repo.resolve()
    args.out = args.out or repo / 'assets'
    sys.path[:0] = [str(repo / 'src'), str(repo / 'tools')]
    import pyte
    import agent_info
    import capture as capture_module
    import installation
    import runtime
    import space_colors

    args.out.mkdir(parents=True, exist_ok=True)
    binary = os.environ.get('HERDR_SHOWCASE_BIN') or shutil.which('herdr') or str(Path.home() / '.local/bin/herdr')
    temporary = tempfile.mkdtemp(prefix='hsc-shot-', dir='/tmp')
    root = Path(temporary)
    started = []
    log('temp root', root)
    try:
        glyphs = Glyphs(prepare_fonts(args.font_dir, root))
        settings = SETTINGS | {'show_git_icon': glyphs.has(GIT_ICON)}
        if not settings['show_git_icon']:
            log('Nerd Font Git icon unavailable; turning the Git icon off')
        env = isolated_env(root, binary)
        env.update(CODEX_HOME=str(root / 'codex'), PI_CODING_AGENT_DIR=str(root / 'pi'),
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        env.pop('GH_CONFIG_DIR', None)
        config = Path(env['HERDR_CONFIG_PATH'])
        config.parent.mkdir(parents=True)
        config.write_text(CONFIG)
        endpoint = str(root / 'config/herdr/sessions' / SESSION / 'herdr.sock')
        log_path = root / 'server.log'
        with log_path.open('w') as server_log:
            server = subprocess.Popen([binary, '--session', SESSION, 'server'], env=env, cwd=root,
                                      stdout=server_log, stderr=subprocess.STDOUT)
        started.append(server)
        log('server pid', server.pid)
        wait_for_server(server, endpoint, log_path, agent_info.rpc)
        plugin_config = root / 'config/herdr/plugins/config' / runtime.PLUGIN
        env.update(HERDR_ENV='1', HERDR_SOCKET_PATH=endpoint,
                   HERDR_PLUGIN_CONFIG_DIR=str(plugin_config),
                   HERDR_PLUGIN_STATE_DIR=str(root / 'state/herdr/plugins' / runtime.PLUGIN))
        plugin_config.mkdir(parents=True)
        (plugin_config / 'settings.json').write_text(json.dumps(settings, indent=2))
        with patch.dict(os.environ, env, clear=True):
            startup = {w['workspace_id'] for w in agent_info.rpc(endpoint, 'workspace.list')['workspaces']}
            linked = agent_info.rpc(endpoint, 'plugin.link', {'path': str(repo)})
            if linked.get('warnings'):
                log('plugin warnings:', linked['warnings'])
            subprocess.run([sys.executable, str(repo / 'src/manage.py'), 'configure',
                            '--layout', 'managed', '--no-collect-details'],
                           env=env, check=True, capture_output=True, text=True)
            directory = agent_info.endpoint_state(runtime.state_root(), endpoint)
            wid, tab_id, pane = build_spaces(root, endpoint, agent_info.rpc, space_colors, directory)
            ours = {w['workspace_id'] for w in agent_info.rpc(endpoint, 'workspace.list')['workspaces']} - startup
            first_workspace_cleanup(endpoint, agent_info.rpc, ours)
            agent_info.rpc(endpoint, 'workspace.focus', {'workspace_id': wid})
            agent_info.rpc(endpoint, 'tab.focus', {'tab_id': tab_id})
            script = root / 'transcript.py'
            shutil.copy(HERE / 'showcase_transcript.py', script)
            agent_info.rpc(endpoint, 'pane.send_text',
                           {'pane_id': pane, 'text': f'clear; exec python3 {script}\n'})
            expected = [space[0] for space in SPACES] + [a[3] for s in SPACES for a in s[4]]
            expected += ['↑3', '↓4', 'no git repo', 'Add per-client rate limiting']
            screen = capture(root, binary, env, expected, capture_module,
                             capture_screen_class(pyte))
            if os.environ.get('SHOWCASE_DUMP'):
                cells = [[[c.data, c.fg, c.bg, c.bold, c.reverse] for c in
                          (screen.buffer[y][x] for x in range(COLUMNS))] for y in range(ROWS)]
                Path(os.environ['SHOWCASE_DUMP']).write_text(json.dumps(cells))
            title = 'Herdr with Herdr Sidebar Customizer: colored spaces, Git rows and agent details'
            full = svg(screen, glyphs, title)
            save_png(full, args.out / 'showcase.png')
            if args.all:
                (args.out / 'showcase.svg').write_text(full)
                side = svg(crop(screen, SIDEBAR_WIDTH - 1, pyte), glyphs,
                           'Herdr Sidebar Customizer sidebar', frame=False)
                save_png(side, args.out / 'sidebar.png')
            if glyphs.missing:
                log('glyphs with no font:', ''.join(sorted(glyphs.missing)))
            installation.restore(endpoint)
            status = agent_info.read_json(directory / 'status.json') or {}
            worker = status.get('pid')
            if worker and runtime.worker_running(directory):
                log('stopping worker pid', worker)
                os.kill(worker, signal.SIGTERM)
    finally:
        for process in started:
            stop(process)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    log('wrote', args.out / 'showcase.png')


if __name__ == '__main__':
    main()
