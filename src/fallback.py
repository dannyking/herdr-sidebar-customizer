"""Native fallback text for agents whose details row this plugin does not fill.

The managed Agents layout pairs our tokens with native `agent` and `state_text`
tokens that hide when they contain MARKER. The worker appends MARKER to every
agent whose details row belongs to the plugin, so that row never shows both:
all agents while details are hidden or collection is off, otherwise those it
describes. The rest keep the native text, e.g. `claude · idle`: an agent the
worker cannot describe, or one on a saved SSH machine that runs no worker.

These are presentation-only fields: waits, notifications and rollups still use
the semantic state. Like the space name tokens, they persist without a TTL so a
stopped worker cannot expose duplicate text; Restore clears them.
"""
from agent_info import PLUGIN, rpc
from runtime import HERDR_TEXT_LIMIT

SOURCE = PLUGIN + '.fallback'
# Invisible, survives Herdr's text normalization and differs from the U+2062
# and U+2063 color shade markers.
MARKER = '⁤'
STATES = ('idle', 'working', 'blocked', 'done', 'unknown')
# Leaves room for the marker inside Herdr's text cap.
TEXT_LENGTH = HERDR_TEXT_LIMIT - len(MARKER)


def plain(value):
    return value.replace(MARKER, '') if isinstance(value, str) else ''


def marked(agent):
    """Whether this plugin's marker is in the agent's name or any state label."""
    labels = agent.get('state_labels')
    texts = [agent.get('display_agent')] + (list(labels.values()) if isinstance(labels, dict) else [])
    return any(isinstance(text, str) and MARKER in text for text in texts)


def presentation(agent):
    """Marked display name and state labels, preserving another reporter's text."""
    name = plain(agent.get('display_agent')) or plain(agent.get('agent')) or 'agent'
    labels = agent.get('state_labels')
    labels = labels if isinstance(labels, dict) else {}
    return {'display_agent': name[:TEXT_LENGTH] + MARKER,
            'state_labels': {s: (plain(labels.get(s)) or s)[:TEXT_LENGTH] + MARKER for s in STATES}}


def sync(endpoint, agents):
    """Mark the agents whose presentation differs from the marked one."""
    for agent in agents:
        wanted = presentation(agent)
        if (agent.get('display_agent') == wanted['display_agent']
                and (agent.get('state_labels') or {}) == wanted['state_labels']):
            continue
        rpc(endpoint, 'pane.report_metadata', {'pane_id': agent['pane_id'], 'source': SOURCE, **wanted})


def clear(endpoint, pane_id):
    rpc(endpoint, 'pane.report_metadata', {'pane_id': pane_id, 'source': SOURCE,
                                           'clear_display_agent': True, 'clear_state_labels': True})
