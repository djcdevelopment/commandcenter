"""Bounded OS advisory append lock, shared by independent kernel writers."""
from contextlib import contextmanager
import time


@contextmanager
def append_lock(path, timeout_s=30):
    # Same primitives as ExecutionLedger; no SQLite projection dependency.
    try:
        import msvcrt
        def acquire(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        def release(handle):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except ImportError:
        import fcntl
        def acquire(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        def release(handle):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        while True:
            try:
                acquire(handle)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("kernel append lock timed out") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            release(handle)
