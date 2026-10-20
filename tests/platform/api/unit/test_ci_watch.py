"""The queue watchdog distinguishes admission, execution and confirmed GitHub outcomes."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from throng_api import ci

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def reservation(status, reason=None, *, expiry=1, deadline=1, started_at=None):
    """Describe only the facts the read-only watch decision is allowed to use."""
    return SimpleNamespace(
        status=status,
        started_at=started_at,
        reason=reason,
        expires_at=NOW + timedelta(seconds=expiry),
        deadline=NOW + timedelta(seconds=deadline),
    )


@pytest.mark.parametrize(
    "status,reason,expected",
    [
        ("preparing", None, "waiting"),
        ("ready", None, "waiting"),
        ("selected", None, "waiting"),
        ("running", None, "started"),
        ("completed", "runner_exited", "waiting"),
        ("completed", "github_success", "started"),
        ("completed", "github_failure", "started"),
        ("cancelled", "github_cancelled", "started"),
        ("failed", "github_unavailable", "failed"),
        ("completed", "github_outcome_unavailable", "failed"),
        ("cancelled", "bootstrap_cancelled", "failed"),
        ("timed_out", "runner_timeout", "failed"),
    ],
)
def test_watch_decision_requires_start_or_authoritative_outcome(status, reason, expected):
    """An exited runner is provisional; only real start or GitHub completion settles the guard."""
    result = ci.watch_decision(reservation(status, reason), NOW)
    assert result == (
        {"status": expected, "reason": reason} if expected == "failed" else {"status": expected}
    )


@pytest.mark.parametrize("status", ["preparing", "ready", "selected", "running", "completed"])
@pytest.mark.parametrize("expiry", [-1, 0])
def test_watch_rejects_expiration_including_the_exact_boundary(status, expiry):
    """A waiting or running lease is unusable at equality, including provisional runner exit."""
    row = reservation(status, "runner_exited" if status == "completed" else None, expiry=expiry)
    assert ci.watch_decision(row, NOW) == {"status": "failed", "reason": "runner_timeout"}


@pytest.mark.parametrize("status", ["preparing", "ready"])
def test_watch_rejects_elapsed_preparation_before_local_tick(status):
    """A future execution lease cannot hide an expired preparation deadline."""
    assert ci.watch_decision(reservation(status, deadline=0), NOW) == {
        "status": "failed",
        "reason": "preparation_timeout",
    }


@pytest.mark.parametrize(
    "status,reason", [("completed", "github_success"), ("cancelled", "github_cancelled")]
)
def test_watch_preserves_confirmed_historical_outcome_after_lease_expiry(status, reason):
    """A late-starting watchdog must not turn an already completed job into a new failure."""
    assert ci.watch_decision(reservation(status, reason, expiry=-1), NOW) == {"status": "started"}


def test_watch_missing_reservation_never_admits_work():
    """Watching an absent instance reports failure rather than reserving or guessing a runner."""
    assert ci.watch_decision(None, NOW) == {"status": "failed", "reason": "reservation_unavailable"}


@pytest.mark.parametrize(
    "status,reason",
    [
        ("completed", "runner_exited"),
        ("completed", "github_outcome_unavailable"),
        ("failed", "isolate_unavailable"),
        ("timed_out", "runner_timeout"),
        ("cancelled", "authorization_revoked"),
    ],
)
@pytest.mark.parametrize("expiry", [-1, 0, 1])
def test_watch_remembers_a_verified_start_after_later_lifecycle_changes(status, reason, expiry):
    """Past dispatch is historical evidence, independent of later execution leases or failures."""
    row = reservation(status, reason, expiry=expiry, started_at=NOW - timedelta(seconds=10))
    assert ci.watch_decision(row, NOW) == {"status": "started"}
