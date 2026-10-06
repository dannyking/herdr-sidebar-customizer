# Changelog

All notable changes to this project are listed here.

## Unreleased

### Settings panel

- Regrouped into six pages: Spaces, Agents, Tabs, Colors, Motion & symbols and
  Shortcuts (keys 1–6). Colors, Agents and Motion & symbols have small
  headings.
- Related switches are now single choices: **Space row shows** (symbol and
  name, name only, symbol only), **Git icon** (None or one of eleven),
  **Context** (used %, remaining %, off) and **Spaces without Git** (shorter
  row, blank line, explain why). They save the same keys as before, so
  existing settings files keep working.
- Plain-language values everywhere, such as **After space name**, **Not set**
  and **Compact / Spaced**, and a **Row spacing** control on both list pages.
- **Collect agent details** comes first on the Agents page, with a status line
  from the worker, for example "Reading details for 5 of 6 agents · 1 needs
  Herdr's codex integration".
- **Agent names** shows all five labels in one row and edits them in turn.
- **Symbol set** applies Circles, Dots or Plain ASCII to all five state
  symbols, and each symbol is shown beside a sample name in the space's color.
- Shorter animation names, so every gallery label fits on one line.
- The swatch name, such as "Teal · Medium", appears under the color palette.
- The title line shows **Saved** or the number of unsaved changes; transient
  messages share one status line, and one clickable key-hint line replaces
  the separate hints and buttons.
- Help appears under the selected field rather than at the bottom of the pane.
- **Reload** is now **Undo all** (still **U**, still asking first).
- The previews stack the Agents list under the Spaces list, like the sidebar,
  and are hidden on the Shortcuts page.

### Shortcuts

- New optional **Pin tab name** shortcut for the `pin-tab` action, saved as
  `shortcut_pin_tab` and not set by default. A `pin-tab` binding you wrote by
  hand is left alone.

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
