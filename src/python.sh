#!/bin/sh
# Run a plugin entrypoint with the first Python 3.11+ available.
# Herdr passes its server's PATH to plugin commands. A server started over SSH,
# such as one set up by `herdr machine add`, may only see /usr/bin, where macOS
# ships Python 3.9. HERDR_SIDEBAR_PYTHON overrides the search.
# Text is UTF-8 everywhere, whatever the locale; settings hold symbols.
export PYTHONUTF8=1
for candidate in "${HERDR_SIDEBAR_PYTHON:-}" python3 python3.14 python3.13 python3.12 python3.11 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.local/bin/python3"; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec "$candidate" "$@"
    fi
done
echo "Herdr Sidebar Customizer needs Python 3.11 or newer on PATH." >&2
exit 1
