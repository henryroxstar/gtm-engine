import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class VisualsLockError(RuntimeError):
    pass


@contextmanager
def visuals_render_lock(content_root: Path | str, *, timeout_s: float = 180.0) -> Iterator[Path]:
    """Acquire exclusive advisory flock on content_root/.locks/visuals-render.lock."""
    import fcntl  # POSIX only

    content_root = Path(content_root)
    locks_dir = content_root / ".locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path = locks_dir / "visuals-render.lock"
    if lock_path.is_symlink():
        lock_path.unlink()
    fh = lock_path.open("a+")

    try:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    if time.monotonic() - start >= timeout_s:
                        raise VisualsLockError(
                            f"Timed out after {timeout_s:.1f}s waiting on visuals-render.lock"
                        ) from None
                    time.sleep(0.2)
                else:
                    raise

        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()

        try:
            yield lock_path
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        fh.close()
