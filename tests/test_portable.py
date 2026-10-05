"""Operating-system branches of portable.py; Windows paths run against fakes."""
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import portable


class UnixTests(unittest.TestCase):
    @unittest.skipIf(portable.WINDOWS, 'Unix locks')
    def test_try_lock_excludes_a_second_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.lock'
            with path.open('a') as first, path.open('a') as second:
                self.assertTrue(portable.try_lock(first))
                self.assertFalse(portable.try_lock(second))
            with path.open('a') as again:
                self.assertTrue(portable.try_lock(again))


class FakeMsvcrt(types.ModuleType):
    LK_NBLCK = 2

    def __init__(self):
        super().__init__('msvcrt')
        self.held = set()

    def locking(self, fd, mode, length):
        key = os.fstat(fd).st_ino
        if key in self.held:
            raise OSError(36, 'locked')
        self.held.add(key)


class PipeFile:
    """A named-pipe stand-in: the reply arrives in chunks, or never."""

    def __init__(self, chunks, hang=False):
        self.chunks, self.hang, self.written, self.closed = list(chunks), hang, b'', False
        self.release = threading.Event()

    def write(self, data):
        self.written += data

    def read(self, size):
        if self.hang:
            self.release.wait(5)
            raise OSError('closed')
        return self.chunks.pop(0) if self.chunks else b''

    def close(self):
        self.closed = True


def busy():
    error = OSError('pipe busy')
    error.winerror = 231
    return error


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(portable, 'WINDOWS', True))

    def test_msvcrt_lock_is_exclusive_and_waits(self):
        fake = FakeMsvcrt()
        with patch.dict(sys.modules, {'msvcrt': fake}), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.lock'
            with path.open('a') as first, path.open('a') as second:
                self.assertTrue(portable.try_lock(first))
                self.assertEqual(first.tell(), 0)
                self.assertFalse(portable.try_lock(second))
                releaser = threading.Timer(0.1, fake.held.clear)
                releaser.start()
                portable.lock(second)  # Returns once the holder lets go.

    def test_pipe_request_retries_busy_and_joins_chunks(self):
        pipe = PipeFile([b'{"result":', b'{}}\n{"extra":1}\n'])
        attempts = iter([busy(), busy(), pipe])
        def fake_open(name, mode, buffering):
            self.assertEqual(name, '\\\\.\\pipe\\C:\\Users\\x\\herdr.sock')
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return value
        with patch('builtins.open', fake_open):
            line = portable.request('C:\\Users\\x\\herdr.sock', b'{"id":1}\n')
        self.assertEqual(line, b'{"result":{}}\n')
        self.assertEqual(pipe.written, b'{"id":1}\n')
        self.assertTrue(pipe.closed)

    def test_pipe_timeout_cancels_the_read_and_the_thread_closes_the_pipe(self):
        pipe = PipeFile([], hang=True)
        cancelled = []
        def cancel(thread):
            cancelled.append(thread)
            pipe.release.set()  # What CancelSynchronousIo does to the blocked ReadFile.
        with patch('builtins.open', lambda *a, **k: pipe), patch.object(portable, 'cancel_blocking_io', cancel):
            with self.assertRaises(TimeoutError):
                portable.request('p', b'x\n', timeout=0.2)
        self.assertEqual(len(cancelled), 1)
        cancelled[0].join(1)
        self.assertTrue(pipe.closed)

    def test_pipe_timeout_returns_even_when_the_read_cannot_be_cancelled(self):
        pipe = PipeFile([], hang=True)
        started = time.monotonic()
        with patch('builtins.open', lambda *a, **k: pipe), \
                patch.object(portable, 'cancel_blocking_io', lambda thread: None):
            with self.assertRaises(TimeoutError):
                portable.request('p', b'x\n', timeout=0.2)
        self.assertLess(time.monotonic() - started, 1)
        # The caller never closes a pipe another thread is still reading.
        self.assertFalse(pipe.closed)
        pipe.release.set()

    def test_pipe_errors_raise_and_early_close_reads_as_empty(self):
        denied = OSError('denied')
        denied.winerror = 5
        with patch('builtins.open', side_effect=denied), self.assertRaises(OSError):
            portable.request('p', b'x\n')
        with patch('builtins.open', lambda *a, **k: PipeFile([])):
            self.assertEqual(portable.request('p', b'x\n'), b'')

    def test_replace_retries_while_a_reader_holds_the_target(self):
        calls = []
        def flaky(source, target):
            calls.append(source)
            if len(calls) < 3:
                raise PermissionError('in use')
        with patch.object(os, 'replace', flaky):
            portable.replace('a', 'b')
        self.assertEqual(len(calls), 3)

    def test_spawn_falls_back_when_the_job_forbids_breakaway(self):
        flags = []
        def popen(argv, creationflags=0, **kwargs):
            flags.append(creationflags)
            if creationflags & 0x01000000:
                raise OSError('access denied')
            return 'process'
        constants = dict(DETACHED_PROCESS=0x8, CREATE_NEW_PROCESS_GROUP=0x200,
                         CREATE_BREAKAWAY_FROM_JOB=0x01000000)
        with patch.multiple(portable.subprocess, create=True, Popen=popen, **constants):
            self.assertEqual(portable.spawn(['python', 'agent_info.py', 'watch']), 'process')
        self.assertEqual(flags, [0x01000208, 0x208])


class WindowsPathTests(unittest.TestCase):
    def test_defaults_match_herdrs_windows_directories(self):
        import runtime
        env = {'APPDATA': '/w/Roaming', 'LOCALAPPDATA': '/w/Local'}
        with patch.object(portable, 'WINDOWS', True), patch.dict(os.environ, env):
            for key in ('HERDR_CONFIG_PATH', 'HERDR_PLUGIN_CONFIG_DIR', 'HERDR_PLUGIN_STATE_DIR'):
                os.environ.pop(key, None)
            self.assertEqual(runtime.config_path(), Path('/w/Roaming/herdr/config.toml').resolve())
            self.assertEqual(runtime.preferences_path(),
                             Path('/w/Roaming/herdr/plugins/config/herdr-sidebar-customizer/settings.json'))
            self.assertEqual(runtime.state_root(), Path('/w/Local/herdr/plugins/herdr-sidebar-customizer'))
            # Herdr's own variables still win.
            with patch.dict(os.environ, {'HERDR_PLUGIN_STATE_DIR': '/elsewhere'}):
                self.assertEqual(runtime.state_root(), Path('/elsewhere'))


if __name__ == '__main__':
    unittest.main()
