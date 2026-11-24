"""Validate private, launcher-created storage separately from the app database."""

import os
from pathlib import Path
import stat
import tempfile


DEMO_PREFIX = "worksite-demo-"
MARKER = ".worksite-demo-workspace"
ACCOUNTS_FILE = "accounts.json"
DATABASE_FILE = "demo.sqlite3"


def _owned_private(info):
    # Windows process temp is per-user. On POSIX also verify ownership and mode.
    return os.name != "posix" or (info.st_uid == os.getuid() and not info.st_mode & 0o077)


def _marker_value(directory, database_stat):
    return f"WORKSITE Synthetic Data workspace v1\n{directory}\n{database_stat.st_dev}:{database_stat.st_ino}\n"


def private_demo_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not _owned_private(info):
        raise ValueError("Demo workspace contains an unsafe file")
    return path


def validate_demo_directory(path):
    directory = Path(path)
    if (
        not directory.is_absolute()
        or directory.parent.resolve() != Path(tempfile.gettempdir()).resolve()
        or not directory.name.startswith(DEMO_PREFIX)
    ):
        raise ValueError("Demo workspace must be a launcher-created directory in the user temp location")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or not _owned_private(info):
        raise ValueError("Demo workspace must be a private directory")
    directory = directory.resolve()
    marker = private_demo_file(directory / MARKER)
    database = private_demo_file(directory / DATABASE_FILE)
    if marker.read_text(encoding="utf-8") != _marker_value(directory, database.stat()):
        raise ValueError("Demo workspace marker does not match the directory and database")
    for name in (ACCOUNTS_FILE, f"{DATABASE_FILE}-journal", f"{DATABASE_FILE}-wal", f"{DATABASE_FILE}-shm"):
        path = directory / name
        if path.exists() or path.is_symlink():
            private_demo_file(path)
    for name in ("data", "tiles", ".env"):
        path = directory / name
        if path.exists() or path.is_symlink():
            raise ValueError("Demo workspace must be kept separate from runtime data or environment files")
    return directory


def create_demo_directory():
    directory = Path(tempfile.mkdtemp(prefix=DEMO_PREFIX)).resolve()
    database = directory / DATABASE_FILE
    with os.fdopen(os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"):
        pass  # SQLite initializes this empty file during migration.
    marker_path = directory / MARKER
    with os.fdopen(os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as marker:
        marker.write(_marker_value(directory, database.stat()))
    return validate_demo_directory(directory)
