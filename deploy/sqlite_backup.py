"""Back up or restore the deployment database while both services are stopped."""

import argparse
import re
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from throng_api.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
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
    if action == "backup":
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        if not database.exists():
            (directory / "absent").touch()
            return
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("SQLite backup integrity check failed")
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
