#!/usr/bin/env python3
"""Open the real settings UI with invented data; never touches a Herdr session."""
import curses
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import settings_ui as ui  # noqa: E402
import sidebar_settings as prefs  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with patch.object(prefs, 'preferences_path', return_value=root / 'settings.json'), \
                patch.object(ui, 'rpc', return_value={'workspaces': []}), \
                patch.object(prefs, 'apply_settings',
                             side_effect=RuntimeError('Preview demo; nothing is saved.')):
            curses.wrapper(ui.screen_main, ui.Session('synthetic-demo', root))


if __name__ == '__main__':
    main()
