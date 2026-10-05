#!/usr/bin/env python3
"""Preserve and chain an existing Claude status line, with reversible ownership."""
import argparse
import json
import os
from pathlib import Path
import shlex

import portable
from runtime import preferences_path, state_root

HOOK_MARKER = 'HERDR_SIDEBAR_CUSTOMIZER_HOOK=1'
STATE_PREFIX = 'HERDR_PLUGIN_STATE_DIR='


def settings_path():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude'))) / 'settings.json'


def hook_command(root):
    here = Path(__file__)
    return shlex.join([
        'env', HOOK_MARKER, STATE_PREFIX + str(root),
        'HERDR_PLUGIN_CONFIG_DIR=' + str(preferences_path().parent),
        # The launcher finds Python 3.11+ even where python3 on PATH is older.
        '/bin/sh', str(here.with_name('python.sh').resolve()),
        str(here.with_name('agent_info.py').resolve()), 'claude-statusline',
    ])


def command_of(status_line):
    return status_line.get('command', '') if isinstance(status_line, dict) else ''


def hook_arguments(status_line):
    try:
        return shlex.split(command_of(status_line))
    except ValueError:
        return []


def owned_by(arguments, root):
    roots = [part.removeprefix(STATE_PREFIX) for part in arguments if part.startswith(STATE_PREFIX)]
    return HOOK_MARKER in arguments and len(roots) == 1 and Path(roots[0]).resolve() == root.resolve()


def parse_settings(settings, original):
    try:
        data = json.loads(original or '{}')
    except ValueError as error:
        raise ValueError(f'{settings} is not valid JSON ({error}). Fix it, then try again.') from error
    if not isinstance(data, dict):
        raise ValueError(f'{settings} must contain a JSON object.')
    return data


def prepare(settings, root, remove=False):
    """Return the transaction changes that install or remove the hook."""
    if remove:
        return prepare_remove(settings, root)
    return prepare_install(settings, root)


def prepare_install(settings, root):
    from installation import read_text, encoded  # installation imports this module
    if portable.WINDOWS:
        raise ValueError('The Claude status-line hook is not available on Windows yet.')
    original = read_text(settings)
    data = parse_settings(settings, original)
    current = data.get('statusLine')
    arguments = hook_arguments(current)
    ours = owned_by(arguments, root)
    command = hook_command(root)
    saved = root / 'previous-statusline.json'
    if HOOK_MARKER in arguments and not ours:
        raise RuntimeError('Another Sidebar Customizer installation owns the Claude hook; restore it first')
    if ours and command_of(current) == command:
        return []
    if current is not None and (not isinstance(current, dict) or current.get('type') != 'command'):
        raise RuntimeError('Unrecognized existing status-line type; leaving it untouched')
    if not ours and 'agent_info.py' in command_of(current):
        raise RuntimeError('Another collector path is configured; restore it before installing')
    changes = []
    if ours:
        if read_text(saved) is None:
            raise RuntimeError('Original status-line settings are missing; refusing to replace them')
    else:
        changes.append((saved, read_text(saved), encoded(current)))
    data['statusLine'] = dict(current or {}) | {'type': 'command', 'command': command}
    changes.append((settings, original, encoded(data)))
    return changes


def prepare_remove(settings, root):
    from installation import read_text, encoded  # installation imports this module
    original = read_text(settings)
    # Without the marker the hook cannot be ours, so leave the file unparsed.
    if not original or HOOK_MARKER not in original:
        return []
    data = parse_settings(settings, original)
    if not owned_by(hook_arguments(data.get('statusLine')), root):
        return []
    previous_raw = read_text(root / 'previous-statusline.json')
    if previous_raw is None:
        raise RuntimeError('Previous status-line settings are missing; refusing to replace them')
    previous = json.loads(previous_raw)
    if previous is None:
        data.pop('statusLine', None)
    elif isinstance(previous, dict) and previous.get('type') == 'command':
        data['statusLine'] = previous
    else:
        raise RuntimeError('Previous status-line settings are invalid; refusing to replace them')
    return [(settings, original, encoded(data))]


def install(settings, root, remove=False):
    from installation import transaction
    transaction(prepare(settings, root, remove), endpoint=None, reload=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    install(settings_path(), state_root(), args.remove)
