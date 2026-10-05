#!/usr/bin/env python3
"""First-run setup, reversible removal and diagnostics for Sidebar Customizer."""
import argparse
import json
import platform
import subprocess
import sys
import textwrap
import time

if sys.version_info < (3, 11):
    raise SystemExit('Herdr Sidebar Customizer requires Python 3.11 or newer.')

import installation
import portable
import runtime
import sidebar_settings as prefs
from agent_info import endpoint_state, read_json, rpc, state_root

# Expected failures: bad files, Herdr errors and a failing or hung herdr binary.
FAILURES = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
# Worker status older than this means the worker has stopped updating it.
STALE_AFTER = 15
CONSENT = ('Optional details read local Claude, Codex, OpenCode, pi and omp session data. '
           'Only model, effort and context metadata are retained; no provider calls or telemetry.')


def suggested_settings():
    """Drop conflicting suggestions on a fresh setup; never silently steal keys."""
    settings = prefs.read_settings()
    if installation.active():
        return settings
    text = installation.read_text(runtime.config_path()) or ''
    candidates = {key: settings[key] for key in prefs.SHORTCUTS}
    settings.update({key: '' for key in candidates})
    for key, chord in candidates.items():
        try:
            prefs.check_shortcuts(text, settings | {key: chord})
        except (ValueError, subprocess.SubprocessError):
            continue
        settings[key] = chord
    return settings


def suggested_shortcuts():
    try:
        settings = suggested_settings()
    except FAILURES as error:
        return 'unavailable (' + str(error) + ')'
    return ', '.join(settings[key] for key in prefs.SHORTCUTS if settings[key])


def configure(endpoint, mode, collect, claude_hook=False):
    """Set up the plugin. collect=None keeps the saved detail-collection choice."""
    if claude_hook and portable.WINDOWS:
        raise ValueError('The Claude status-line hook is not available on Windows yet.')
    runtime.check_prerequisites()
    settings = suggested_settings()
    if collect is not None:
        settings['collect_details'] = collect
    if claude_hook and not settings['collect_details']:
        raise ValueError('Enable detail collection before installing the Claude hook.')
    installation.configure(endpoint, settings, mode, claude_hook=claude_hook)
    return settings


def doctor(endpoint):
    result = {'plugin': runtime.PLUGIN, 'python': platform.python_version(),
              'platform': sys.platform, 'issues': []}
    try:
        result['herdr'] = runtime.check_prerequisites()
        result['server'] = rpc(endpoint, 'session.snapshot').get('snapshot', {}).get('version')
        current = installation.record()
        result['setup'] = bool(current.get('enabled'))
        result['layout'] = current.get('mode', 'not configured')
        result['collect_details'] = prefs.read_settings()['collect_details']
        if not result['setup']:
            result['issues'].append('Run the setup action.')
        if current.get('enabled') and current.get('mode') == 'managed':
            from config_edit import owned_values
            actual = installation.read_text(runtime.config_path()) or ''
            if owned_values(actual, runtime.PLUGIN) != owned_values(current.get('applied') or '', runtime.PLUGIN):
                result['issues'].append('Sidebar configuration was changed outside the plugin.')
        rpc(endpoint, 'workspace.list')
        directory = endpoint_state(state_root(), endpoint)
        status = read_json(directory / 'status.json')
        result['worker_healthy'] = bool(runtime.worker_running(directory)) and fresh(status)
        result['animation_healthy'] = fresh(read_json(directory / 'animation-status.json'))
        if result['setup'] and not (result['worker_healthy'] and result['animation_healthy']):
            result['issues'].append('Worker is not healthy. Run the refresh action.')
        result['models'] = status.get('models', 0)
        result['contexts'] = status.get('contexts', 0)
        from detail_hints import issues
        result['issues'] += issues(status)
        if status.get('settings_error'):
            result['issues'].append('Settings JSON is invalid; the worker is using its last valid settings.')
        if status.get('space_rows_error'):
            result['issues'].append('Could not check empty Git rows. Run the refresh action.')
    except FAILURES as error:
        # No config contents, session paths, tokens or raw API payloads in reports.
        result['issues'].append(type(error).__name__ + ': prerequisite or setup check failed.')
    return result


def fresh(status):
    return status.get('checked_at', 0) >= time.time() - STALE_AFTER and not status.get('error')


def show_lines(screen, title, lines, footer):
    import curses
    from settings_ui import put
    screen.erase()
    height, width = screen.getmaxyx()
    put(screen, 0, 1, title, curses.A_BOLD)
    row = 2
    for line in lines:
        for wrapped in textwrap.wrap(line, max(1, width - 4)) or ['']:
            if row < height - 2:
                put(screen, row, 2, wrapped)
                row += 1
    put(screen, height - 1, 1, footer, curses.A_REVERSE)
    screen.refresh()


def setup_screen(screen, endpoint):
    import curses
    screen.keypad(True)
    curses.curs_set(0)
    current = installation.record()
    mode = current.get('mode', 'managed')
    collect = prefs.read_settings()['collect_details']
    hook = False
    selected = 0
    message = ''
    shortcuts = suggested_shortcuts()
    while True:
        choices = [('Layout', mode), ('Collect local agent details', 'on' if collect else 'off'),
                   ('Install Claude status-line hook', 'yes' if hook else 'no')]
        lines = ['Add colors, agent details and animations to the Herdr sidebar', '',
                 'Config: ' + str(runtime.config_path()),
                 'Managed layout replaces Spaces/Agents rows and adds available shortcuts. '
                 'Previous values are saved privately for Restore. Other settings stay intact.',
                 'Manual layout publishes tokens without editing your sidebar or shortcuts.',
                 CONSENT, '']
        lines += [('> ' if n == selected else '  ') + label + ': ' + value
                  for n, (label, value) in enumerate(choices)]
        lines += ['', 'Suggested shortcuts: ' + shortcuts,
                  'The Claude hook preserves your current status-line command.', message]
        show_lines(screen, runtime.NAME + ' setup', lines,
                   'Up/Down select  Left/Right change  A Apply  Q Cancel')
        key = screen.get_wch()
        if key in ('q', 'Q', '\x1b'):
            return
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            selected = (selected + (1 if key == curses.KEY_DOWN else -1)) % 3
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT, ' '):
            if selected == 0:
                mode = 'manual' if mode == 'managed' else 'managed'
            elif selected == 1:
                collect = not collect
                if not collect:
                    hook = False
            else:
                hook = not hook
                if hook:
                    collect = True
        elif key in ('a', 'A'):
            try:
                configure(endpoint, mode, collect, hook)
                message = 'Applied. Press Q to close, or open the settings action to customize.'
            except FAILURES as error:
                message = str(error)
            shortcuts = suggested_shortcuts()


def restore_screen(screen, endpoint):
    screen.keypad(True)
    message = ''
    while True:
        show_lines(screen, 'Restore previous sidebar', [
            'Restore your previous Spaces/Agents rows and shortcuts, and remove the optional Claude hook.',
            'Later edits are preserved. Color choices and plugin preferences are retained.',
            'After restoring, you can uninstall the plugin through Herdr.', '', message],
            'R Restore previous sidebar  Q Cancel / close')
        key = screen.get_wch()
        if key in ('q', 'Q', '\x1b'):
            return
        if key in ('r', 'R'):
            try:
                conflicts = installation.restore(endpoint)
                message = ('Restored. Preserved later edits to: ' + ', '.join(conflicts)
                           if conflicts else 'Restored. You can now uninstall the plugin.')
            except FAILURES as error:
                message = str(error)


def doctor_screen(screen, endpoint):
    screen.keypad(True)
    report = doctor(endpoint)
    while True:
        show_lines(screen, runtime.NAME + ' diagnostics',
                   json.dumps(report, indent=2).splitlines(), 'R Refresh report  Q Close')
        key = screen.get_wch()
        if key in ('q', 'Q', '\x1b'):
            return
        if key in ('r', 'R'):
            report = doctor(endpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('setup', 'restore', 'doctor', 'setup-ui',
                                         'restore-ui', 'doctor-ui', 'configure', 'restore-now'))
    parser.add_argument('--layout', choices=('managed', 'manual'), default='managed')
    # Omitted: keep the saved choice, so re-running configure never turns it off.
    parser.add_argument('--collect-details', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--claude-hook', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        endpoint = runtime.require_context()
        if args.command == 'doctor' and args.json:
            print(json.dumps(doctor(endpoint), indent=2))
        elif args.command in ('setup', 'restore', 'doctor'):
            rpc(endpoint, 'plugin.pane.open', {'plugin_id': runtime.PLUGIN, 'entrypoint': args.command})
        elif args.command == 'configure':
            configure(endpoint, args.layout, args.collect_details, args.claude_hook)
            print('Sidebar Customizer setup applied.')
        elif args.command == 'restore-now':
            print(json.dumps({'preserved_edits': installation.restore(endpoint)}))
        else:
            screen = {'setup-ui': setup_screen, 'restore-ui': restore_screen, 'doctor-ui': doctor_screen}[args.command]
            import curses
            curses.wrapper(screen, endpoint)
    except FAILURES as error:
        raise SystemExit(str(error)) from error


if __name__ == '__main__':
    main()
