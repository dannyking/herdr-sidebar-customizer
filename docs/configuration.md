# Configuration

This page covers layout ownership, Git rows, text templates, manual tokens,
saved SSH machines (including Windows), colors and motion, and local data.

## Layout ownership

Managed setup owns `rows` and `row_gap` in `[ui.sidebar.spaces]` and
`[ui.sidebar.agents]`, plus this plugin's `[[keys.command]]` bindings. It saves
the original config privately, checks the generated config with
`herdr config check`, and reloads it. Other settings are kept, and conflicting
shortcuts are rejected. Settings Apply refuses to overwrite owned rows that
another editor changed. Restore changes only values that still match the
plugin's last applied version.

Use ordinary explicit TOML section headers for these tables. Unusual inline or
quoted table forms may be refused before any file is written.

Preferences are shared across sessions. Space colors belong to each server
socket and workspace ID, so renaming a space keeps its color. New-space color
defaults affect only new assignments. Applying layout changes reloads the
calling server; other Herdr servers that use the same config may need
`herdr server reload-config`. Do not manage different config files with the same
plugin preference directory.

Manual setup writes preferences and publishes metadata, and leaves your config
alone. Controls that affect layout or styling then need matching changes in
your rows; the preview shows the managed layout. In manual layout the
**Shortcuts** page is inactive and the spacing action is unavailable. Restore
leaves manually integrated rows in place; remove their plugin tokens yourself
before uninstalling.

## Git rows

**Spaces → Spaces without Git** has three choices:

- **Shorter row:** collapse the second row when it has no Git details.
- **Blank line** (default): keep an empty second row so spaces have equal heights.
- **Explain why:** keep the row with muted text such as `no git repo`.

Older settings files and the JSON keep the saved values `hide`, `blank` and
`placeholder` for these.

The placeholder tells apart a missing repository, a detached HEAD, hidden
details and grouped worktrees. A repository is never labeled missing just
because no Git details are visible. **Spaces → Row spacing** separately adds
a blank row between entries.

**Spaces → Git icon** adds a Nerd Font glyph to populated rows, including
placeholder text. It is **None** by default and needs a Nerd Font in your
terminal. The other choices are the eleven icons below, with live previews.
Choosing **None** keeps the last icon saved for next time, and blank rows stay
blank. The icon and its text share
one token, so no separator dot appears between them. Placeholder text is dark
gray (`#585b70`).

| Saved ID | Icon |
|---|---|
| `01` | GitHub outline |
| `03` | GitHub alternate |
| `04` | GitHub filled |
| `21` | Forked repository |
| `27` | Repository |
| `41` | Branch (Material), the default |
| `43` | Branch (Powerline) |
| `44` | Git diamond (Devicons) |
| `45` | Git wordmark |
| `47` | Git square |
| `48` | Git diamond (Material) |

Herdr 0.9 has no minimum row height option, so the plugin publishes
`$space_git_padding` (blank or explanatory text) for empty Git rows only. It uses
Herdr's read-only worktree lookup and, when only status is shown, a local Git
comparison. It never fetches from the network or changes a repository. Changes
show up on the next collector refresh, within a few seconds.

Managed rows use:

- `$space_git_branch` for the icon and branch together, when the icon is on.
  Without the icon, Herdr's native branch token is used.
- `$space_git_icon` for status-only rows.
- `$space_git_padding` for placeholders.

Native status tokens keep their semantic colors. If you integrate these tokens
manually with the icon on, replace the native branch token with
`$space_git_branch` to avoid showing the branch twice.

## Text templates

On the Agents page, choose **Details text → Pick fields** for individual field
switches and custom **Agent names**, or **Write a template** to control the
whole label with **Template**. Each mode keeps its own choices.

```text
{agent} {model} · {effort} · {context}
{model} · {remaining_pct} left
{agent} · {used} of {capacity}
```

Fields: `{agent}`, `{model}`, `{effort}`, `{context}`, `{used}`, `{remaining}`,
`{capacity}`, `{used_pct}`, `{remaining_pct}`. Separate optional groups with
`·`; a group disappears when none of its fields has a value. Custom mode uses the
standard agent names and ignores the Pick fields switches. Labels are capped
at Herdr's 80-character metadata limit.

## Manual tokens

The agent metadata tokens are `$agent_info`, `$model`, `$model_id`, `$effort`,
`$model_effort`, `$context`, `$context_used`, `$context_left`,
`$context_window`, `$context_used_pct`, `$context_left_pct`, `$context_compact`
and `$model_context`. The optional `$claude_icon` and `$codex_icon` tokens need a
suitable Nerd Font.

For example, to keep native names and show details beneath them:

```toml
[ui.sidebar.agents]
rows = [["state_icon", "workspace", "tab"], ["$agent_info"]]
```

For the colored, animated name, include all twelve `$space_<hue>` tokens; only
one has a value for a given space. The hues are red, orange, yellow, lime,
green, teal, cyan, blue, indigo, violet, magenta and rose. Each value holds the
state glyph and the name, plus invisible shade markers that the generated style
rules match. The glyph and name share one token because Herdr puts a separator
between separate custom tokens.

To print complete styled example rows without changing any file, run this from
a checkout:

```bash
PYTHONPATH=src python3 -c 'import sidebar_settings as s; print(s.render_config("", s.DEFAULTS))'
```

## Saved SSH machines

**Agents → Machine labels** (on by default) shows Herdr's label for the saved
machine an agent runs on. **Agents → Machine label position** puts it after the
space name (**After space name**), for example `○ Website · build-box · review`, or at the start of the
details row (**In details row**). Local agents are never labeled. The label
uses the agent details color, in bold, unless you change
**Colors → Machine label**. It is
Herdr's native token, so it has one fixed color rather than the space's, and no
surrounding text such as parentheses. To change its text, rename the saved
machine with `herdr machine rename`.

Herdr's client draws the sidebar for every saved machine from your local config,
but the plugin's tokens come from the server that owns each space or agent. A
machine shows plugin rows only when the plugin runs there too. Without it:

- **Agents** keep a basic entry. The managed layout adds Herdr's native `agent`
  and `state_text` as fallback text, for example `claude · idle`. On its own
  server, the worker marks each agent whose details row it fills with an
  invisible character in Herdr's presentation-only display name and state
  labels, and hide rules on that marker stop those rows from also showing the
  fallback. With details on and collection on, agents it cannot describe (an
  unsupported agent, or a missing session ID with hints off) keep the native
  text; with details hidden or collection off, no agent shows it. Notifications,
  waits and rollups are unaffected.
- **Spaces** are not shown. Space rows have no machine token and native text
  cannot be hidden per row, so a space whose name comes only from plugin tokens
  has nothing to show until the plugin runs on that machine.

For full rows, install the plugin on the other machine and choose **manual**
layout there. Its worker then publishes colors, animation and that machine's
agent details into its own server, and your client draws them with its managed
layout. That machine's config is not edited. To do this without the setup
screen, run this from a pane on that machine, in the plugin directory:

```bash
python3 src/manage.py configure --layout manual --collect-details
```

`--collect-details` and `--no-collect-details` set detail collection; leave
both out to keep the saved choice. Colors and preferences belong to that
machine's plugin installation, so set them there.

### Windows servers

Windows servers run the worker only. The settings, setup and color picker
screens, and the Claude status-line hook, are not available on Windows yet;
`configure --claude-hook` is refused there.

1. Clone the repository and link the checkout with
   `herdr plugin link C:\path\to\checkout`. This is the tested path;
   `herdr plugin install` from GitHub has not been tested on Windows yet.
2. Optional: to change preferences, copy a `settings.json` from a Linux or
   macOS installation (see [local data](#local-data)) to
   `%APPDATA%\herdr\plugins\config\herdr-sidebar-customizer\settings.json`.
   Without one, the defaults are used.
3. Open a pane in Herdr on the Windows machine, so `HERDR_ENV` and
   `HERDR_SOCKET_PATH` are set, and go to the plugin directory. If `herdr` is
   not on `PATH`, set `HERDR_BIN_PATH` to the full path of `herdr.exe`.
4. Run `src\python.cmd src\manage.py configure --layout manual --collect-details`.
5. Check the result with `src\python.cmd src\manage.py doctor --json`.

Herdr's startup and agent hooks then keep the worker running. It talks to
Herdr through its named pipe and keeps state in
`%LOCALAPPDATA%\herdr\plugins\herdr-sidebar-customizer`. `src\python.cmd` tries
`HERDR_SIDEBAR_PYTHON`, then `py -3`, then `python`, skipping the Microsoft
Store stub.

#### Undo on Windows

From the same kind of Herdr pane, in the plugin directory, run
`src\python.cmd src\manage.py restore-now`, then
`herdr plugin uninstall herdr-sidebar-customizer`.

Restore also clears the invisible markers. Another reporter's display name or
state labels come back when it next reports.

## Tab names

**Tabs → Rename tabs from agent topic** (off by default) renames each agent's tab
from its agent's terminal title, which agents set to their task or session name,
for example after `/rename` in Claude Code. The worker does this on its normal
sync, every few seconds, so no extra process runs.

- A tab is renamed only when its agent's title changes. A name you type stays
  until the agent next retitles; the first sight of a tab counts as a change.
- **Pin** a tab to keep its name past a title change: run the `pin-tab` action
  from that tab, or `src/tab_names.py pin --tab <tab_id>` with ids from
  `herdr tab list`. Run it again to unpin. `pinned-tabs` lists pins. To give
  the action a key, set **Shortcuts → Pin tab name** (not set by default).
- Product titles such as `Claude Code`, titles shorter than three characters and
  status words are ignored; Codex's `[ ! ] Action Required | Task | repo` names the
  tab `Task`. Escape sequences and markup are removed.
- **Tabs → Tab name format** takes `{topic}` and `{n}` (the tab number), e.g.
  `{n}· {topic}`. Labels are capped at 128 characters.
- History and pins are kept per Herdr session in the plugin's state directory
  (`tab-names.json`). On a saved SSH machine, tabs are named by that machine's
  worker, so turn the setting on there.

## Colors and motion

Text colors follow the terminal theme by default. Space colors use 36 distinct
xterm-256 swatches; choose darker shades on a light background. Turning off
**Use space colors** uses the **Names when colors are off** color and keeps the saved
assignments. Herdr draws the compact sidebar rail itself; custom animations
apply to the expanded sidebar.

The seven animations have stable saved IDs (`01`, `03`, `04`, `19`, `20`, `22`,
`23`) while the UI shows their names. Rings use `○◎●◎`. Speed ranges
from 0.25× to 4×. Turning off **Animate working agents** uses the **Working**
symbol from the same Motion & symbols page.

**Symbol set** fills all five state symbols at once: **Circles** (the default,
`○ ● ● ⊘ ·` for idle, working, done, blocked and unknown), **Dots**
(`◦ • • × ·`) or **Plain ASCII** (`- * + ! ?`). Editing one symbol makes the
set **Custom**. The symbols take the space's color, so the settings page shows
each beside a sample space name.

## Local data

Paths follow Herdr's `HERDR_PLUGIN_CONFIG_DIR` and `HERDR_PLUGIN_STATE_DIR` when
it sets them. Otherwise the defaults are:

| | Linux and macOS | Windows |
|---|---|---|
| Preferences | `$XDG_CONFIG_HOME/herdr/plugins/config/herdr-sidebar-customizer/settings.json` | `%APPDATA%\herdr\plugins\config\herdr-sidebar-customizer\settings.json` |
| State | `$XDG_STATE_HOME/herdr/plugins/herdr-sidebar-customizer/` | `%LOCALAPPDATA%\herdr\plugins\herdr-sidebar-customizer\` |
| Herdr config | `$XDG_CONFIG_HOME/herdr/config.toml` | `%APPDATA%\herdr\config.toml` |

`HERDR_CONFIG_PATH` overrides the Herdr config path. Unset or empty XDG
variables default to `~/.config` and `~/.local/state`. The plugin uses
`HERDR_BIN_PATH` when it points to an executable file, and otherwise `herdr` on
`PATH`.

State holds space color assignments, model and context values, health reports,
a private restoration record with the original config, and the previous Claude
status-line command when the hook is installed. These files can contain
personal paths or configuration, so do not post them in bug reports.

Collection is off by default. When on, it reads only sessions that match the
session IDs Herdr reports. Reads are bounded and incremental.

- **Claude Code:** transcripts under `CLAUDE_CONFIG_DIR` (default `~/.claude`).
  The optional status-line hook adds the current context.
- **Codex:** session files under `CODEX_HOME` (default `~/.codex`).
- **OpenCode:** the SQLite database `$XDG_DATA_HOME/opencode/opencode.db`
  (default `~/.local/share/opencode`), opened read-only for the session's model,
  variant and latest token count. The context window comes from
  `provider.<id>.models.<id>.limit.context` in `~/.config/opencode/opencode.json`,
  or OpenCode's cached `models.json` catalog.
- **pi and omp:** they report their session file's path to Herdr, and only
  `.jsonl` files inside their own sessions directories are read. For pi these
  are `~/.pi/agent/sessions`, `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`
  or `sessionDir` in its settings. For omp they are `~/.omp/agent/sessions`
  (`PI_CONFIG_DIR` replaces `.omp`), its profiles, `$XDG_DATA_HOME/omp/sessions`
  and the same variables, as seen by the Herdr server. The active branch is
  followed from the last entry. Context follows each app's own figure: pi's total
  tokens of the latest finished reply, and omp's prompt tokens. Context windows
  come from pi's `models.json` and `models-store.json`, omp's `models.yml`, or
  the installed app's built-in catalog.

The collector never guesses a context window from a model name. A Claude session
may need a status-line refresh or a restart to pick up a newly installed
status-line command; the Herdr server does not need restarting.

Restore removes the Claude hook only while this plugin still owns it, so a later
manual replacement is kept. Keep the restoration files until Restore completes.
To erase saved metadata afterwards, stop the workers (Restore does this) and
delete only this plugin's state directory. Otherwise, reinstalling keeps your
preferences and colors.
