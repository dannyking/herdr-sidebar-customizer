"""Portable paths and locks shared by all plugin entrypoints."""
from contextlib import contextmanager
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess

import portable

PLUGIN = "herdr-sidebar-customizer"
NAME = "Herdr Sidebar Customizer"


# Herdr's limit on a metadata token's text.
HERDR_TEXT_LIMIT = 80


def base_dir(xdg_var, xdg_default, windows_var):
    """On Windows, the folder Herdr uses (APPDATA or LOCALAPPDATA); elsewhere the XDG directory."""
    return windows_dir(windows_var) or Path(os.environ.get(xdg_var) or Path.home() / xdg_default)


def windows_dir(name):
    if portable.WINDOWS and os.environ.get(name):
        return Path(os.environ[name])
    return None


def config_path():
    override = os.environ.get("HERDR_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return (base_dir("XDG_CONFIG_HOME", ".config", "APPDATA") / "herdr/config.toml").resolve()


def preferences_path():
    default = base_dir("XDG_CONFIG_HOME", ".config", "APPDATA") / "herdr/plugins/config" / PLUGIN
    return Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR") or default) / "settings.json"


def state_root():
    default = base_dir("XDG_STATE_HOME", ".local/state", "LOCALAPPDATA") / "herdr/plugins" / PLUGIN
    return Path(os.environ.get("HERDR_PLUGIN_STATE_DIR") or default)


def herdr_binary():
    for candidate in (os.environ.get("HERDR_BIN_PATH"), shutil.which("herdr")):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise RuntimeError("Herdr is not executable. Put herdr on PATH and try again.")


def installation_path():
    key = hashlib.sha256(str(config_path()).encode()).hexdigest()[:24]
    return state_root() / "installations" / (key + ".json")


@contextmanager
def config_lock():
    """Global lock for config and preferences, shared by every Herdr endpoint."""
    root = state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "configuration.lock").open("a") as handle:
        portable.lock(handle)
        yield


def require_context():
    endpoint = os.environ.get("HERDR_SOCKET_PATH")
    if os.environ.get("HERDR_ENV") != "1" or not endpoint:
        raise RuntimeError("Run this action inside Herdr.")
    return endpoint


def check_prerequisites():
    # Import here so CLI diagnostics can explain incomplete Python installs.
    # Windows runs only the worker for now, which needs no curses.
    if not portable.WINDOWS:
        import curses
    import tomllib
    result = subprocess.run([herdr_binary(), "--version"], capture_output=True,
                            text=True, timeout=10, check=True)
    version = result.stdout.strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (0, 9, 1):
        raise RuntimeError("Herdr 0.9.1 or newer is required.")
    return version


@contextmanager
def palette_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'space-colors-sync.lock').open('a') as handle:
        portable.lock(handle)
        yield


def worker_running(directory):
    path = directory / 'watch.lock'
    if not path.exists():
        return False
    with path.open('a') as handle:
        return not portable.try_lock(handle)
