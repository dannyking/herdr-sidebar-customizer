"""A small file's parsed contents, reread only when the file changes on disk."""


def file_stamp(path):
    """(inode, mtime, size): changes whenever the file is replaced or rewritten."""
    stat = path.stat()
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


class StampCache:
    """`parse(text)` of a UTF-8 file, recomputed only when its stamp changes.

    A missing or unreadable file gives `default` and is retried on the next
    load. Text that cannot be decoded or parsed (ValueError, or RecursionError
    for JSON nested too deeply) also gives `default`, kept until the file
    changes.
    """

    def __init__(self, path, parse, default):
        self.path, self.parse, self.default = path, parse, default
        self.stamp, self.value = None, default

    def load(self):
        try:
            stamp = file_stamp(self.path)
        except OSError:
            return self.default
        if stamp != self.stamp:
            try:
                value = self.parse(self.path.read_text(encoding='utf-8'))
            except OSError:
                return self.default
            except (ValueError, RecursionError):
                value = self.default
            self.stamp, self.value = stamp, value
        return self.value
