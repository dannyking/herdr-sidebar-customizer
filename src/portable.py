"""The few operating-system differences the worker needs, behind one interface.

Linux and macOS use `fcntl` locks and Herdr's Unix socket. Windows uses `msvcrt`
byte-range locks and Herdr's named pipe, `\\\\.\\pipe\\` plus HERDR_SOCKET_PATH,
which speaks the same newline-delimited JSON (the file at that path is only a
liveness marker).
"""
import os
import subprocess
import threading
import time

WINDOWS = os.name == 'nt'
# Win32 ERROR_FILE_NOT_FOUND and ERROR_PIPE_BUSY: no server yet, or every pipe
# instance is serving another client; Herdr opens a new instance shortly.
PIPE_RETRY = {2, 231}
# Win32 THREAD_TERMINATE, the access right CancelSynchronousIo requires.
THREAD_TERMINATE = 0x0001
MAX_RESPONSE = 8 * 1024 * 1024


def try_lock(handle):
    """Take an exclusive lock without waiting; False when another process holds it."""
    if WINDOWS:
        import msvcrt
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def lock(handle):
    """Wait for an exclusive lock, released when the handle closes."""
    if WINDOWS:
        while not try_lock(handle):
            time.sleep(0.05)
        return
    import fcntl
    fcntl.flock(handle, fcntl.LOCK_EX)


def replace(source, target, attempts=20):
    """os.replace, retried briefly: Windows refuses while a reader has the target open."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if not WINDOWS or attempt == attempts - 1:
                raise
            time.sleep(0.025)


def request(endpoint, payload, timeout=2):
    """Send one request line to Herdr and return its response line (b'' if it closed first)."""
    if WINDOWS:
        return pipe_request(endpoint, payload, timeout)
    return socket_request(endpoint, payload, timeout)


def socket_request(endpoint, payload, timeout):
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(endpoint)
        client.sendall(payload)
        with client.makefile('rb') as handle:
            return handle.readline(MAX_RESPONSE)


def open_pipe(endpoint, deadline):
    while True:
        try:
            return open('\\\\.\\pipe\\' + endpoint, 'r+b', buffering=0)
        except OSError as error:
            if getattr(error, 'winerror', None) not in PIPE_RETRY or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def pipe_request(endpoint, payload, timeout):
    """The named-pipe exchange, run on a daemon thread because pipe reads have no timeout.

    The thread owns the pipe and closes it when it finishes. On timeout the
    caller cancels the thread's blocked read and returns at once: closing the
    pipe from here instead would wait on the read, which may never finish.
    If the cancel misses, the thread and its handle linger until Herdr answers
    or the process exits, but the caller is never held up.
    """
    deadline = time.monotonic() + timeout
    pipe = open_pipe(endpoint, deadline)
    result = {}

    def exchange():
        try:
            pipe.write(payload)
            result['line'] = read_line(pipe)
        except OSError as error:
            result['error'] = error
        finally:
            pipe.close()

    worker = threading.Thread(target=exchange, daemon=True)
    worker.start()
    worker.join(max(0.1, deadline - time.monotonic()))
    if worker.is_alive():
        cancel_blocking_io(worker)
        raise TimeoutError('Herdr did not answer in time')
    if 'error' in result:
        raise result['error']
    return result['line']


def read_line(pipe):
    """Bytes up to and including the first newline, like a socket file's readline."""
    line = b''
    while b'\n' not in line and len(line) < MAX_RESPONSE:
        chunk = pipe.read(65536)
        if not chunk:
            break
        line += chunk
    first, newline, _ = line.partition(b'\n')
    return (first + newline)[:MAX_RESPONSE]


def cancel_blocking_io(thread):
    """Abort the thread's pending synchronous pipe I/O, if any; best effort."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenThread(THREAD_TERMINATE, False, thread.native_id)
    if handle:
        kernel32.CancelSynchronousIo(handle)
        kernel32.CloseHandle(handle)


def spawn(argv):
    """Start the worker so it outlives the hook that started it."""
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, close_fds=True)
    if not WINDOWS:
        return subprocess.Popen(argv, start_new_session=True, **quiet)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # Leave the hook's job object if Herdr allows it, so ending the hook cannot
    # end the worker; a job that forbids breakaway refuses the flag outright.
    try:
        return subprocess.Popen(argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **quiet)
    except OSError:
        return subprocess.Popen(argv, creationflags=flags, **quiet)
