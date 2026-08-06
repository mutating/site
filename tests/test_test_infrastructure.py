"""Regression coverage for the parallel pytest runner itself."""

from __future__ import annotations

import pytest


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
