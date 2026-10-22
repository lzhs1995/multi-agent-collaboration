"""有界、只读的本地证据读取；特殊文件与并发替换不算稳定证据。"""
import os
from pathlib import Path
import stat
import secrets

MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_ATTEMPTS = 128


def regular_lstat(path, *, limit=MAX_EVIDENCE_BYTES):
    """Observe the named file itself; never follow links or open special files."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('evidence path must be absolute')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('evidence is not a bounded regular file')
    return info


def publish_archive(path, raw):
    """Publish fully synced bytes once in an existing private directory.

    Callers validate archive ownership/content after publication. A concurrent
    final name is never replaced. A directory-sync error may leave the complete
    final file present; retain and inspect that same archive before any retry.
    """
    path = Path(path)
    if not path.is_absolute() or not isinstance(raw, bytes) or len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError('absolute path and bounded archive bytes required')
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = None
    try:
        info = os.fstat(folder)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('archive directory is not private and owned')
        name = '.archive-' + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=folder)
        temporary = name
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.parent.lstat()
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('archive directory changed before publication')
        try:
            os.link(name, path.name, src_dir_fd=folder, dst_dir_fd=folder,
                    follow_symlinks=False)
        except FileExistsError:
            pass  # Validate the original final name; never overwrite it.
        finally:
            os.unlink(name, dir_fd=folder)
            temporary = None
        os.fsync(folder)
        regular_lstat(path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=folder)
            except FileNotFoundError:
                pass
        os.close(folder)


def open_regular_lock(path):
    """非阻塞打开锁文件；FIFO、链接和替换不能卡死发送/核收。"""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('lock path must be absolute')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        held, current = os.fstat(fd), path.lstat()
        if (not stat.S_ISREG(held.st_mode) or held.st_nlink != 1
                or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError('lock is not the original single-link regular file')
        return os.fdopen(fd, 'a+b')
    except BaseException:
        os.close(fd)
        raise


def snapshot(path, limit=MAX_EVIDENCE_BYTES):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('evidence path must be absolute')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('evidence is not a bounded regular file')
        raw = stream.read(limit + 1)
        after, current = os.fstat(stream.fileno()), path.lstat()
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns)
    if (len(raw) > limit or not stat.S_ISREG(current.st_mode)
            or identity(before) != identity(after)
            or identity(before) != identity(current)):
        raise ValueError('evidence changed during the bounded read')
    return raw, identity(before)


def read_bytes(path, limit=MAX_EVIDENCE_BYTES):
    return snapshot(path, limit)[0]


def attempt_paths(journal):
    paths = []
    for path in Path(journal).glob('attempt-*.json'):
        paths.append(path)
        if len(paths) > MAX_ATTEMPTS:
            raise ValueError('attempt count exceeds bounded evidence budget')
    return sorted(paths)
