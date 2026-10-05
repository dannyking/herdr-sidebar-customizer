#!/usr/bin/env python3
"""Interactive Herdr Sidebar Customizer settings, with draft previews and explicit Apply."""
import argparse
from collections import namedtuple
import curses
from dataclasses import dataclass, field as dataclass_field
import json
import math
import os
import subprocess
import textwrap
import time

from agent_info import PLUGIN, endpoint_state, read_json, rpc, state_root, tokens
import sidebar_settings as settings
import space_colors
from sidebar_state import symbol
from space_rows import PLACEHOLDER_COLOUR


# A field is (key, label, options, help). None options means free text;
# a tuple is a cyclic choice (booleans included); an empty tuple is a swatch picker.
ON_OFF = (False, True)
SPEEDS = (.25, .5, .75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
STATE_SYMBOL_HELP = 'One single-width symbol. Working uses this when animation is off.'
SHORTCUT_HELP = 'Use prefix+shift+s or ctrl+alt+s; empty disables. Conflicts are rejected.'
SECTIONS = [
    ('Display', [
        ('state_icons', 'State indicators', ON_OFF, 'Show working, idle, done and blocked symbols.'),
        ('space_names', 'Space names', ON_OFF, 'Show the workspace name beside its indicator.'),
        ('show_tab', 'Tab names', ON_OFF, 'Show tab names alongside the space in the Agents list.'),
        ('tab_names', 'Name tabs after agents', ON_OFF, "Rename each agent's tab from its title, e.g. after /rename. A name you type stays until the title changes; pin a tab to keep it."),
        ('tab_name_format', 'Tab name format', None, 'Use {topic} for the agent\'s topic and {n} for the tab number, e.g. "{n}· {topic}".'),
        ('show_machine', 'Machine labels', ON_OFF, 'Show the saved SSH machine name on agents from other machines. Local agents are never labeled.'),
        ('machine_position', 'Machine label position', ('name', 'details'), 'After the space name on the tab row, or leading the details row.'),
        ('spaces_gap', 'Spaces spacing', (0, 1), 'Compact or one blank row between entries.'),
        ('agents_gap', 'Agents spacing', (0, 1), 'Independent of the Spaces list spacing.'),
        ('name_bold', 'Bold space names', ON_OFF, 'Applies to the combined name and state indicator.'),
    ]),
    ('Git', [
        ('show_branch', 'Git branch', ON_OFF, 'Show the current branch in the Spaces list.'),
        ('show_git', 'Git status', ON_OFF, 'Show the native Git status and ahead/behind indicators.'),
        ('empty_git_row', 'Empty Git row', ('hide', 'blank', 'placeholder'), 'Hide collapses the row. Blank keeps its height. Placeholder explains why Git details are absent.'),
        ('show_git_icon', 'Show Git icon', ON_OFF, 'Show the selected Nerd Font icon on populated Git rows. Requires a Nerd Font in your terminal.'),
        ('git_icon', 'Git icon style', tuple(settings.GIT_ICONS), 'Left/Right selects a GitHub, repository or Git icon. The sample rows preview your choice; Apply saves it.'),
    ]),
    ('Agent text', [
        ('agent_text', 'Agent details row', ON_OFF, 'Show the configurable agent/model/effort/context row.'),
        ('collect_details', 'Collect agent details', ON_OFF, 'Read model/context metadata from local Claude, Codex, OpenCode, pi and omp session data. Off stops collection, independently of visibility.'),
        ('detail_hints', 'Missing-detail hints', ON_OFF, 'Say in the details row why model or context is missing, e.g. needs herdr integration.'),
        ('text_mode', 'Text layout', ('automatic', 'custom'), 'Choose field controls or a custom template. Each mode keeps its own settings.'),
        ('show_agent', 'Agent name', ON_OFF, 'Show the agent name, using the labels below.'),
        ('show_model', 'Model', ON_OFF, 'Show the model reported by the session.'),
        ('show_effort', 'Reasoning effort', ON_OFF, 'Unknown effort is omitted.'),
        ('show_context', 'Context usage', ON_OFF, 'Use the current session usage, never an assumed capacity.'),
        ('show_capacity', 'Context capacity', ON_OFF, 'Include the reported window, for example 25%/1M.'),
        ('context_mode', 'Context display', ('used', 'remaining'), 'Remaining includes a "left" label to distinguish it from used.'),
        ('claude_label', 'Claude label', None, 'Custom visible name; this does not change agent identity.'),
        ('codex_label', 'Codex label', None, 'Custom visible name for Codex in automatic mode.'),
        ('opencode_label', 'OpenCode label', None, 'Custom visible name for OpenCode in automatic mode.'),
        ('pi_label', 'pi label', None, 'Custom visible name for pi in automatic mode.'),
        ('omp_label', 'omp label', None, 'Custom visible name for omp (oh-my-pi) in automatic mode.'),
        ('text_template', 'Custom text template', None, 'Enter edits the template. Available fields and examples are shown below.'),
    ]),
    ('Animation', [
        ('animate', 'Animate working agents', ON_OFF, 'Off uses the static working symbol from States.'),
        ('animation', 'Working animation', tuple(settings.ANIMATIONS), 'Left/Right selects an animation; click any preview to choose it.'),
        ('speed', 'Animation speed', SPEEDS, 'A multiplier of each animation\'s natural speed; 1x preserves the preview cadence.'),
    ]),
    ('Colors', [
        ('space_colours', 'Use space colors', ON_OFF, 'Off uses the neutral color; saved space assignments are retained.'),
        ('@space', 'Selected space', (), 'Choose the space whose color to edit.'),
        ('@colour', 'Selected space color', (), 'Choose from the swatches below. Enter focuses the palette; Escape returns to fields.'),
        ('default_hue', 'New-space hue', ('random', *(hue for hue, *_ in space_colors.HUES)), 'Applies only to new spaces; existing assignments stay as they are.'),
        ('default_shade', 'New-space shade', ('dark', 'medium', 'light'), 'Applies only to newly assigned spaces.'),
        ('neutral_colour', 'Neutral names', (), 'Choose a swatch below. Used when space colors are off.'),
        ('agent_colour', 'Agent details color', (), 'Choose a swatch below. Neutrals are included for text.'),
        ('tab_colour', 'Tab name color', (), 'Choose a swatch below.'),
        ('machine_colour', 'Machine label color', (), 'Choose a swatch below. By default labels match the agent details color, in bold.'),
        ('branch_colour', 'Branch color', (), 'Choose a swatch below. Git status retains its native semantic colors.'),
    ]),
    ('States', [
        ('idle_symbol', 'Idle symbol', None, STATE_SYMBOL_HELP),
        ('working_symbol', 'Working symbol', None, STATE_SYMBOL_HELP),
        ('done_symbol', 'Done symbol', None, STATE_SYMBOL_HELP),
        ('blocked_symbol', 'Blocked symbol', None, STATE_SYMBOL_HELP),
        ('unknown_symbol', 'Unknown symbol', None, STATE_SYMBOL_HELP),
    ]),
    ('Shortcuts', [
        ('shortcut_settings', 'Open settings', None, SHORTCUT_HELP),
        ('shortcut_colours', 'Space color picker', None, SHORTCUT_HELP),
        ('shortcut_spacing', 'Toggle spacing', None, SHORTCUT_HELP),
    ]),
]

# Everything on Agent text except the row switch and collection itself is
# meaningless while the row is hidden.
AGENT_TEXT_DEPENDENTS = (
    {key for name, fields in SECTIONS if name == 'Agent text' for key, *_ in fields}
    - {'agent_text', 'collect_details'}
)


def inactive_reason(session, key):
    """Explain inactive fields while keeping their saved choices intact."""
    draft = session.draft
    if key == 'tab_name_format' and not draft['tab_names']:
        return 'Inactive: turn on Name tabs after agents.'
    if key in ('animate', 'animation', 'speed') or key.endswith('_symbol'):
        if not draft['state_icons']:
            return 'Inactive: turn on State indicators on Display.'
    if key in ('animation', 'speed') and not draft['animate']:
        return 'Inactive: turn on Animate working agents.'
    if key == 'working_symbol' and draft['animate']:
        return 'Inactive: turn off Animate working agents on Animation.'
    if key == 'detail_hints' and not draft['collect_details']:
        return 'Inactive: turn on Collect agent details.'
    if key in ('machine_colour', 'machine_position') and not draft['show_machine']:
        return 'Inactive: turn on Machine labels on Display.'
    if key == 'git_icon' and not draft['show_git_icon']:
        return 'Inactive: turn on Show Git icon.'
    if key in AGENT_TEXT_DEPENDENTS and not draft['agent_text']:
        return 'Inactive: turn on Agent details row on Agent text.'
    if key in ('show_capacity', 'context_mode') and not draft['show_context']:
        return 'Inactive: turn on Context usage.'
    if key.endswith('_label') and not draft['show_agent']:
        return 'Inactive: turn on Agent name.'
    if key.startswith('shortcut_') and session.layout_mode == 'manual':
        return 'Inactive: manual layout leaves shortcuts to your own Herdr config.'
    return ''


def field_help(session, field):
    """Static help, plus the worker's latest missing-detail findings for collection."""
    if field[0] != 'collect_details' or not session.draft['collect_details']:
        return field[3]
    from detail_hints import issues
    found = issues(read_json(session.directory / 'status.json'))
    return ' '.join(found) if found else field[3]


def default_space_color(draft, random_hue):
    """The color a new space would get; random_hue stands in when the hue is random."""
    hue = random_hue if draft['default_hue'] == 'random' else draft['default_hue']
    return f"{hue}_{draft['default_shade']}"


class Session:
    def __init__(self, endpoint, directory, layout_mode='managed'):
        self.endpoint = endpoint
        self.directory = directory
        self.layout_mode = layout_mode
        self.message = ''
        self.wid = os.environ.get('HERDR_WORKSPACE_ID')
        self.reload()

    def reload(self):
        base = settings.read_settings()
        spaces = rpc(self.endpoint, 'workspace.list')['workspaces']
        path = self.directory / 'space-colors.json'
        base_colours = json.loads(path.read_text()) if path.exists() else {}
        valid = isinstance(base_colours, dict) and all(
            value in space_colors.KEYS for value in base_colours.values())
        if not valid:
            raise ValueError('Saved space colors are invalid')
        self.base = base
        self.spaces = spaces
        self.base_colours = base_colours
        self.draft = dict(base)
        if self.wid not in [space['workspace_id'] for space in spaces]:
            self.wid = spaces[0]['workspace_id'] if spaces else None
        self.colours = dict(base_colours)
        self.preview_colour = None

    @property
    def dirty(self):
        return self.draft != self.base or self.colours != self.base_colours

    def value(self, key):
        if key == '@space':
            return self.wid
        if key == '@colour':
            fallback = self.preview_colour or default_space_color(self.draft, 'blue')
            return self.colours.get(self.wid, fallback)
        return self.draft[key]

    def set(self, key, value):
        reason = inactive_reason(self, key)
        if reason:
            self.message = reason
            return
        if self.value(key) != value:
            self.message = ''
        if key == '@space':
            self.wid = value
        elif key == '@colour':
            if self.wid:
                self.colours[self.wid] = value
            else:
                self.preview_colour = value
        else:
            self.draft[key] = value
            if key == 'text_mode' and value == 'custom' and not self.draft['text_template']:
                self.draft['text_template'] = settings.DEFAULT_TEMPLATE

    def cycle(self, field, step):
        key, _, options, _ = field
        if key == '@space':
            options = tuple(space['workspace_id'] for space in self.spaces)
        if is_color(key):
            options = tuple(value for value, _, _ in color_options(self, key))
        if options:
            current = self.value(key)
            index = options.index(current) if current in options else 0
            self.set(key, options[(index + step) % len(options)])

    def apply(self):
        changes = {wid: color for wid, color in self.colours.items()
                   if self.base_colours.get(wid) != color}
        settings.apply_settings(self.endpoint, self.directory, self.draft, self.base,
                                changes, self.base_colours)
        self.reload()
        if self.layout_mode == 'manual':
            self.message = 'Saved. Update your rows to match layout changes.'
        else:
            self.message = 'Applied. Sidebar updates within three seconds.'

    def defaults(self):
        self.draft = dict(settings.DEFAULTS)
        self.colours = dict(self.base_colours)
        self.preview_colour = None
        self.message = ('Defaults previewed; Apply to save. Saved space colors kept. '
                        'Restore previous sidebar is a separate plugin action.')


def display_value(session, key):
    value = session.value(key)
    if key == '@space':
        return next((space['label'] for space in session.spaces if space['workspace_id'] == value),
                    'Sample space (preview only)')
    if is_color(key):
        return next(label for choice, label, _ in color_options(session, key) if choice == value)
    if isinstance(value, bool):
        return 'On' if value else 'Off'
    if key == 'animation':
        return settings.ANIMATIONS[value][0]
    if key == 'git_icon':
        label, glyph = settings.GIT_ICONS[value]
        return glyph + ' ' + label
    if key == 'empty_git_row':
        return value.title()
    if key == 'text_mode':
        return 'Automatic fields' if value == 'automatic' else 'Custom template'
    if key == 'speed':
        return f'{value:g}x'
    if key.endswith('_gap'):
        return 'Spaced' if value else 'Compact'
    if value == '':
        return '(disabled)' if key.startswith('shortcut_') else '(empty)'
    return str(value)


def put(screen, y, x, text, style=0):
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width - 1:
        try:
            screen.addnstr(y, x, text, max(0, width - x - 1), style)
        except curses.error:
            pass


ESCAPE = '\x1b'
ENTER_KEYS = ('\n', '\r', curses.KEY_ENTER)
CLOSE_KEYS = ('q', 'Q', ESCAPE)
BACKSPACE_KEYS = (curses.KEY_BACKSPACE, '\x7f', '\b')
CTRL_A, CTRL_E, CTRL_U = '\x01', '\x05', '\x15'
MAX_TEXT_LENGTH = 160


def apply_edit_key(value, position, key):
    """Return the text and cursor position after one key in the line editor."""
    if key == CTRL_U:
        return '', 0
    if key in BACKSPACE_KEYS and position:
        return value[:position - 1] + value[position:], position - 1
    if key == curses.KEY_DC:
        return value[:position] + value[position + 1:], position
    if key == curses.KEY_LEFT:
        return value, max(0, position - 1)
    if key == curses.KEY_RIGHT:
        return value, min(len(value), position + 1)
    if key in (curses.KEY_HOME, CTRL_A):
        return value, 0
    if key in (curses.KEY_END, CTRL_E):
        return value, len(value)
    if isinstance(key, str) and key.isprintable() and len(value) < MAX_TEXT_LENGTH:
        return value[:position] + key + value[position:], position + 1
    return value, position


def edit_text(screen, title, value):
    original = value
    position = len(value)
    screen.timeout(-1)
    curses.curs_set(1)
    try:
        while True:
            height, width = screen.getmaxyx()
            if height < 5 or width < 12:
                return original
            y = height - 4
            for row in range(y, height):
                screen.move(row, 0)
                screen.clrtoeol()
            put(screen, y, 1, 'Edit ' + title, curses.A_BOLD)
            start = max(0, position - max(1, width - 5))
            put(screen, y + 1, 1, value[start:])
            put(screen, y + 2, 1, 'Enter: accept   Esc: cancel   Ctrl+U: clear', curses.A_DIM)
            screen.move(y + 1, min(width - 2, 1 + position - start))
            screen.refresh()
            key = screen.get_wch()
            if key in ENTER_KEYS:
                return value
            if key == ESCAPE:
                return original
            value, position = apply_edit_key(value, position, key)
    finally:
        curses.curs_set(0)
        screen.timeout(60)


# Text colors use the 36-swatch space palette plus neutrals, and a saved custom
# color stays selectable without a hex editor.
NEUTRALS = tuple((f'#{n:02x}{n:02x}{n:02x}', name) for n, name in
                 [(0, 'Black'), (48, 'Charcoal'), (88, 'Dark gray'), (128, 'Gray'),
                  (168, 'Soft gray'), (208, 'Light gray'), (228, 'Pale gray'), (255, 'White')])
PALETTE_FOREGROUNDS = {key: fg for key, _, fg in space_colors.PALETTE}


def is_color(key):
    # Keys keep their original '_colour' spelling so existing installs still load.
    return key == '@colour' or key.endswith('_colour')


def color_options(session, key):
    """Return (value, label, foreground) choices for a color field."""
    palette = [(value, value.replace('_', ' ').title(), fg) for value, _, fg in space_colors.PALETTE]
    if key == '@colour':
        return palette
    options = [('theme', 'Follow theme', 'theme')]
    if key == 'machine_colour':
        options.insert(0, ('agent', 'Same as agent details', session.draft['agent_colour']))
    options += [(fg, label, fg) for _, label, fg in palette]
    options += [(fg, name, fg) for fg, name in NEUTRALS]
    known = {value for value, _, _ in options}
    # The saved color comes before the moving draft so arrow navigation stays stable.
    extras = ((session.base[key], 'Original color'),
              (settings.DEFAULTS[key], 'Default color'),
              (session.value(key), 'Current color'))
    for value, label in extras:
        if value not in known:
            options.append((value, label, value))
            known.add(value)
    return options


def active_fields(session, section):
    name, fields = SECTIONS[section]
    if name != 'Agent text':
        return fields
    custom = session.draft['text_mode'] == 'custom'
    always_shown = ('agent_text', 'collect_details', 'detail_hints', 'text_mode')
    return [field for field in fields
            if field[0] in always_shown or (field[0] == 'text_template') == custom]


def template_help(width):
    paragraphs = [
        'AVAILABLE FIELDS',
        '{agent}         Agent name: Claude / Codex / OpenCode / pi / omp',
        '{model}         Model name',
        '{effort}        Reasoning effort',
        '{context}       Context used with capacity: 25%/1M',
        '{used}          Tokens used: 250k',
        '{remaining}     Tokens remaining: 750k',
        '{capacity}      Context window: 1M',
        '{used_pct}      Percentage used: 25%',
        '{remaining_pct} Percentage remaining: 75%',
        '',
        'Example: {agent} {model} · {effort} · {context}',
        'Remaining: {model} · {remaining_pct} left',
        'Automatic-mode switches and labels do not apply here. Separate optional groups with ·; a group with no available fields disappears.',
    ]
    lines = []
    for paragraph in paragraphs:
        wrapped = textwrap.wrap(paragraph, width=max(1, width - 4),
                                break_long_words=False, break_on_hyphens=False)
        lines.extend(wrapped or [''])
    return lines


# Two illustrative entries keep every layout control visible in empty sessions.
# The second runs on a fictional saved machine and has a Git branch.
PreviewSample = namedtuple('PreviewSample', 'name color status agent model tab branch remote')
SEPARATOR = ' · '
SAMPLE_MACHINE = 'Build box'
SAMPLE_GIT_STATUS = '↑1 ↓2'
GIT_STATUS_COLOR = '#afff87'


def preview_samples(session):
    first = next((space['label'] for space in session.spaces if space['workspace_id'] == session.wid),
                 'Website')
    return [
        PreviewSample(first, session.value('@colour'), 'working', 'claude', 'Opus 5',
                      'editor', None, False),
        PreviewSample('Sandbox', default_space_color(session.draft, 'teal'), 'done', 'codex',
                      'GPT-6 Astra', 'tests', 'feature/ui', True),
    ]


def append_segment(row, text, fg, bold=False):
    """Add a (text, fg, bold) segment, separated from any earlier one by a middle dot."""
    row.append(((SEPARATOR if row else '') + text, fg, bold))


def preview_rows(session, kind, now):
    """Sample rows for the 'spaces' or 'agents' list, as lists of (text, fg, bold)."""
    draft = session.draft
    sample_rows = space_sample_rows if kind == 'spaces' else agent_sample_rows
    rows = []
    for index, sample in enumerate(preview_samples(session)):
        if index:
            rows.extend([] for _ in range(draft[kind + '_gap']))
        rows.extend(sample_rows(draft, sample, now))
    return rows


def name_row(draft, sample, now):
    fg = PALETTE_FOREGROUNDS[sample.color] if draft['space_colours'] else draft['neutral_colour']
    glyph = symbol(sample.status, now, draft) if draft['state_icons'] else ''
    name = sample.name if draft['space_names'] else ''
    label = ' '.join(part for part in (glyph, name) if part)
    return [(label, fg, draft['name_bold'])] if label else []


def space_sample_rows(draft, sample, now):
    rows = [name_row(draft, sample, now)]
    git_row = git_preview_row(draft, sample.branch)
    if git_row or draft['empty_git_row'] != 'hide':
        rows.append(git_row)
    return rows


def git_preview_row(draft, branch):
    icon = settings.GIT_ICONS[draft['git_icon']][1]
    show_branch = bool(branch) and draft['show_branch']
    show_git = bool(branch) and draft['show_git']
    row = []
    if draft['show_git_icon'] and (show_branch or show_git):
        label = icon + (' ' + branch if show_branch else '')
        row.append((label, draft['branch_colour'], False))
        if show_git:
            row.append((' ' + SAMPLE_GIT_STATUS, GIT_STATUS_COLOR, False))
    else:
        if show_git:
            row.append((SAMPLE_GIT_STATUS, GIT_STATUS_COLOR, False))
        if show_branch:
            append_segment(row, branch, draft['branch_colour'])
    if not row and draft['empty_git_row'] == 'placeholder':
        git_hidden = not (draft['show_git'] or draft['show_branch'])
        message = 'Git details hidden' if git_hidden else 'no git repo'
        if draft['show_git_icon']:
            message = icon + ' ' + message
        row = [(message, PLACEHOLDER_COLOUR, False)]
    return row


def agent_sample_rows(draft, sample, now):
    remote = sample.remote and draft['show_machine']
    machine_color = settings.machine_colour(draft)
    title = name_row(draft, sample, now)
    if remote and draft['machine_position'] == 'name':
        append_segment(title, SAMPLE_MACHINE, machine_color, bold=True)
    if draft['show_tab']:
        append_segment(title, sample.tab, draft['tab_colour'])
    rows = [title]
    details = []
    if remote and draft['machine_position'] == 'details':
        details.append((SAMPLE_MACHINE, machine_color, True))
    if draft['agent_text']:
        info = sample_agent_info(draft, sample)
        if info:
            append_segment(details, info, draft['agent_colour'])
    if details:
        rows.append(details)
    return rows


def sample_agent_info(draft, sample):
    observation = {'model': sample.model, 'effort': 'medium',
                   'context': {'used': 250000, 'window': 1000000, 'percent': 25}}
    try:
        settings.validate(draft)
        return tokens(observation, sample.agent, draft)['agent_info'] or ''
    except ValueError:
        return 'Check the template syntax.'


# Curses color pair numbers, one per drawn cell so colors never clash:
# previews use 1-48 (2 panels x 24: up to 6 rows of 4 segments),
# form color fields 60-79 and palette swatches from 80 (at most 48 options).
PREVIEW_PAIRS = 1
PREVIEW_PAIRS_PER_PANEL = 24
PREVIEW_PAIRS_PER_ROW = 4
FIELD_PAIRS = 60
SWATCH_PAIRS = 80


def color_attr(fg, pair):
    try:
        color = space_colors.nearest_color(fg) if curses.COLORS >= 256 else curses.COLOR_CYAN
        curses.init_pair(pair, color, -1)
        return curses.color_pair(pair)
    except (ValueError, curses.error):
        return 0


def preview(screen, session):
    _, width = screen.getmaxyx()
    column = (width - 5) // 2
    put(screen, 2, 1, 'SAMPLE PREVIEWS  ·  first space has no Git repo', curses.A_DIM)
    now = time.monotonic()
    for panel, kind in enumerate(('spaces', 'agents')):
        left = 2 + panel * (column + 2)
        put(screen, 3, left, kind.upper(), curses.A_BOLD)
        for row, segments in enumerate(preview_rows(session, kind, now)):
            first_pair = PREVIEW_PAIRS + panel * PREVIEW_PAIRS_PER_PANEL + row * PREVIEW_PAIRS_PER_ROW
            draw_segments(screen, 4 + row, left, column, segments, first_pair)


def draw_segments(screen, y, left, width, segments, first_pair):
    x = left
    for index, (text, fg, bold) in enumerate(segments):
        available = left + width - x
        if available <= 0:
            break
        attr = color_attr(fg, first_pair + index)
        put(screen, y, x, text[:available], attr | (curses.A_BOLD if bold else 0))
        x += len(text)


def animation_cells(width):
    size = max(1, (width - 4) // len(settings.ANIMATIONS))
    return [(ident, 2 + i * size, size) for i, ident in enumerate(settings.ANIMATIONS)]


def palette_cells(width, count):
    """(index, row, x) per swatch: three squares per hue, with a gutter between hues."""
    columns = space_colors.columns_for(width)
    cells = []
    for i in range(count):
        row = 3 + (i // columns) * 3
        x = 2 + ((i % columns) // 3) * 18 + (i % 3) * 5
        cells.append((i, row, x))
    return cells


def field_height(session, field, width):
    key = field[0]
    if key == 'animation':
        return 5 if (width - 4) // 7 >= 10 else 3
    if key == 'text_template':
        template_lines = textwrap.wrap(session.value(key) or '(empty)', width - 4)
        return 3 + len(template_lines) + len(template_help(width))
    return 1


def form_layout(session, section, width):
    """Return the section's fields, each field's (row, height), and the total rows."""
    fields = active_fields(session, section)
    positions = []
    row = 0
    for field in fields:
        height = field_height(session, field, width)
        positions.append((row, height))
        row += height
    return fields, positions, row


class FormCanvas:
    """Draws form rows, offset by scroll, into screen rows top..bottom-1 and
    records click targets in screen coordinates."""

    def __init__(self, screen, top, bottom, scroll):
        self.screen = screen
        self.top = top
        self.bottom = bottom
        self.scroll = scroll
        self.hits = []

    def screen_y(self, row):
        y = self.top + row - self.scroll
        return y if self.top <= y < self.bottom else None

    def draw(self, row, x, text, attr=0):
        y = self.screen_y(row)
        if y is not None:
            put(self.screen, y, x, text, attr)

    def hit(self, row, x, length, action):
        y = self.screen_y(row)
        if y is not None:
            self.hits.append((y, x, x + length, action))


VALUE_COLUMN = 31


def draw_form(screen, session, section, selected, scroll, top, bottom, color_target, palette_focus):
    """Draw a clipped, scrollable form; return its click targets and total height in rows."""
    _, width = screen.getmaxyx()
    fields, positions, total = form_layout(session, section, width)
    canvas = FormCanvas(screen, top, bottom, scroll)
    for index, (field, (row, height)) in enumerate(zip(fields, positions)):
        highlighted = index == selected and not palette_focus
        draw_field(canvas, session, field, index, row, height, width, index == selected, highlighted)
    if SECTIONS[section][0] == 'Colors':
        total = draw_palette(canvas, session, fields, color_target, palette_focus, total + 1, width)
    return canvas.hits, total


def draw_field(canvas, session, field, index, row, height, width, selected, highlighted):
    key, label, _, _ = field
    inactive = bool(inactive_reason(session, key))
    attr = curses.A_REVERSE if highlighted else 0
    if inactive:
        attr |= curses.A_DIM
    canvas.draw(row, 1, ('> ' if selected else '  ') + label, attr)
    canvas.hit(row, 1, width - 2, ('field', index))
    if key == 'animation':
        canvas.draw(row, VALUE_COLUMN, display_value(session, key), attr)
        draw_animation_strip(canvas, session, index, row, height, width, inactive)
    elif key == 'text_template':
        draw_template_field(canvas, session, index, row, width, attr)
    elif is_color(key):
        current = session.value(key)
        fg = next(fg for value, _, fg in color_options(session, key) if value == current)
        swatch_attr = curses.A_DIM if inactive else color_attr(fg, FIELD_PAIRS + index)
        canvas.draw(row, VALUE_COLUMN, '██', swatch_attr)
        canvas.draw(row, VALUE_COLUMN + 3, display_value(session, key), attr)
    else:
        canvas.draw(row, VALUE_COLUMN, display_value(session, key), attr)


def draw_animation_strip(canvas, session, index, row, height, width, inactive):
    """A clickable live preview of every animation below the field."""
    now = 0 if inactive else time.monotonic()
    for ident, x, size in animation_cells(width):
        glyph = symbol('working', now, session.draft | {'animation': ident, 'animate': True})
        chosen = ident == session.draft['animation']
        if inactive:
            glyph_attr = curses.A_DIM
        else:
            glyph_attr = curses.A_REVERSE if chosen else 0
        cell = '[ ' + glyph + ' ]' if chosen else '  ' + glyph + '  '
        canvas.draw(row + 1, x, cell, glyph_attr)
        labels = textwrap.wrap(settings.ANIMATIONS[ident][0], size - 1) if size >= 10 else []
        label_attr = curses.A_BOLD if chosen and not inactive else curses.A_DIM
        for dy, line in enumerate(labels[:2]):
            canvas.draw(row + 2 + dy, x, line, label_attr)
        for dy in range(1, height - 1):
            canvas.hit(row + dy, x, size, ('animation', ident, index))


def draw_template_field(canvas, session, index, row, width, attr):
    lines = textwrap.wrap(session.value('text_template') or '(empty)', width - 4)
    for dy, line in enumerate(lines, 1):
        canvas.draw(row + dy, 3, line, attr)
        canvas.hit(row + dy, 1, width - 2, ('field', index))
    help_top = row + 3 + len(lines)
    for dy, line in enumerate(template_help(width)):
        canvas.draw(help_top + dy, 3, line, curses.A_DIM)


def draw_palette(canvas, session, fields, target, palette_focus, start, width):
    """Draw the swatch grid for the target color field; return the form's new height."""
    options = color_options(session, target)
    label = next(field[1] for field in fields if field[0] == target)
    canvas.draw(start, 1, label + ' — choose a swatch', curses.A_BOLD)
    if palette_focus:
        hint = 'Arrows: choose   Enter / Esc: back to fields'
    else:
        hint = 'Click a swatch, or select its field and press Enter.'
    canvas.draw(start + 1, 1, hint, curses.A_DIM)
    inactive = bool(inactive_reason(session, target))
    current = session.value(target)
    for index, row, x in palette_cells(width, len(options)):
        value, _, fg = options[index]
        attr = curses.A_DIM if inactive else color_attr(fg, SWATCH_PAIRS + index)
        for dy in (0, 1):
            canvas.draw(start + row + dy, x, '████', attr)
            canvas.hit(start + row + dy, x, 4, ('swatch', value))
        if value == current:
            canvas.draw(start + row + 1, x - 1, '>', curses.A_BOLD)
            canvas.draw(start + row + 1, x + 4, '<', curses.A_BOLD)
    grid_rows = math.ceil(len(options) / space_colors.columns_for(width))
    end = start + 3 + grid_rows * 3
    canvas.draw(end, 1, display_value(session, target), curses.A_BOLD)
    if target == '@colour' and session.wid is None:
        canvas.draw(end + 1, 1, 'Sample only; no real space is changed.', curses.A_DIM)
    return end + 2


MIN_HEIGHT, MIN_WIDTH = 24, 48
PREVIEW_MIN_HEIGHT = 32
NAVIGATION_HINT = 'Tab: page  Up/Down: select  Left/Right: change  Enter: edit  PgUp/PgDn: scroll'
BUTTONS = (('A Apply', 'a'), ('U Reload', 'u'), ('D Defaults', 'd'), ('Q Close', 'q'))
PAGE_KEYS = tuple(str(number) for number in range(1, len(SECTIONS) + 1))
ACTIVATE_KEYS = (*ENTER_KEYS, ' ')
ARROW_KEYS = (curses.KEY_LEFT, curses.KEY_RIGHT, curses.KEY_UP, curses.KEY_DOWN)
LEFT_CLICK = curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED
WHEEL_UP = curses.BUTTON4_PRESSED
WHEEL_DOWN = getattr(curses, 'BUTTON5_PRESSED', 0)
WHEEL_STEP = 3


@dataclass
class View:
    """Navigation state that survives between frames."""
    section: int = 0
    selected: int = 0
    scroll: int = 0
    color_target: str = '@colour'
    palette_focus: bool = False
    pending: str | None = None  # 'close' or 'reload' while awaiting confirmation
    reveal: bool = True  # scroll the selection into view on the next frame

    def open_page(self, section):
        self.section = section
        self.reset_page()

    def reset_page(self):
        self.selected = 0
        self.scroll = 0
        self.palette_focus = False


@dataclass
class Frame:
    """What one drawn frame needs for input handling."""
    height: int
    width: int
    fields: list
    total: int
    visible: int
    hits: list = dataclass_field(default_factory=list)
    tab_hits: list = dataclass_field(default_factory=list)
    button_hits: list = dataclass_field(default_factory=list)

    @property
    def max_scroll(self):
        return max(0, self.total - self.visible)


def init_curses(screen):
    curses.curs_set(0)
    curses.set_escdelay(30)
    screen.keypad(True)
    screen.timeout(60)
    curses.mouseinterval(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.start_color()
    curses.use_default_colors()


def screen_main(screen, session):
    init_curses(screen)
    view = View()
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        draw_title(screen, session)
        if height < MIN_HEIGHT or width < MIN_WIDTH:
            if wait_in_small_pane(screen, session, view):
                return
            continue
        frame = draw_settings(screen, session, view)
        if frame is None:
            continue
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        try:
            if handle_input(screen, session, view, frame, key):
                return
        # A failed or timed-out config check reports the error and keeps the draft.
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            session.message = str(error)


def draw_title(screen, session):
    state = '  [unsaved]' if session.dirty else '  [saved]'
    put(screen, 0, 1, 'HERDR SIDEBAR CUSTOMIZER' + state, curses.A_BOLD)
    put(screen, 1, 1, 'Draft preview only. Apply saves changes.', curses.A_DIM)


def wait_in_small_pane(screen, session, view):
    """Show the resize notice and read one key; return True to close."""
    put(screen, 3, 1, 'Enlarge this pane (48 columns / 24 rows minimum).')
    put(screen, 4, 1, 'Q / Esc closes; unsaved changes need a second press.')
    screen.refresh()
    try:
        key = screen.get_wch()
    except curses.error:
        return False
    if key not in CLOSE_KEYS:
        view.pending = None
        return False
    if not session.dirty or view.pending == 'close':
        return True
    view.pending = 'close'
    return False


def draw_settings(screen, session, view):
    """Draw a full frame. Return None when the scroll had to be clamped and the
    frame must be redrawn before reading input."""
    height, width = screen.getmaxyx()
    if height >= PREVIEW_MIN_HEIGHT:
        preview(screen, session)
        tab_y = 11
    else:
        put(screen, 2, 1, 'Sample previews: enlarge pane to show.', curses.A_DIM)
        tab_y = 4
    tab_hits, tab_y = draw_tabs(screen, view.section, width, tab_y)
    fields, positions, form_rows = form_layout(session, view.section, width)
    view.selected = min(view.selected, len(fields) - 1)
    field = fields[view.selected]
    if is_color(field[0]):
        view.color_target = field[0]
    footer = footer_lines(session, field, width)
    top = tab_y + 2
    bottom = height - len(footer) - 2
    visible = max(1, bottom - top)
    if view.reveal:
        reveal_selection(session, view, positions, form_rows, width, visible)
    hits, total = draw_form(screen, session, view.section, view.selected, view.scroll,
                            top, bottom, view.color_target, view.palette_focus)
    frame = Frame(height, width, fields, total, visible, hits, tab_hits)
    if view.scroll > frame.max_scroll:
        view.scroll = frame.max_scroll
        return None
    for y, (line, attr) in enumerate(footer, bottom + 1):
        put(screen, y, 1, line, attr)
    frame.button_hits = draw_buttons(screen, height)
    return frame


def draw_tabs(screen, section, width, y):
    """Draw page tabs, wrapping as needed; return click targets and the last tab row."""
    hits = []
    x = 1
    for index, (name, _) in enumerate(SECTIONS):
        label = f' {index + 1} {name} '
        if x + len(label) > width - 1:
            x = 1
            y += 1
        put(screen, y, x, label, curses.A_REVERSE if index == section else curses.A_DIM)
        hits.append((y, x, x + len(label), index))
        x += len(label)
    return hits, y


def footer_lines(session, field, width):
    """Wrapped (line, attr) pairs for field help, status and navigation hints."""
    help_text = inactive_reason(session, field[0]) or field_help(session, field)
    if session.message:
        status = session.message
    elif session.dirty:
        status = 'Unsaved changes. Apply to save.'
    else:
        status = 'Saved settings. No pending changes.'
    lines = []
    for text, attr in ((help_text, curses.A_DIM), (status, curses.A_BOLD),
                       (NAVIGATION_HINT, curses.A_DIM)):
        lines.extend((line, attr) for line in textwrap.wrap(text, width - 2))
    return lines


def draw_buttons(screen, height):
    """Draw the action buttons on the last row; return (left, right, key) targets."""
    hits = []
    x = 1
    for label, key in BUTTONS:
        put(screen, height - 1, x, '[' + label + ']', curses.A_BOLD)
        hits.append((x, x + len(label) + 2, key))
        x += len(label) + 4
    return hits


def reveal_selection(session, view, positions, form_rows, width, visible):
    """Scroll so the selected field, or the focused swatch, is on screen."""
    row, size = positions[view.selected]
    if view.palette_focus:
        options = color_options(session, view.color_target)
        current = session.value(view.color_target)
        index = next(i for i, (value, _, _) in enumerate(options) if value == current)
        row = form_rows + 1 + palette_cells(width, len(options))[index][1]
        size = 2
    size = min(size, visible)
    if row < view.scroll:
        view.scroll = row
    elif row + size > view.scroll + visible:
        view.scroll = row + size - visible
    view.reveal = False


def handle_input(screen, session, view, frame, key):
    """Act on one key or mouse event; return True when the pane should close."""
    if key == curses.KEY_RESIZE:
        view.reveal = True
        return False
    if key == curses.KEY_MOUSE:
        _, x, y, _, buttons = curses.getmouse()
        if not buttons & (LEFT_CLICK | WHEEL_UP | WHEEL_DOWN):
            return False
    # Any handled event answers a pending confirmation, cancelling it unless it repeats.
    previous_pending = view.pending
    view.pending = None
    if previous_pending:
        session.message = ''
    if key == curses.KEY_MOUSE:
        key = handle_mouse(session, view, frame, x, y, buttons)
        if key is None:
            return False
    return handle_key(screen, session, view, frame, key, previous_pending)


def handle_mouse(session, view, frame, x, y, buttons):
    """Act on a click or wheel event; return a key to process next, or None."""
    if buttons & LEFT_CLICK:
        return handle_click(session, view, frame, x, y)
    if buttons & WHEEL_UP:
        view.scroll = max(0, view.scroll - WHEEL_STEP)
    elif buttons & WHEEL_DOWN:
        view.scroll = min(frame.max_scroll, view.scroll + WHEEL_STEP)
    return None


def handle_click(session, view, frame, x, y):
    tab = next((index for row, left, right, index in frame.tab_hits
                if y == row and left <= x < right), None)
    if tab is not None:
        view.open_page(tab)
        return None
    # Later targets are drawn on top, so they win.
    action = next((action for row, left, right, action in reversed(frame.hits)
                   if y == row and left <= x < right), None)
    if action is None:
        if y != frame.height - 1:
            return None
        return next((key for left, right, key in frame.button_hits if left <= x < right), None)
    kind = action[0]
    if kind == 'animation':
        _, ident, index = action
        session.set('animation', ident)
        view.selected = index
    elif kind == 'swatch':
        session.set(view.color_target, action[1])
        view.palette_focus = True
        view.selected = next(i for i, field in enumerate(frame.fields)
                             if field[0] == view.color_target)
    else:
        view.selected = action[1]
        view.palette_focus = False
        return '\n'
    return None


def handle_key(screen, session, view, frame, key, previous_pending):
    """Dispatch one key; return True when the pane should close."""
    field = frame.fields[view.selected]
    if view.palette_focus and key in (*ENTER_KEYS, ESCAPE):
        view.palette_focus = False
        view.reveal = True
        return False
    if key in CLOSE_KEYS:
        if not session.dirty or previous_pending == 'close':
            return True
        view.pending = 'close'
        session.message = 'Unsaved changes. Q / Esc again discards; A applies.'
        return False
    if key in ('a', 'A'):
        session.apply()
    elif key in ('u', 'U'):
        reload_session(session, view, previous_pending)
    elif key in ('d', 'D'):
        session.defaults()
        view.reset_page()
    elif key in ('\t', curses.KEY_BTAB):
        step = -1 if key == curses.KEY_BTAB else 1
        view.open_page((view.section + step) % len(SECTIONS))
    elif key in PAGE_KEYS:
        view.open_page(int(key) - 1)
    elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
        step = frame.visible if key == curses.KEY_NPAGE else -frame.visible
        view.scroll = max(0, min(frame.max_scroll, view.scroll + step))
    elif view.palette_focus and key in ARROW_KEYS:
        columns = space_colors.columns_for(frame.width)
        steps = {curses.KEY_LEFT: -1, curses.KEY_RIGHT: 1,
                 curses.KEY_UP: -columns, curses.KEY_DOWN: columns}
        session.cycle(field, steps[key])
        view.reveal = True
    elif key in (curses.KEY_UP, curses.KEY_DOWN):
        step = -1 if key == curses.KEY_UP else 1
        view.selected = (view.selected + step) % len(frame.fields)
        view.reveal = True
    elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
        session.cycle(field, -1 if key == curses.KEY_LEFT else 1)
        view.reveal = True
    elif key in ACTIVATE_KEYS:
        activate_field(screen, session, view, field)
    return False


def reload_session(session, view, previous_pending):
    """Reload saved settings, asking first when that would discard edits."""
    if session.dirty and previous_pending != 'reload':
        view.pending = 'reload'
        session.message = 'Discard unsaved edits? U again reloads; any other key cancels.'
        return
    session.reload()
    session.message = 'Reloaded saved settings.'
    view.reset_page()
    view.reveal = True


def activate_field(screen, session, view, field):
    """Enter or Space: open the palette, edit text, or step a choice."""
    key, label, options, _ = field
    reason = inactive_reason(session, key)
    if reason:
        session.message = reason
        return
    if is_color(key):
        view.color_target = key
        view.palette_focus = True
    elif options is None:
        session.set(key, edit_text(screen, label, session.value(key)))
    else:
        session.cycle(field, 1)
    view.reveal = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('open', 'edit'))
    parser.add_argument('--right', action='store_true', help='Open beside the calling pane')
    args = parser.parse_args()
    endpoint = os.environ.get('HERDR_SOCKET_PATH')
    if os.environ.get('HERDR_ENV') != '1' or not endpoint:
        parser.error('Run inside Herdr')
    from installation import record
    installed = record()
    if args.command == 'open':
        entrypoint = 'settings' if installed.get('enabled') else 'setup'
        params = {'plugin_id': PLUGIN, 'entrypoint': entrypoint}
        if args.right:
            params.update(placement='split', direction='right',
                          target_pane_id=os.environ['HERDR_PANE_ID'], focus=False)
        rpc(endpoint, 'plugin.pane.open', params)
    elif not installed.get('enabled'):
        from manage import setup_screen
        curses.wrapper(setup_screen, endpoint)
    else:
        directory = endpoint_state(state_root(), endpoint)
        session = Session(endpoint, directory, layout_mode=installed.get('mode'))
        curses.wrapper(screen_main, session)


if __name__ == '__main__':
    main()
