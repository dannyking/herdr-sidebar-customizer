# Contributing

Thanks for helping. Bug reports, fixes and documentation improvements are all
welcome. For a larger change, please open an issue first so we can agree on the
approach.

Report security problems privately, as described in [SECURITY.md](SECURITY.md),
not in a public issue.

## Principles

- **Standard library only.** The plugin and its tests use Python 3.11+ and its
  standard library. Do not add runtime dependencies.
- **Herdr owns agent state.** Do not build a second agent-state machine or
  report lifecycle changes Herdr did not report.
- **Setup is transactional.** Setup, Apply and Restore must roll back on
  failure, preserve later edits and leave unrelated configuration alone. Test
  the failure paths when you change what the plugin owns.
- **A new setting** needs validation in `src/sidebar_settings.py`, a field in
  `src/settings_ui.py` and documentation in `docs/configuration.md`.
- **Keep saved names stable.** Action ids, metadata tokens, settings keys and
  on-disk file names are public: existing installs depend on them.
- **Synthetic data only.** Never commit personal config, runtime state, session
  records, real host names or screenshots of private projects.

## Running the tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The opt-in smoke test starts and stops its own Herdr server under temporary
directories. It does not connect to your session, read agent transcripts or
change your configuration. Herdr must be on `PATH`, or set `HERDR_BIN_PATH`.

```bash
HERDR_SMOKE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

To also exercise installing, updating and uninstalling from GitHub:

```bash
HERDR_SMOKE=1 HERDR_SMOKE_GITHUB=dannyking/herdr-sidebar-customizer \
  PYTHONPATH=src python3 -m unittest discover -s tests -p test_smoke.py -v
```

The curses test uses a real PTY and invented sample data. To look at the
settings UI with sample data, run `python3 tools/demo.py`.

## Trying a checkout in Herdr

```bash
herdr plugin link /absolute/path/to/checkout
herdr plugin action invoke herdr-sidebar-customizer.setup
```

Run Restore before you unlink the checkout or switch to an installed copy.

## Pull requests

1. Fork the repository and create a branch.
2. Keep the change focused, and add or update a unit test for each bug fix.
3. Run the unit tests and make sure they pass.
4. Add a line to `CHANGELOG.md` under **Unreleased** for user-visible changes.
5. Use American spelling in user-facing text, docs and comments.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).

## README captures

The README images come from the real curses UI through `tools/capture.py`. To
regenerate them, install `pyte` in a separate virtual environment and run that
script. Add `--gif`, with `cairosvg` and `Pillow` installed, for the animation.
`tools/capture_sidebar.py` captures the native sidebar using its own temporary
Herdr server and synthetic agent reports. The plugin and its tests do not need
these packages.

`tools/capture_sidebar.py` needs updating before it can refresh
`assets/sidebar.svg`. The worker now marks only agents whose details row it
fills, and the capture's synthetic agents have no session the worker can read,
so they also show Herdr's native `agent · state` text.
