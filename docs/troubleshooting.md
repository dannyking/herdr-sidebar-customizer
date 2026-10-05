# Troubleshooting

This page covers diagnostics, missing details, terminals, SSH and recovery.

## Start with diagnostics

Run `herdr plugin action invoke herdr-sidebar-customizer.doctor` inside Herdr.
From a Herdr pane you can also run `python3 src/manage.py doctor --json` in the
plugin directory (`src\python.cmd src\manage.py doctor --json` on Windows). The
report holds version strings, booleans, counts and short issues. It never
includes config contents, session paths or agent text.

- **Not configured:** run the setup action. Installing or enabling a plugin does
  not run setup.
- **Stopped worker:** run `herdr plugin action invoke herdr-sidebar-customizer.refresh`.
  Space names stay readable, but their state and animation may be stale. Check
  Herdr's native state while the worker is unhealthy.
- **Invalid settings:** fix the JSON, or use **U** (reload) or **D** (defaults)
  in the settings panel. A running worker keeps its last valid settings.
- **Sidebar configuration was changed outside the plugin:** run Restore, review
  the edits it preserved, then run setup again, or switch to manual layout.

## Missing details

**Collect agent details** and **Agent details row** on the Agent text page are
separate switches; details need both. They also need a session ID reported by
Herdr and matching local data. Unknown fields stay blank.

- **Claude Code:** current context needs the status-line hook. Turn it on in
  setup.
- **Codex, OpenCode, pi and omp** show the latest recorded turn. OpenCode, pi
  and omp show context only when the model's context window is known, from the
  app's own configuration or catalog.

Diagnostics, and the help line under **Collect agent details**, name each gap
and its fix:

- **No session ID:** if Herdr's integration for that agent is missing, run
  `herdr integration install claude` (or `codex`, `opencode`, `pi`, `omp`), then
  restart or resume the agent. With **Missing-detail hints** on, its details row
  reads `Claude · needs herdr integration`. If the integration is installed, the
  agent started before it, and the row shows `restart for details` until you
  restart or resume it.
- **Claude without context:** the status-line hook is not installed. Run setup
  with it turned on; until then the details row ends with `ctx needs hook`.

Turn off **Missing-detail hints** to keep these out of the sidebar; diagnostics
still report them.

On Windows, where the status-line hook is not available yet, diagnostics say so
instead of suggesting it.

Diagnostics also report two problems that have no sidebar hint:

- **A session this plugin will not read:** the agent reported a session
  reference the plugin rejects, for example a pi or omp session file outside
  its sessions directories, or a malformed Claude or Codex session ID. That
  agent keeps Herdr's native text. Keep its sessions in a directory listed
  under [local data](configuration.md#local-data), as seen by the Herdr server.
- **Reading details failed:** an unexpected error while reading one agent's
  session. Only that agent's details are blank. Please
  [report it](https://github.com/dannyking/herdr-sidebar-customizer/issues/new/choose)
  with the diagnostics output.

The `status` action prints the raw values as `unreadable_session` (a count per
agent kind) and `observe_errors` (an error type per agent kind).

## Missing branch or Git status

Git branch and status come from Herdr's worktree data: Herdr's native tokens,
or the plugin's `$space_git_branch` when **Show Git icon** is on. They describe
the repository that contains the pane's working directory; a directory above a
repository does not count as inside it. Status uses Herdr's native ahead/behind
information, not a changed-file count. See [Git rows](configuration.md#git-rows)
for the empty-row choices.

## Terminal and SSH

The settings panel needs at least 48 columns and 24 rows; a larger popup shows
more. Use Page Up and Page Down to scroll. Choose different state symbols if
your font cannot show one. Your theme and terminal default colors affect the
text previews.

When you run Herdr in a shell reached over SSH, install Python and the plugin on
that host. In Herdr's native remote-client mode, the client can have its own UI
configuration, and server-side setup cannot edit that client's local file. Use
manual layout on the client if needed, and check the config path setup shows.

Herdr runs plugin commands with its server's `PATH`. A server started over SSH
may see only the system Python, such as 3.9 on macOS, so `src/python.sh` also
tries `python3.14` down to `python3.11`, `/opt/homebrew/bin`, `/usr/local/bin`
and `~/.local/bin`. Set `HERDR_SIDEBAR_PYTHON` in the server's environment to
choose an interpreter yourself.

On Windows, `src\python.cmd` tries `HERDR_SIDEBAR_PYTHON`, then `py -3`, then
`python` (skipping the Microsoft Store stub). It also turns on Python's UTF-8
mode, because otherwise Windows Python reads the settings in the ANSI code page
and fails on their symbols.

If a saved SSH machine's spaces are missing, or its agents show only native
`claude · idle` text, the plugin is not running on that machine. See
[Saved SSH machines](configuration.md#saved-ssh-machines).

## Recovery after an early uninstall

Run Restore before uninstalling or updating. If you removed the plugin first,
reinstall it at the same location, run Restore, then uninstall again. Herdr
normally keeps the plugin's preference and state directories.

If a Claude hook points to a deleted checkout, reinstall the plugin and run
setup with the hook on, or restore the previous `statusLine` yourself from
`previous-statusline.json` in the plugin's state directory. If the saved
original is missing, Restore reports it instead of replacing another command.
If Restore reports `Claude settings.json (remove the status-line hook by hand)`,
your Claude settings file is not valid JSON: fix it and run Restore again, or
remove the hook yourself.

Restore lists owned rows or shortcuts you changed after setup and leaves them
in place. Remove any remaining `$space_*` tokens or plugin bindings from those
rows yourself before uninstalling. Native rows such as
`[["state_icon", "workspace"], ["branch", "git_status"]]` make a working layout
for `[ui.sidebar.spaces]`; check the result with `herdr config check`.
