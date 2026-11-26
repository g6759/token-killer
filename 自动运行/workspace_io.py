"""File operations shared by local game runners."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_file(path, contents):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + "-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save(path, value):
    write_file(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("另一个进程正在操作这个批次。") from error
        yield


def check_disk(directory):
    targets = [directory, ROOT, Path(tempfile.gettempdir())]
    if os.name == "nt":
        targets.append(Path(os.environ["SystemRoot"]))
    for target in targets:
        while not target.exists() and target != target.parent:
            target = target.parent
        if shutil.disk_usage(target).free < 10 * 1024**3:
            raise ValueError(f"{target.anchor} 可用空间低于 10 GiB，已暂停。 / Free disk space below 10 GiB.")
