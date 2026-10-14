"""Regression coverage for the parallel pytest runner itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import PROJECT_ROOT, copy_controlled_test_content


def test_controlled_test_content_excludes_published_articles(
    tmp_path: Path,
) -> None:
    """Keep future editorial additions out of browser-test ordering."""

    published_blog = PROJECT_ROOT / "content" / "blog"
    published_entries = {
        entry.name
        for entry in published_blog.iterdir()
        if not entry.name.startswith("_index.")
    }
    target = tmp_path / "content"
    copy_controlled_test_content(target)

    controlled_entries = {
        entry.name
        for entry in (target / "blog").iterdir()
        if not entry.name.startswith("_index.")
    }
    assert published_entries.isdisjoint(controlled_entries)


def test_every_test_has_a_bounded_runtime(pytestconfig: pytest.Config) -> None:
    """Turn a stalled browser promise into a reported failure before CI ends."""

    assert float(pytestconfig.getini("timeout")) == 120
    assert pytestconfig.getini("timeout_method") == "thread"


def test_parallel_temp_root_keeps_its_cleanup_lock(
    pytestconfig: pytest.Config,
) -> None:
    """Prevent concurrent pytest cleanup from deleting an active temp root.

    This suite runs with ``-n auto`` and stores every worker below one numbered
    pytest temp root. In pytest 8.4, the ``none`` retention policy translates to
    ``keep=0`` and deliberately creates that shared root without a cleanup lock.
    A concurrent pytest controller can then treat that unlocked root as stale,
    rename/delete it, and make active workers lose ``popen-gw*``: Hugo serves
    incomplete pages and unrelated DOM assertions fail together. Keeping failed
    artifacts with a positive retention count gives the shared root the cleanup
    lock required to protect it from other pytest controllers. Reverting the
    policy to ``none`` or the count to zero must fail this configuration-level
    regression before another long stability run becomes nondeterministic.
    """

    assert pytestconfig.getini("tmp_path_retention_policy") == "failed"
    assert int(pytestconfig.getini("tmp_path_retention_count")) >= 1
