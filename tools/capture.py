#!/usr/bin/env python3
"""Capture the real synthetic curses demo as SVG. Dev dependency: pyte."""
import codecs
import fcntl
from html import escape
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pyte

ROOT = Path(__file__).resolve().parents[1]
COLUMNS, ROWS = 110, 48
SETTINGS_TITLE = 'Herdr Sidebar Customizer settings with synthetic projects'
COLORS = {'black': '#171923', 'red': '#e06c75', 'green': '#98c379',
          'brown': '#e5c07b', 'blue': '#61afef', 'magenta': '#c678dd',
          'cyan': '#56b6c2', 'white': '#d7dae0', 'brightblack': '#7f8490',
          'brightwhite': '#ffffff'}
CELL_WIDTH, CELL_HEIGHT = 9, 18
DOWN = b'\x1bOB'


def svg(screen, title=SETTINGS_TITLE, light=False):
    """Render a pyte screen as an SVG image with an accessible title."""
    bg, fg = ('#f7f8fa', '#242936') if light else ('#1c1e2a', '#d7dae0')

    def color(value, default):
        if value == 'default':
            return default
        if value in COLORS:
            return COLORS[value]
        return '#' + value if len(value) == 6 else fg

    width = screen.columns * CELL_WIDTH + 22
    height = screen.lines * CELL_HEIGHT + 14
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">',
             f'<title>{escape(title)}</title>',
             f'<rect width="{width}" height="{height}" rx="12" fill="{bg}"/>',
             '<g font-family="DejaVu Sans Mono,Menlo,monospace" font-size="14">']
    for y in range(screen.lines):
        for x in range(screen.columns):
            cell = screen.buffer[y][x]
            front, back = color(cell.fg, fg), color(cell.bg, bg)
            if cell.reverse:
                front, back = back, front
            px, py = 11 + x * CELL_WIDTH, 22 + y * CELL_HEIGHT
            if back != bg:
                parts.append(f'<rect x="{px}" y="{py - 14}" width="{CELL_WIDTH}" '
                             f'height="{CELL_HEIGHT}" fill="{back}"/>')
            if cell.data.strip():
                parts.append(svg_text(cell, px, py, front))
    return '\n'.join(parts + ['</g></svg>']) + '\n'


def svg_text(cell, x, y, fill):
    style = ' font-weight="bold"' if cell.bold else ''
    # Symbols are missing from most monospace fonts.
    if any(ord(char) > 127 for char in cell.data):
        style += ' font-family="DejaVu Sans,Noto Sans Symbols 2,monospace"'
    return f'<text x="{x}" y="{y}" fill="{fill}"{style}>{escape(cell.data)}</text>'


def set_size(master, rows, columns):
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))


class Terminal:
    """A pyte screen fed from a PTY master."""

    def __init__(self, master, screen):
        self.master = master
        self.screen = screen
        self.stream = pyte.Stream(screen)
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def drain(self, seconds, on_data=None):
        """Feed output for the given time; stop early when the PTY closes."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if not select.select([self.master], [], [], .03)[0]:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            self.stream.feed(self.decoder.decode(data))
            if on_data:
                on_data(data)

    def resize(self, rows, columns, process):
        set_size(self.master, rows, columns)
        self.screen.resize(rows, columns)
        process.send_signal(signal.SIGWINCH)
        self.drain(.2)

    def send(self, keys, seconds):
        os.write(self.master, keys)
        self.drain(seconds)


def save_gif(terminal, path):
    import io
    import cairosvg
    from PIL import Image
    frames = []
    for _ in range(30):
        terminal.drain(.1)
        frames.append(svg(terminal.screen))
    images = [Image.open(io.BytesIO(cairosvg.svg2png(bytestring=frame.encode()))).convert('RGB')
              for frame in frames]
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=100, loop=0, optimize=True)


def main():
    master, slave = pty.openpty()
    set_size(master, ROWS, COLUMNS)
    proc = subprocess.Popen([sys.executable, str(ROOT / 'tools/demo.py')],
                            stdin=slave, stdout=slave, stderr=slave,
                            env=dict(os.environ, TERM='xterm-256color'))
    os.close(slave)
    terminal = Terminal(master, pyte.Screen(COLUMNS, ROWS))
    try:
        terminal.drain(.5)
        # Animation page, with the animation strip selected.
        terminal.resize(34, COLUMNS, proc)
        terminal.send(b'4' + DOWN, .3)
        (ROOT / 'assets/animations.svg').write_text(svg(terminal.screen))
        if '--gif' in sys.argv:
            save_gif(terminal, ROOT / 'assets/animations.gif')
        (ROOT / 'assets/animations-light.svg').write_text(svg(terminal.screen, light=True))
        # Colors page, with the selected space's palette focused.
        terminal.resize(ROWS, COLUMNS, proc)
        terminal.send(b'5' + DOWN + DOWN + b'\n', .3)
        (ROOT / 'assets/colours.svg').write_text(svg(terminal.screen))
        terminal.send(b'\x1b', .1)
        terminal.send(b'q', .2)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
        os.close(master)


if __name__ == '__main__':
    main()
