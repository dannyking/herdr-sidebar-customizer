"""Name each agent tab after what its agent is working on.

Herdr keeps every agent's live terminal title, which agents set to their task or
session name (`/rename` in Claude Code, for example), but leaves tabs numbered.
The worker renames an agent's tab from that title on each sync, so no separate
process is needed.

A tab is renamed only when its agent's title has changed since the last rename,
so a name typed by hand survives until the agent retitles. A pinned tab is never
renamed. The first sight of a tab counts as a change.
"""
import argparse
import json
import os
import re

from agent_info import atomic_json, endpoint_state, read_json, rpc
import runtime

STATE_NAME = 'tab-names.json'
MAX_LABEL = 128
MIN_TOPIC = 3
HISTORY = 6
ESCAPES = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])')
GLYPHS = re.compile(r'^(?:[>›✓-✘⏰-⏸○-◗⚠✢-✿⠀-⣿][︎️]?\s*)+')
STATUS_SEGMENTS = {'action required', 'waiting', 'idle', 'working', 'running', 'done', 'blocked'}
# Agents without a session name title themselves after the product, which is no topic.
PRODUCT_TITLES = {'claude', 'claude code', 'codex', 'opencode', 'cursor', 'gemini', 'gemini cli',
                  'amp', 'devin', 'droid', 'grok', 'kimi', 'aider', 'hermes', 'pi', 'omp'}


def topic(title):
    """The task named by a terminal title, or None for product names and fragments."""
    text = ESCAPES.sub('', title if isinstance(title, str) else '')
    text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
    text = re.sub(r'^<command-name>.*?</command-name>\s*', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = GLYPHS.sub('', text)
    text = re.sub(r'^\s*\[[^\]]*\]\s*', '', text)
    text = ' '.join(text.split())
    if '|' in text:
        # Codex reports status lines such as "[ ! ] Action Required | Task | repo".
        parts = [part.strip() for part in text.split('|') if part.strip()]
        text = next((p for p in parts if p.lower() not in STATUS_SEGMENTS), parts[0] if parts else '')
    if text.lower() in PRODUCT_TITLES or len(text) < MIN_TOPIC:
        return None
    return text


def label_for(name, tab, template):
    def field(match):
        values = {'topic': name, 'n': str(tab.get('number', ''))}
        return values.get(match[1], match[0])
    label = re.sub(r'\{(\w+)\}', field, template)
    return label if len(label) <= MAX_LABEL else label[:MAX_LABEL - 1].rstrip() + '…'


class State:
    """Per-tab title history and pins, in the worker's per-session state directory."""

    def __init__(self, directory):
        self.path = directory / STATE_NAME

    def load(self):
        data = read_json(self.path)
        data = data if isinstance(data, dict) else {}
        tabs = data.get('tabs') if isinstance(data.get('tabs'), dict) else {}
        pinned = data.get('pinned') if isinstance(data.get('pinned'), list) else []
        return ({tab: entry for tab, entry in tabs.items() if isinstance(entry, dict)},
                [tab for tab in pinned if isinstance(tab, str)])

    def save(self, tabs, pinned):
        atomic_json(self.path, {'tabs': tabs, 'pinned': pinned})


class TabNamer:
    def __init__(self, endpoint, directory):
        self.endpoint, self.state = endpoint, State(directory)

    def sync(self, agents, template):
        """Rename tabs whose agent's title changed; returns the number renamed."""
        tabs = {tab['tab_id']: tab for tab in rpc(self.endpoint, 'tab.list')['tabs']}
        history, pinned = self.state.load()
        renamed, seen, kept = 0, set(), {}
        for agent in agents:
            tab_id = agent.get('tab_id')
            tab = tabs.get(tab_id)
            # The first agent with a usable title names a tab with several agents.
            name = topic(agent.get('terminal_title_stripped') or agent.get('terminal_title'))
            if tab is None or tab_id in seen or not name:
                continue
            seen.add(tab_id)
            entry = history.get(tab_id, {})
            if tab_id in pinned or entry.get('lastTitle') == name:
                kept[tab_id] = entry | {'lastTitle': name}
                continue
            label = label_for(name, tab, template)
            if tab.get('label') != label:
                rpc(self.endpoint, 'tab.rename', {'tab_id': tab_id, 'label': label})
                renamed += 1
            seen_labels = [label] + [x for x in entry.get('seen', []) if x != label]
            kept[tab_id] = {'lastTitle': name, 'seen': seen_labels[:HISTORY]}
        # Keep history for open tabs whose agent is quiet or gone for now.
        kept |= {tab: entry for tab, entry in history.items() if tab in tabs and tab not in kept}
        # Pins come from disk again so a toggle made during this sync survives.
        _, pinned_now = self.state.load()
        self.state.save(kept, [tab for tab in pinned_now if tab in tabs])
        return renamed


def toggle_pin(directory, tab_id):
    """Pin or unpin a tab; returns True when it is now pinned."""
    state = State(directory)
    history, pinned = state.load()
    now_pinned = tab_id not in pinned
    pinned = pinned + [tab_id] if now_pinned else [tab for tab in pinned if tab != tab_id]
    state.save(history, pinned)
    return now_pinned


def describe_pins(endpoint, directory):
    _, pinned = State(directory).load()
    if not pinned:
        return 'No pinned tabs. Agent tabs follow their agent\'s topic.'
    labels = {tab['tab_id']: tab.get('label', '?') for tab in rpc(endpoint, 'tab.list')['tabs']}
    return 'Pinned: ' + ', '.join(json.dumps(labels.get(tab, '?')) for tab in pinned)


def target_tab(endpoint, explicit):
    """--tab, else the tab Herdr invoked the action from, else the focused tab."""
    if explicit:
        return explicit
    if os.environ.get('HERDR_TAB_ID'):
        return os.environ['HERDR_TAB_ID']
    tabs = rpc(endpoint, 'tab.list')['tabs']
    return next((tab['tab_id'] for tab in tabs if tab.get('focused')), None)


def main():
    parser = argparse.ArgumentParser(description='Pin agent tab names.')
    parser.add_argument('command', choices=('pin', 'pinned'))
    parser.add_argument('--tab', help='tab id from `herdr tab list`; defaults to the current tab')
    args = parser.parse_args()
    endpoint = runtime.require_context()
    directory = endpoint_state(runtime.state_root(), endpoint)
    if args.command == 'pinned':
        message = describe_pins(endpoint, directory)
    else:
        tab_id = target_tab(endpoint, args.tab)
        if not tab_id:
            raise SystemExit('No tab to pin.')
        pinned = toggle_pin(directory, tab_id)
        label = rpc(endpoint, 'tab.get', {'tab_id': tab_id}).get('tab', {}).get('label', tab_id)
        message = (f'Tab "{label}" pinned: its name stays.' if pinned
                   else f'Tab "{label}" unpinned: it follows its agent again.')
    print(message)
    rpc(endpoint, 'notification.show', {'title': runtime.NAME, 'body': message})


if __name__ == '__main__':
    main()
