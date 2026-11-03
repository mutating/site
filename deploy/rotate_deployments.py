"""Rotate deployment history after a verified deployment, under its activation lock."""

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
BACKUP_ID = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})-(\d{8}T\d{6})")
SQLITE_SIDECARS = {"throng.db-journal", "throng.db-wal", "throng.db-shm"}
BACKUP_FILES = {"throng.db", "absent", "throng.db.incomplete", ".complete.json"} | SQLITE_SIDECARS


def _healthy():
    for url in ("http://127.0.0.1:8000/health/ready", "http://127.0.0.1:8001/login"):
        with urllib.request.urlopen(url, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("Current API and dashboard must be healthy before rotation")


def _active_paths(releases, backups):
    """Collect referenced directories without exposing process arguments in reports."""
    proc = Path("/proc")
    if proc.is_dir():
        commands = []
        for path in proc.glob("[0-9]*/cmdline"):
            try:
                commands.append(path.read_bytes().decode(errors="replace"))
            except (OSError, ProcessLookupError):
                continue
    else:
        commands = [subprocess.check_output(["ps", "-axo", "command="], text=True)]
    result = []
    for root in (releases, backups):
        pattern = re.compile(
            re.escape(str(root)) + r"/([A-Za-z0-9][A-Za-z0-9._-]{0,143})(?=[/\s\x00]|$)"
        )
        result.append({name for command in commands for name in pattern.findall(command)})
    return result


def _success_time(release):
    activation = release / ".activation"
    status = activation / "status"
    if activation.is_symlink() or status.is_symlink():
        return None
    try:
        return status.stat().st_mtime_ns if status.read_text().strip() == "0" else None
    except (OSError, UnicodeError):
        return None


def _is_release(path):
    return (
        bool(RELEASE_ID.fullmatch(path.name))
        and (path / "platform/web/dist/index.html").is_file()
        and (path / "site/public/index.html").is_file()
    )


def _complete_backup(directory):
    """Trust matching completion metadata, or validate a legacy snapshot once."""
    if any(
        path.is_symlink() or not path.is_file() or path.name not in BACKUP_FILES
        for path in directory.iterdir()
    ):
        return None, "unknown_files"
    database = directory / "throng.db"
    absent = directory / "absent"
    partial = directory / "throng.db.incomplete"
    if (
        partial.exists()
        or any((directory / name).exists() for name in SQLITE_SIDECARS)
        or database.exists() == absent.exists()
    ):
        return None, "incomplete"
    file = database if database.exists() else absent
    stat = file.stat()
    metadata = {
        "version": 1,
        "kind": "sqlite" if database.exists() else "absent",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    marker = directory / ".complete.json"
    try:
        if json.loads(marker.read_text()) == metadata:
            return metadata, "cached"
    except (OSError, ValueError, UnicodeError):
        pass
    if database.exists():
        try:
            # Backups are closed snapshots. Immutable mode also avoids creating WAL/SHM files.
            with closing(
                sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
            ) as connection:
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    return None, "invalid"
        except sqlite3.DatabaseError:
            return None, "invalid"
    elif stat.st_size != 0:
        return None, "invalid"
    return metadata, "validated"


def _write_marker(parent, name, metadata):
    directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    temporary = f".complete.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory
        )
        with os.fdopen(descriptor, "w") as output:
            output.write(json.dumps(metadata) + "\n")
        os.replace(temporary, ".complete.json", src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def rotate(root, backups, expected, *, apply=False, previous=3, backup_keep=5, stale_hours=24):
    if not RELEASE_ID.fullmatch(expected) or previous < 3 or backup_keep < 5 or stale_hours < 1:
        raise ValueError(
            "Require a valid release, three previous versions, five backups and a one-hour grace period"
        )
    root = root.resolve(strict=True)
    releases = root / "releases"
    if releases.is_symlink() or backups.is_symlink():
        raise ValueError("Deployment and backup roots must not be symlinks")
    releases = releases.resolve(strict=True)
    backups = backups.resolve(strict=True)
    with (root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = (root / "current").resolve(strict=True)
        if (
            current.parent != releases
            or current.name != expected
            or not _is_release(current)
            or _success_time(current) is None
        ):
            raise ValueError(
                "Rotation requires the expected successfully activated current release"
            )
        _healthy()
        active_releases, active_backups = _active_paths(releases, backups)
        entries = [path for path in releases.iterdir() if path.is_dir() and not path.is_symlink()]
        successful = [
            (stamp, path.name, path)
            for path in entries
            if path != current and _is_release(path) and (stamp := _success_time(path)) is not None
        ]
        keep_releases = {
            current,
            *(item[2] for item in sorted(successful, reverse=True)[:previous]),
        }
        cutoff = time.time() - stale_hours * 3600
        remove_releases = []
        skipped = {}
        for path in entries:
            if path in keep_releases:
                continue
            if path.name in active_releases:
                skipped[str(path)] = "live_process"
                continue
            staging = bool(re.fullmatch(r"[a-f0-9]{40}-[0-9]+-[0-9]+\.uploading", path.name))
            complete = _is_release(path)
            if not staging and not complete:
                skipped[str(path)] = "unknown_release"
                continue
            if _success_time(path) is None and path.stat().st_mtime > cutoff:
                skipped[str(path)] = "recent_unfinished_release"
                continue
            remove_releases.append(path)

        snapshots = []
        for path in backups.iterdir():
            match = BACKUP_ID.fullmatch(path.name)
            if path.is_symlink() or not path.is_dir() or not match:
                continue
            try:
                datetime.strptime(match[2], "%Y%m%dT%H%M%S")
            except ValueError:
                continue
            snapshots.append((match[2], path.name, path, match[1]))
        keep_backups = set()
        validated = {}
        invalid = set()
        current_backup = None
        # Stop scanning once five usable copies and the current release's copy are found.
        for _, _, path, release_id in sorted(snapshots, reverse=True):
            if path.name in active_backups:
                skipped[str(path)] = "live_process"
                continue
            metadata, state = _complete_backup(path)
            if metadata is None:
                invalid.add(path)
                skipped[str(path)] = state
                continue
            if len(keep_backups) < backup_keep:
                keep_backups.add(path)
            if release_id == expected and current_backup is None:
                current_backup = path
                keep_backups.add(path)
            if state == "validated":
                validated[path] = metadata
            if len(keep_backups) >= backup_keep and current_backup is not None:
                break
        if current_backup is None:
            raise RuntimeError("No verified backup of the current deployment; nothing was removed")
        remove_backups = []
        if len(keep_backups) >= backup_keep:
            for _, _, path, _ in snapshots:
                if path in keep_backups or path.name in active_backups:
                    continue
                if any(
                    child.is_symlink() or not child.is_file() or child.name not in BACKUP_FILES
                    for child in path.iterdir()
                ):
                    skipped[str(path)] = "unknown_files"
                    continue
                incomplete = (
                    path in invalid
                    or (path / "throng.db.incomplete").exists()
                    or any((path / name).exists() for name in SQLITE_SIDECARS)
                    or not (path / ".complete.json").is_file()
                    or not ((path / "throng.db").exists() or (path / "absent").exists())
                )
                if incomplete and path.stat().st_mtime > cutoff:
                    skipped[str(path)] = "recent_incomplete_backup"
                    continue
                remove_backups.append(path)
                skipped.pop(str(path), None)
        before = shutil.disk_usage(root).free
        # Revalidate every path before performing any deletion, under the same lock.
        for path, parent in [(path, releases) for path in remove_releases] + [
            (path, backups) for path in remove_backups
        ]:
            if path.is_symlink() or path.resolve(strict=True).parent != parent:
                raise ValueError("Rotation path changed during planning")
        if apply:
            release_directory = os.open(releases, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                backup_directory = os.open(backups, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for path, metadata in validated.items():
                        if path in keep_backups:
                            _write_marker(backup_directory, path.name, metadata)
                    for path in remove_releases:
                        shutil.rmtree(path.name, dir_fd=release_directory)
                    for path in remove_backups:
                        shutil.rmtree(path.name, dir_fd=backup_directory)
                finally:
                    os.close(backup_directory)
            finally:
                os.close(release_directory)
        return {
            "applied": apply,
            "kept_releases": sorted(path.name for path in keep_releases),
            "kept_backups": sorted(path.name for path in keep_backups),
            "removed_releases": sorted(path.name for path in remove_releases),
            "removed_backups": sorted(path.name for path in remove_backups),
            "skipped": skipped,
            "free_before": before,
            "free_after": shutil.disk_usage(root).free,
            "changed": apply
            and bool(remove_releases or remove_backups or keep_backups.intersection(validated)),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/mutating"))
    parser.add_argument("--backups", type=Path, default=Path("/var/lib/throng/backups"))
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(rotate(args.root, args.backups, args.expected_current, apply=args.apply)))


if __name__ == "__main__":
    main()
