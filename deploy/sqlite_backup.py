"""Back up or restore the deployment database while both services are stopped."""

import argparse
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from throng_api.config import Settings


def check_space(database):
    # A SQLite backup copies pages, including committed WAL pages. Leave room for
    # service writes and completion metadata rather than filling the filesystem.
    size = 0
    if database.exists():
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
            size = (
                source.execute("PRAGMA page_count").fetchone()[0]
                * source.execute("PRAGMA page_size").fetchone()[0]
            )
    required = size + max(64 * 1024 * 1024, size // 10)
    available = shutil.disk_usage(database.parent).free
    if available < required:
        raise RuntimeError(
            f"Not enough free space for SQLite backup: need {required} bytes, available {available} bytes"
        )


def mark_complete(directory, file, kind):
    stat = file.stat()
    (directory / ".complete.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": kind,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
        + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "check-space"])
    parser.add_argument("name")
    parser.add_argument("--env-file", type=Path, required=True)
    arguments = parser.parse_args()
    name = arguments.name
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}", name):
        raise ValueError("Invalid backup name")
    url = make_url(Settings(_env_file=arguments.env_file).database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("Deployment backups require a SQLite file")
    database = Path(url.database).resolve()
    directory = database.parent / "backups" / name
    backup = directory / "throng.db"
    action = arguments.action
    if action == "check-space":
        check_space(database)
    elif action == "backup":
        check_space(database)
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        try:
            if not database.exists():
                (directory / "absent").touch()
                mark_complete(directory, directory / "absent", "absent")
                return
            partial = directory / "throng.db.incomplete"
            with (
                closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(partial)) as target,
            ):
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("SQLite backup integrity check failed")
            partial.replace(backup)
            mark_complete(directory, backup, "sqlite")
        except BaseException:
            # Only this invocation's newly created directory is disposable.
            shutil.rmtree(directory)
            raise
    elif action == "restore":
        # The caller has stopped all writers. Remove stale WAL/SHM with the file.
        if not backup.is_file() and not (directory / "absent").is_file():
            raise FileNotFoundError("No completed backup")
        for candidate in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            candidate.unlink(missing_ok=True)
        if backup.is_file():
            shutil.copyfile(backup, database)
    else:
        raise ValueError("Expected backup or restore")


if __name__ == "__main__":
    main()
