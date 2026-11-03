"""Exercise automatic release and SQLite retention against real temporary history."""

import fcntl
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("rotation", ROOT / "deploy/rotate_deployments.py")
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)


def _release(root, name, number, status="0", age_hours=48):
    path = root / "releases" / name
    for document in ("platform/web/dist/index.html", "site/public/index.html"):
        file = path / document
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(name)
    if status is not None:
        activation = path / ".activation"
        activation.mkdir()
        (activation / "status").write_text(status)
        os.utime(activation / "status", (number, number))
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


def _backup(root, release, number, *, partial=False, age_hours=48):
    path = root / f"{release}-20261014T{number:06d}"
    path.mkdir()
    if partial:
        (path / "throng.db.incomplete").write_bytes(b"unfinished")
    else:
        with sqlite3.connect(path / "throng.db") as connection:
            connection.execute("CREATE TABLE evidence (value TEXT)")
            connection.execute("INSERT INTO evidence VALUES (?)", (release,))
        connection.close()
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Build eight successful versions, snapshots, a live database and an unrelated blog."""
    root = tmp_path / "mutating"
    backups = tmp_path / "throng/backups"
    backups.mkdir(parents=True)
    (backups.parent / "throng.db").write_bytes(b"the live database is not deployment history")
    blog = tmp_path / "pomponchik.org/post.html"
    blog.parent.mkdir()
    blog.write_text("preserve the blog")
    for number in range(1, 9):
        # All directory mtimes are recent; successful versions must still be bounded by count.
        _release(root, f"r{number}", number, age_hours=number / 24)
        _backup(backups, f"r{number}", number)
    (root / "current").symlink_to(root / "releases/r8")
    monkeypatch.setattr(rotation, "_healthy", lambda: None)
    monkeypatch.setattr(rotation, "_active_paths", lambda *_: [set(), set()])
    return root, backups, blog


def test_rotation_preview_is_read_only_and_uses_activation_order(history):
    """A plan keeps current plus three successful versions without writing validation markers."""
    root, backups, blog = history
    report = rotation.rotate(root, backups, "r8")
    assert report["kept_releases"] == ["r5", "r6", "r7", "r8"]
    assert report["removed_releases"] == ["r1", "r2", "r3", "r4"]
    assert [name.split("-")[0] for name in report["kept_backups"]] == ["r4", "r5", "r6", "r7", "r8"]
    assert len(list((root / "releases").iterdir())) == 8
    assert len(list(backups.iterdir())) == 8
    assert not list(backups.glob("*/.complete.json"))
    assert blog.read_text() == "preserve the blog"


def test_rotation_bounds_history_preserves_data_and_is_idempotent(history, monkeypatch):
    """Delete only old history, cache validation and avoid rescanning unchanged SQLite copies."""
    root, backups, blog = history
    report = rotation.rotate(root, backups, "r8", apply=True)
    assert report["changed"]
    assert sorted(path.name for path in (root / "releases").iterdir()) == ["r5", "r6", "r7", "r8"]
    assert len(list(backups.iterdir())) == 5
    assert len(list(backups.glob("*/.complete.json"))) == 5
    assert (
        backups.parent / "throng.db"
    ).read_bytes() == b"the live database is not deployment history"
    assert blog.read_text() == "preserve the blog"
    with sqlite3.connect(backups / "r8-20261014T000008/throng.db") as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone() == ("r8",)

    def no_rescan(*_args, **_kwargs):
        raise AssertionError("Unchanged snapshots should use completion metadata")

    monkeypatch.setattr(rotation.sqlite3, "connect", no_rescan)
    repeated = rotation.rotate(root, backups, "r8", apply=True)
    assert not repeated["changed"]
    assert repeated["removed_releases"] == repeated["removed_backups"] == []


@pytest.mark.parametrize("expected", ["r7", "../r8", "", "r8\n"])
def test_rotation_refuses_an_unexpected_or_invalid_current_release(history, expected):
    """Stale CI runs and untrusted release IDs cannot remove newer deployment history."""
    root, backups, _ = history
    with pytest.raises(ValueError):
        rotation.rotate(root, backups, expected, apply=True)
    assert len(list((root / "releases").iterdir())) == 8
    assert len(list(backups.iterdir())) == 8


def test_rotation_refuses_a_current_release_that_has_not_completed(history):
    """Only a successfully activated current version may trigger cleanup."""
    root, backups, _ = history
    (root / "current/.activation/status").write_text("1")
    with pytest.raises(ValueError, match="successfully activated"):
        rotation.rotate(root, backups, "r8", apply=True)
    assert len(list(backups.iterdir())) == 8


def test_health_failure_preserves_all_history(history, monkeypatch):
    """A regression detected after activation must leave every rollback copy available."""
    root, backups, _ = history

    def unhealthy():
        raise RuntimeError("API is unavailable")

    monkeypatch.setattr(rotation, "_healthy", unhealthy)
    with pytest.raises(RuntimeError, match="API is unavailable"):
        rotation.rotate(root, backups, "r8", apply=True)
    assert len(list((root / "releases").iterdir())) == 8
    assert not list(backups.glob("*/.complete.json"))


def test_activation_lock_prevents_concurrent_rotation(history):
    """Cleanup cannot race a release switch or a database backup."""
    root, backups, _ = history
    with (root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            rotation.rotate(root, backups, "r8", apply=True)
    assert len(list(backups.iterdir())) == 8


def test_live_and_recent_work_is_preserved_but_stale_work_is_removed(history, monkeypatch):
    """Protect running workers and recent preparations, while reclaiming abandoned uploads."""
    root, backups, _ = history
    pending = _release(root, "pending", 10, status=None, age_hours=1)
    failed = _release(root, "failed", 11, status="17", age_hours=48)
    uploading = root / "releases" / ("a" * 40 + "-42-1.uploading")
    uploading.mkdir()
    os.utime(uploading, (1, 1))
    live_backup = "r1-20261014T000001"
    monkeypatch.setattr(rotation, "_active_paths", lambda *_: [{"r1"}, {live_backup}])
    report = rotation.rotate(root, backups, "r8", apply=True)
    assert pending.exists()
    assert (root / "releases/r1").exists()
    assert (backups / live_backup).exists()
    assert not failed.exists()
    assert not uploading.exists()
    assert report["skipped"][str(pending)] == "recent_unfinished_release"


def test_symlinks_and_unrecognized_files_cannot_target_the_blog(history):
    """Ignore links and foreign data instead of treating them as disposable history."""
    root, backups, blog = history
    (root / "releases/blog-link").symlink_to(blog.parent)
    (backups / "blog-20261014T000009").symlink_to(blog.parent)
    foreign = _backup(backups, "foreign", 10)
    (foreign / "private-notes.txt").write_text("not deployment data")
    unknown = root / "releases/unrelated"
    (unknown / ".activation").mkdir(parents=True)
    (unknown / ".activation/status").write_text("0")
    report = rotation.rotate(root, backups, "r8", apply=True)
    assert report["kept_releases"] == ["r5", "r6", "r7", "r8"]
    assert unknown.exists() and foreign.exists()
    assert blog.read_text() == "preserve the blog"


@pytest.mark.parametrize("recent", [True, False])
@pytest.mark.parametrize("sidecar", [None, "throng.db-journal", "throng.db-wal", "throng.db-shm"])
def test_incomplete_backup_does_not_count_toward_five_usable_copies(history, recent, sidecar):
    """Keep five real snapshots even when a newer failed copy occupies an apparent slot."""
    root, backups, _ = history
    partial = _backup(backups, "broken", 9, partial=sidecar is None)
    if sidecar:
        (partial / sidecar).write_bytes(b"unfinished transaction")
    stamp = time.time() - (1 if recent else 48) * 3600
    os.utime(partial, (stamp, stamp))
    report = rotation.rotate(root, backups, "r8", apply=True)
    assert len(report["kept_backups"]) == 5
    assert partial.exists() is recent


def test_process_reference_protection_supports_the_longest_valid_backup_id(tmp_path, monkeypatch):
    """A timestamp suffix must not make a live backup invisible to process protection."""
    release = "a" * 128
    backup = release + "-20261014T000001"
    releases = tmp_path / "releases"
    backups = tmp_path / "backups"
    monkeypatch.setattr(rotation, "Path", lambda _: tmp_path / "no-proc")
    monkeypatch.setattr(
        rotation.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (
            f"python {releases / release}/worker {backups / backup}/throng.db"
        ),
    )
    assert rotation._active_paths(releases, backups) == [{release}, {backup}]


def test_corrupt_current_backup_aborts_before_any_deletion(history):
    """Never prune earlier recovery points without a verified copy for the current deployment."""
    root, backups, _ = history
    (backups / "r8-20261014T000008/throng.db").write_bytes(b"not a SQLite database")
    with pytest.raises(RuntimeError, match="No verified backup"):
        rotation.rotate(root, backups, "r8", apply=True)
    assert len(list((root / "releases").iterdir())) == 8
    assert len(list(backups.iterdir())) == 8


def test_fewer_than_five_snapshots_are_all_preserved(history):
    """The retention count is a lower bound on protected copies, never a reason to delete the last ones."""
    root, backups, _ = history
    for number in range(1, 5):
        shutil.rmtree(backups / f"r{number}-20261014T{number:06d}")
    report = rotation.rotate(root, backups, "r8", apply=True)
    assert report["removed_backups"] == []
    assert len(list(backups.iterdir())) == 4


def test_changed_completion_metadata_cannot_hide_a_corrupted_snapshot(history):
    """A changed file must be revalidated even if an earlier completion marker remains."""
    root, backups, _ = history
    rotation.rotate(root, backups, "r8", apply=True)
    current = backups / "r8-20261014T000008/throng.db"
    current.write_bytes(b"x" * current.stat().st_size)
    with pytest.raises(RuntimeError, match="No verified backup"):
        rotation.rotate(root, backups, "r8", apply=True)
    assert len(list(backups.iterdir())) == 5


def test_a_directory_swapped_for_a_symlink_cannot_overwrite_the_blog(history, monkeypatch):
    """Descriptor-relative writes must refuse a directory replaced after planning."""
    root, backups, blog = history
    original = rotation._write_marker
    swapped = False

    def swap(parent, name, metadata):
        nonlocal swapped
        if not swapped:
            swapped = True
            path = backups / name
            path.rename(backups / "moved-snapshot")
            path.symlink_to(blog.parent)
        return original(parent, name, metadata)

    monkeypatch.setattr(rotation, "_write_marker", swap)
    with pytest.raises(OSError):
        rotation.rotate(root, backups, "r8", apply=True)
    assert blog.read_text() == "preserve the blog"
    assert not (blog.parent / ".complete.json").exists()
    assert len(list((root / "releases").iterdir())) == 8


@pytest.mark.parametrize("phase,apply", [("rotate", True), ("rotate-plan", False)])
def test_privileged_wrapper_delegates_only_the_fixed_rotation_policy(tmp_path, phase, apply):
    """CI's existing sudo permission must not execute code from a writable release as root."""
    helper = tmp_path / "helper.py"
    helper.write_text("import json, sys; print(json.dumps(sys.argv[1:]))")
    wrapper = tmp_path / "activate.sh"
    wrapper.write_text(
        (ROOT / "deploy/activate.sh")
        .read_text()
        .replace(
            "/usr/bin/python3 /usr/local/lib/mutating/rotate_deployments.py",
            f"'{sys.executable}' '{helper}'",
        )
    )
    result = subprocess.run(
        ["bash", str(wrapper), "reviewed-release", phase],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [
        "--expected-current",
        "reviewed-release",
        *(["--apply"] if apply else []),
    ]
    rejected = subprocess.run(
        ["bash", str(wrapper), "reviewed-release", phase, "--root=/var/www"],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
