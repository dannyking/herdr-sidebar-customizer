"""Explain missing agent details: in the sidebar, diagnostics and settings.

Two independent gaps leave details blank. Herdr reports no session ID when its
own integration for that agent is not installed, so no transcript can be read.
Claude context also needs this plugin's status-line hook, because transcripts
do not record the context window. The worker counts both on each sync; the
diagnostics, settings help and optional sidebar hints read the same counts.
"""
import json
from pathlib import Path
import re
import subprocess
import time

from filecache import StampCache
from runtime import HERDR_TEXT_LIMIT
from setup_claude import hook_arguments, owned_by
from sidebar_settings import AGENT_NAMES

# Short enough to leave room for the agent label in a narrow sidebar.
SESSION_HINT = 'needs herdr integration'
RESTART_HINT = 'restart for details'
CONTEXT_HINT = 'ctx needs hook'
INTEGRATION_SECONDS = 60


class HookCheck:
    """Whether this installation's Claude status-line hook is configured, cached by file stamp."""

    def __init__(self, settings_path, root):
        self.settings = StampCache(settings_path, lambda text: hook_installed(text, root), False)

    def installed(self):
        return self.settings.load()


class IntegrationCheck:
    """Which Herdr agent integrations are installed, from `herdr integration status`, rechecked slowly."""

    def __init__(self, binary):
        self.binary, self.checked, self.value = binary, None, {}

    def installed(self, now=None):
        now = time.monotonic() if now is None else now
        if self.checked is None or now - self.checked >= INTEGRATION_SECONDS:
            self.checked, self.value = now, integration_status(self.binary)
        return self.value


def integration_status(binary):
    """{agent: installed} for each listed integration; empty when the command fails."""
    try:
        result = subprocess.run([binary, 'integration', 'status'], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode:
        return {}
    found = (re.match(r'([a-z0-9_-]+): (.*)', line) for line in result.stdout.splitlines())
    return {m[1]: not m[2].startswith('not installed') for m in found if m}


def session_hint(kind, integrations):
    return RESTART_HINT if integrations.get(kind) else SESSION_HINT


def hook_installed(text, root):
    try:
        settings = json.loads(text)
    except ValueError:
        return False
    if not isinstance(settings, dict):
        return False
    return owned_by(hook_arguments(settings.get('statusLine')), Path(root))


def with_hint(label, hint):
    return ' · '.join(p for p in (label, hint) if p)[:HERDR_TEXT_LIMIT]


def plural(count, kind, verb):
    """'1 Codex agent reports' / '2 Codex agents report'."""
    one = count == 1
    return f"{count} {kind} agent{'' if one else 's'} {verb + 's' if one else verb}"


def issues(status):
    """Plain-language problems with the fix for each; empty when nothing is missing."""
    result = []
    integrations = status.get('integrations') or {}
    for kind, count in sorted((status.get('missing_session') or {}).items()):
        if not count:
            continue
        name = AGENT_NAMES.get(kind, kind.title())
        them = 'it' if count == 1 else 'them'
        if integrations.get(kind):
            # Installed now; these agents started before Herdr could record a session.
            result.append(f"{plural(count, name, 'report')} no session ID, so details are blank. "
                          f'Restart or resume {them} to pick up Herdr\'s {kind} integration.')
        else:
            missing = 'is not installed' if kind in integrations else 'may be missing'
            result.append(f"{plural(count, name, 'report')} no session ID: Herdr's {kind} integration {missing}. "
                          f'Run `herdr integration install {kind}`, then restart or resume {them}.')
    for kind, count in sorted((status.get('unreadable_session') or {}).items()):
        name = AGENT_NAMES.get(kind, kind.title())
        result.append(f"{plural(count, name, 'report')} a session this plugin will not read, "
                      'such as a file outside the agent\'s usual session folder or a malformed ID, '
                      "so the sidebar keeps Herdr's native text. "
                      f'Keep {name} sessions in their default folder, or report it if the problem continues.')
    for kind, error in sorted((status.get('observe_errors') or {}).items()):
        name = AGENT_NAMES.get(kind, kind.title())
        result.append(f'Reading {name} details failed with {error}, so those details are blank. '
                      'Please report it with the doctor output; other agents are unaffected.')
    if status.get('missing_context') and not status.get('claude_hook'):
        shown = plural(status['missing_context'], 'Claude', 'show')
        # An older status.json has no claude_hook_supported key; treat it as supported.
        if status.get('claude_hook_supported', True):
            result.append(f'{shown} no context. Run setup with the Claude status-line hook enabled.')
        else:
            result.append(f'{shown} no context. The status-line hook that reports it is not available on Windows.')
    return result
