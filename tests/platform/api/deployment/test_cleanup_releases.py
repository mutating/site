"""Production cleanup is opt-in, scoped and cannot race release activation."""

import fcntl
import importlib.util
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "cleanup_releases", ROOT / "infra/playbooks/files/cleanup_releases.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def server(tmp_path):
    """Create old, active and recent releases beside an unrelated hosted blog."""
    root = tmp_path / "mutating"
    for name, age in [
        ("old-a", 10),
        ("old-b", 9),
        ("active", 8),
        ("recent-a", 3),
        ("recent-b", 2),
        ("recent-c", 1),
    ]:
        release = root / "releases" / name
        for document in ("platform/web/dist/index.html", "site/public/index.html"):
            path = release / document
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        timestamp = time.time() - age * 86400
        os.utime(release, (timestamp, timestamp))
    (root / "current").symlink_to(root / "releases/active")
    blog = tmp_path / "pomponchik.org/post.html"
    blog.parent.mkdir()
    blog.write_text("keep the blog")
    return root, blog


def test_cleanup_dry_run_and_apply_preserve_the_current_release_and_blog(server):
    """Planning changes nothing, and applying removes only the selected history."""
    root, blog = server
    plan = module.cleanup(root, ["old-a"])
    assert not plan["applied"]
    assert (root / "releases/old-a").is_dir()
    applied = module.cleanup(root, ["old-a"], apply=True)
    assert applied["applied"]
    assert not (root / "releases/old-a").exists()
    assert (root / "current/site/public/index.html").read_text() == "active"
    assert blog.read_text() == "keep the blog"


@pytest.mark.parametrize(
    "name", ["active", "recent-a", "recent-b", "recent-c", "../pomponchik.org"]
)
def test_invalid_selection_is_fully_rejected_before_any_deletion(server, name):
    """Reject the complete selection before deleting even an eligible first item."""
    root, blog = server
    with pytest.raises(ValueError):
        module.cleanup(root, ["old-a", name], apply=True)
    assert (root / "releases/old-a").is_dir()
    assert blog.read_text() == "keep the blog"


def test_cleanup_refuses_symlinks_to_other_sites(server):
    """A symlink cannot turn release cleanup into deletion of another site."""
    root, blog = server
    (root / "releases/blog-link").symlink_to(blog.parent)
    with pytest.raises(ValueError, match="symlink"):
        module.cleanup(root, ["blog-link"], apply=True)
    assert blog.read_text() == "keep the blog"


def test_cleanup_cannot_run_during_activation(server):
    """The deployment lock prevents removing a release while activation selects it."""
    root, _ = server
    with (root / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            module.cleanup(root, ["old-a"], apply=True)
    assert (root / "releases/old-a").is_dir()


def test_cleanup_refuses_a_release_with_a_live_activation_pid(server):
    """Preserve an old release even if its activation process is unexpectedly alive."""
    root, _ = server
    release = root / "releases/old-a"
    (release / ".activation").mkdir()
    (release / ".activation/pid").write_text(str(os.getpid()))
    timestamp = time.time() - 10 * 86400
    os.utime(release, (timestamp, timestamp))
    with pytest.raises(ValueError, match="still present"):
        module.cleanup(root, ["old-a"], apply=True)
    assert release.is_dir()
