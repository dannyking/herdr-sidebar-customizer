# Changelog

All notable changes to this project are listed here.

## Unreleased

## 0.1.0 - 2026-10-04

First public release.

### Sidebar

- 36 color swatches shared by each space and its agents, with new-space defaults
  and theme-following text colors.
- Seven working animations with adjustable speed, a motion-off switch and
  editable state symbols.
- Independent spacing and visibility for the Spaces and Agents lists.
- Git rows with an optional Nerd Font icon (eleven to choose from) and a choice
  of hidden, blank or explanatory placeholder rows for spaces without Git
  details.
- A settings panel with seven pages, live previews, mouse support, editable
  shortcuts, and inactive controls that explain what turns them on.

### Agent details

- Model, reasoning effort and context use for Claude Code, Codex, OpenCode, pi
  and omp, read from their local session data, with automatic fields or a
  custom template. Collection is opt-in; nothing is sent anywhere.
- An optional Claude status-line hook for current context, which preserves and
  chains any existing status line.
- Diagnostics, settings help and optional sidebar hints that say why details
  are missing and how to fix it.
- Optional agent tab names: a tab follows its agent's topic, for example after
  `/rename`, keeps names typed by hand until the topic changes, and can be
  pinned.

### Saved SSH machines

- An optional machine label for agents on other machines, after the space name
  or at the start of the details row.
- Native `agent · state` fallback text for agents on machines that do not run
  the plugin.
- Windows servers run the worker through Herdr's named pipe, so Windows saved
  machines get full rows. The settings, setup and color picker screens and the
  Claude hook are Linux and macOS only.

### Setup

- Managed layout, or manual tokens for your own rows.
- Reversible setup and Restore that keep unrelated configuration and later
  edits, symlinked settings files and comments, and roll back on any error.
- Requires Herdr 0.9.1 or newer and Python 3.11 or newer, with no other
  dependencies.
