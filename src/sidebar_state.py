"""Animated expanded-sidebar state tokens, without changing agent lifecycle state.

Runs beside the slow model/context collector, so animation never rereads a
transcript or reassigns colors. Only changed frames and periodic refreshes are
published. Name tokens persist, so the sidebar stays readable if this worker
stops; Restore clears them. Herdr's native renderer owns the compact rail.
"""
import time

from agent_info import PLUGIN, RECOVERABLE, atomic_json, rpc, read_json, endpoint_state, state_root
from filecache import file_stamp

SOURCE = PLUGIN + ".animation"
# Herdr colors the whole icon/name token, and an invisible marker after the
# glyph selects the shade, so the name stays steady while the glyph animates.
SHADE_MARKERS = {'dark': '', 'medium': '\u2063', 'light': '\u2063\u2063'}
RENEW_SECONDS = 5


def shade_marker(shade):
    # Bound both ends so shorter shade markers cannot match longer ones.
    return '\u2062' + SHADE_MARKERS[shade] + '\u2062'


def label_tokens(status, now, name, color, settings=None):
    from space_colors import HUES
    from sidebar_settings import DEFAULTS
    settings = settings or DEFAULTS
    values = {'space_'+hue: None for hue,*_ in HUES}
    if color:
        hue,shade = color.rsplit('_', 1)
        glyph = symbol(status,now,settings) if settings['state_icons'] else ''
        label = ' '.join(p for p in (glyph, name[:65] if settings['space_names'] else '') if p)
        values['space_'+hue] = label[:1] + shade_marker(shade) + label[1:] if label else None
    return values


def symbol(status, now, settings=None):
    from sidebar_settings import ANIMATIONS, DEFAULTS, frame_interval
    settings = settings or DEFAULTS
    if status == "working" and settings['animate']:
        frames = ANIMATIONS[settings['animation']][1]
        return frames[int(now / frame_interval(settings)) % len(frames)]
    return settings.get(status+'_symbol', settings['unknown_symbol'])


class Animator:
    def __init__(self, endpoint, directory=None):
        from sidebar_settings import SettingsReader
        self.endpoint = endpoint
        self.settings = SettingsReader()
        self.previous = {}
        self.directory = directory or endpoint_state(state_root(), endpoint)
        self.palette_stamp = None
        self.colors = {}

    def palette(self):
        path = self.directory / 'space-colors.json'
        try:
            stamp = file_stamp(path)
        except FileNotFoundError:
            return {}
        if stamp != self.palette_stamp:
            from space_colors import KEYS
            colors = read_json(path)
            if not isinstance(colors, dict) or any(v not in KEYS for v in colors.values()):
                raise ValueError('Invalid saved space colors')
            self.colors, self.palette_stamp = colors, stamp
        return self.colors

    def tick(self, now=None):
        settings = self.settings.load()
        now = time.monotonic() if now is None else now
        agents = rpc(self.endpoint, "agent.list")["agents"]
        workspaces = rpc(self.endpoint, "workspace.list")["workspaces"]
        names = {w['workspace_id']: w.get('label', '') for w in workspaces}
        colors = self.palette()
        current = set()
        working = 0
        for kind, items, id_key in [("pane", agents, "pane_id"),
                                   ("workspace", workspaces, "workspace_id")]:
            for item in items:
                key = (kind, item[id_key])
                current.add(key)
                status = item.get("agent_status", "unknown")
                working += status == "working" and settings['animate'] and settings['state_icons']
                name = names.get(item.get('workspace_id'), '')
                # One styled value avoids Herdr's separator between icon/name.
                # Keep below the API's 80-character cap, including invisible markers.
                values = label_tokens(status, now, name, colors.get(item.get('workspace_id')), settings)
                previous = self.previous.get(key)
                if previous and previous[0] == values and now-previous[1] < RENEW_SECONDS:
                    continue
                rpc(self.endpoint, kind + ".report_metadata", {
                    id_key: item[id_key], "source": SOURCE,
                    "tokens": values})
                self.previous[key] = (values, now)
        # Herdr discards metadata with closed panes/workspaces.
        self.previous = {k:v for k,v in self.previous.items() if k in current}
        result = {"agents": len(agents), "spaces": len(workspaces), "working": working}
        if self.settings.error:
            result['settings_error'] = self.settings.error
        return result


def run(endpoint, directory, stop):
    animator = Animator(endpoint, directory)
    next_health = 0
    while not stop.is_set():
        now = time.monotonic()
        try:
            state = animator.tick(now)
            from sidebar_settings import frame_interval
            delay = max(.02, min(1, frame_interval(animator.settings.value))) if state["working"] else 1
        except RECOVERABLE as error:
            state = {"error": type(error).__name__}
            delay = 1
        if now >= next_health:
            try:
                atomic_json(directory / "animation-status.json",
                            {"checked_at": int(time.time()), **state})
            except OSError:
                pass  # A failed health write must not kill the animation worker.
            next_health = now + RENEW_SECONDS
        stop.wait(delay)
