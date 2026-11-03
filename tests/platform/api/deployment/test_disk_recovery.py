"""Disk-pressure failures must preserve the live database and existing backups."""

import importlib.util
import os
import sqlite3
import sys
from collections import namedtuple
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def backup(tmp_path, monkeypatch):
    """Load the real backup CLI against a database containing evidence to preserve."""
    spec = importlib.util.spec_from_file_location(
        "deployment_backup", ROOT / "deploy/sqlite_backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "throng.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('keep production data')")
    env_file = tmp_path / "server.env"
    env_file.write_text(f"THRONG_DATABASE_URL=sqlite+aiosqlite:///{database}\n")
    for name in list(os.environ):
        if name.startswith("THRONG_"):
            monkeypatch.delenv(name)

    def run(action, name="release"):
        monkeypatch.setattr(
            sys, "argv", ["sqlite_backup.py", action, name, "--env-file", str(env_file)]
        )
        module.main()

    return module, database, run


def test_insufficient_backup_space_is_detected_before_creating_files(backup, monkeypatch):
    """A full filesystem must leave the database and backup directory untouched."""
    module, database, run = backup
    before = database.read_bytes()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage(1024, 1024, 0))
    for action in ("check-space", "backup"):
        with pytest.raises(RuntimeError, match="Not enough free space"):
            run(action)
    assert database.read_bytes() == before
    assert not (database.parent / "backups").exists()


def test_failed_backup_removes_only_its_incomplete_output(backup, monkeypatch):
    """A partial copy must not consume space or remove an earlier completed backup."""
    module, database, run = backup
    run("backup", "previous")
    previous = database.parent / "backups/previous/throng.db"
    saved = previous.read_bytes()
    source = database.read_bytes()
    connect = sqlite3.connect

    class FullDiskSource(sqlite3.Connection):
        def backup(self, target, **options):
            target.execute("CREATE TABLE partial (value TEXT)")
            target.commit()
            raise sqlite3.OperationalError("database or disk is full")

    def constrained(path, *args, **kwargs):
        if kwargs.get("uri"):
            kwargs["factory"] = FullDiskSource
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", constrained)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        run("backup")
    assert not (database.parent / "backups/release").exists()
    assert previous.read_bytes() == saved
    assert database.read_bytes() == source


def test_incomplete_backup_cannot_replace_the_live_database(backup):
    """Refuse restoration before touching production when the copy never completed."""
    _, database, run = backup
    source = database.read_bytes()
    incomplete = database.parent / "backups/release"
    incomplete.mkdir(parents=True)
    (incomplete / "throng.db.incomplete").write_bytes(b"partial")
    with pytest.raises(FileNotFoundError, match="No completed backup"):
        run("restore")
    assert database.read_bytes() == source


@pytest.mark.parametrize("absent", [False, True])
def test_completed_backup_metadata_is_accepted_without_rescanning(backup, monkeypatch, absent):
    """The backup writer and rotation reader must agree for real and initially absent databases."""
    _, database, run = backup
    if absent:
        database.unlink()
    run("backup")
    spec = importlib.util.spec_from_file_location(
        "rotation_backup_contract", ROOT / "deploy/rotate_deployments.py"
    )
    rotation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rotation)

    def no_rescan(*_args, **_kwargs):
        raise AssertionError("A completed new backup should not require another SQLite scan")

    monkeypatch.setattr(rotation.sqlite3, "connect", no_rescan)
    metadata, state = rotation._complete_backup(database.parent / "backups/release")
    assert state == "cached"
    assert metadata["kind"] == ("absent" if absent else "sqlite")


def test_completion_marker_write_failure_removes_the_new_backup_only(backup, monkeypatch):
    """Disk exhaustion during metadata publication must not leave a misleading completed copy."""
    module, database, run = backup
    run("backup", "previous")
    previous = database.parent / "backups/previous/throng.db"
    saved = previous.read_bytes()
    source = database.read_bytes()

    def full_disk(*_args):
        raise OSError("No space left on device")

    monkeypatch.setattr(module, "mark_complete", full_disk)
    with pytest.raises(OSError, match="No space left"):
        run("backup")
    assert not (database.parent / "backups/release").exists()
    assert previous.read_bytes() == saved
    assert database.read_bytes() == source
