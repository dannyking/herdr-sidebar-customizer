"""Real curses initialization/input/resize in a PTY, including on macOS CI."""
import fcntl
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
import unittest

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 5
# Curses only redraws changed cells, so a page's field labels may arrive in
# fragments. Its tab label switches to reverse video and is always sent whole.
PAGE_TABS = [
    (b'2', b' 2 Agents '),
    (b'3', b' 3 Tabs '),
    (b'4', b' 4 Colors '),
    (b'5', b' 5 Motion & symbols '),
    (b'6', b' 6 Shortcuts '),
    (b'1', b' 1 Spaces '),
]


class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.master, slave = pty.openpty()
        self.resize(42, 110)
        self.proc = subprocess.Popen([sys.executable, str(ROOT / 'tools/demo.py')],
                                     stdin=slave, stdout=slave, stderr=slave,
                                     env=dict(os.environ, TERM='xterm-256color'))
        os.close(slave)
        self.output = bytearray()

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=TIMEOUT)
        os.close(self.master)

    def resize(self, rows, columns):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))

    def read_available(self, wait):
        """Read whatever arrives within wait seconds; return False at end of output."""
        if not select.select([self.master], [], [], wait)[0]:
            return True
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return False
        self.output.extend(data)
        return bool(data)

    def wait_for(self, marker, start):
        """Read until marker appears after offset start, failing after TIMEOUT."""
        deadline = time.monotonic() + TIMEOUT
        while marker not in self.output[start:]:
            if time.monotonic() > deadline or not self.read_available(.05):
                tail = self.output.decode(errors='replace')[-2000:]
                self.fail(f'{marker!r} did not appear:\n{tail}')

    def send(self, data, marker):
        start = len(self.output)
        os.write(self.master, data)
        self.wait_for(marker, start)

    def resize_and_wait(self, rows, columns, marker):
        start = len(self.output)
        self.resize(rows, columns)
        self.proc.send_signal(signal.SIGWINCH)
        self.wait_for(marker, start)

    def wait_for_exit(self):
        deadline = time.monotonic() + TIMEOUT
        while self.proc.poll() is None and time.monotonic() < deadline:
            self.read_available(.05)
        return self.proc.wait(timeout=TIMEOUT)

    def test_settings_pages_resize_and_exit_in_real_terminal(self):
        self.wait_for(b'HERDR SIDEBAR CUSTOMIZER', 0)
        for key, marker in PAGE_TABS:
            with self.subTest(page=key):
                self.send(key, marker)
        self.resize_and_wait(20, 40, b'Enlarge this pane')
        self.resize_and_wait(42, 110, b'AGENTS')
        os.write(self.master, b'q')
        self.assertEqual(self.wait_for_exit(), 0, self.output.decode(errors='replace')[-2000:])
        self.assertNotIn(b'Traceback', self.output)


if __name__ == '__main__':
    unittest.main()
