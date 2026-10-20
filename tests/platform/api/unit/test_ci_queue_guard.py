"""The hosted queue guard observes one reservation and lets GitHub cancel its matrix sibling."""

import urllib.error
import urllib.parse
from http.client import BadStatusLine, IncompleteRead
from types import SimpleNamespace

import pytest
import yaml

from tests.platform.api.unit.test_ci_bootstrap import bootstrap as bootstrap
from throng_api import ci_bootstrap, ci_workflow
from throng_api.ci_workflow import render


@pytest.fixture
def watch(bootstrap, monkeypatch):
    """Run guard mode against a deterministic clock and GitHub's normal output-file environment."""
    now, calls, sleeps = [0.0], [], []
    monkeypatch.setenv("POWERCELL_MODE", "watch")
    monkeypatch.setenv("POWERCELL_INSTANCE", "matrix-one")
    clock_reads = 0

    def monotonic():
        nonlocal clock_reads
        clock_reads += 1
        assert clock_reads <= 2000, "Polling must wait instead of spinning on an unchanged clock"
        return float(now[0])

    monkeypatch.setattr(ci_bootstrap.time, "monotonic", monotonic)

    def sleep(seconds):
        assert 0 < seconds <= 2
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(ci_bootstrap.time, "sleep", sleep)
    return SimpleNamespace(now=now, calls=calls, sleeps=sleeps, output=bootstrap)


# Assertions: an observer uses only watch, preserves the exact instance, waits two
# seconds between decisions, and exits on confirmed start without publishing or cancelling.
def test_guard_observes_start_without_reserving_or_publishing(watch, monkeypatch):
    """Observing an existing selection cannot allocate another runner or issue a cancellation."""
    monkeypatch.delenv("GITHUB_OUTPUT")  # Observation does not require step outputs.

    def peer(url, **kwargs):
        watch.calls.append((url, kwargs))
        assert len(watch.calls) <= 3
        if ".actions.githubusercontent.com/" in url:
            return {"value": "private-oidc"}
        assert kwargs["body"] == {"instance": "matrix-one"}
        return {"status": "waiting" if watch.now[0] == 0 else "started"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert [urllib.parse.urlsplit(url).path.rsplit("/", 1)[1] for url, _ in watch.calls] == [
        "token",
        "watch",
        "watch",
    ]
    assert watch.sleeps == [2] and not watch.output.exists()


# Assertions: only explicit waiting/start states are accepted; a local failure,
# malformed JSON shape or a legacy ready selection fails the guard without other writes.
@pytest.mark.parametrize(
    "decision",
    [
        {"status": "failed", "reason": "runner_timeout"},
        [],
        {"status": "unknown"},
        {"status": "ready", "runs_on": ["powercell-foreign"]},
    ],
)
def test_guard_rejects_failed_and_malformed_decisions(watch, monkeypatch, decision):
    """An invalid or failed observation must fail the matrix guard rather than select a runner."""

    def peer(url, **kwargs):
        watch.calls.append(url)
        return {"value": "private-oidc"} if len(watch.calls) == 1 else decision

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert len(watch.calls) == 2 and watch.calls[-1].endswith("/watch")
    assert watch.sleeps == [] and not watch.output.exists()


# Assertions: transient identity/watch transport errors get a bounded retry;
# the request credential never reaches the platform and provider text stays private.
@pytest.mark.parametrize("stage", ["identity", "watch"])
@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("https://peer", 429, "private-provider", {}, None),
        urllib.error.HTTPError("https://peer", 503, "private-provider", {}, None),
        urllib.error.URLError("private-provider"),
        TimeoutError("private-provider"),
        IncompleteRead(b"private-provider"),
        BadStatusLine("private-provider"),
    ],
)
def test_guard_recovers_a_transient_identity_or_api_failure(
    watch, monkeypatch, capsys, stage, failure
):
    """Retry a temporary upstream failure inside the same guard deadline, with redacted diagnostics."""
    failed = False

    def peer(url, **kwargs):
        nonlocal failed
        watch.calls.append((url, kwargs))
        identity = ".actions.githubusercontent.com/" in url
        if (stage == "identity") == identity and not failed:
            failed = True
            raise failure
        if identity:
            assert kwargs["token"] == "fake-request-token"
            return {"value": "private-oidc"}
        assert url.endswith("/watch") and kwargs["token"] == "private-oidc"
        return {"status": "started"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert failed and watch.sleeps == [2] and not watch.output.exists()
    assert "private-" not in capsys.readouterr().out


# Assertions: authentication, redirects and other non-transient HTTP errors are
# not retried; invalid JSON also fails immediately without reserve/cancel/output.
@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("https://peer", status, "private-provider", {}, None)
        for status in (302, 400, 401, 403, 404, 409)
    ]
    + [ValueError("private-json")],
)
@pytest.mark.parametrize("stage", ["identity", "watch"])
def test_guard_does_not_retry_permanent_or_invalid_responses(watch, monkeypatch, failure, stage):
    """A permanent refusal must terminate the guard without concealing it as a temporary outage."""

    def peer(url, **kwargs):
        watch.calls.append(url)
        if len(watch.calls) == 1 and stage != "identity":
            return {"value": "private-oidc"}
        raise failure

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError) as error:
        ci_bootstrap.main()
    assert len(watch.calls) == (1 if stage == "identity" else 2)
    if stage == "identity":
        assert ".actions.githubusercontent.com/" in watch.calls[-1]
    else:
        assert watch.calls[-1].endswith("/watch")
    assert watch.sleeps == [] and not watch.output.exists()
    assert "private-" not in str(error.value)


# Assertions: a persistent outage cannot reset the 300+10 second guard budget;
# refresh continues every 120 seconds, polling never spins and cleanup has no side effects.
@pytest.mark.parametrize("outage", [False, True])
def test_guard_deadline_and_oidc_refresh_are_independent_of_preparation(watch, monkeypatch, outage):
    """Bound waiting by the selection lease budget, including an alternating API outage."""
    identities, tokens = [], []

    def peer(url, **kwargs):
        watch.calls.append(url)
        if ".actions.githubusercontent.com/" in url:
            identities.append("private-" + str(len(identities) + 1))
            return {"value": identities[-1]}
        assert url.endswith("/watch")
        assert len(tokens) < 155
        tokens.append(kwargs["token"])
        if outage and len(tokens) % 2:
            raise urllib.error.URLError("private-outage")
        return {"status": "waiting"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert watch.now[0] == 310 and len(watch.sleeps) == len(tokens) == 155
    assert identities == ["private-1", "private-2", "private-3"]
    assert tokens[:60] == ["private-1"] * 60
    assert tokens[60:120] == ["private-2"] * 60
    assert tokens[120:] == ["private-3"] * 35
    assert not watch.output.exists() and not any(url.endswith("/cancel") for url in watch.calls)


# Assertions: GitHub's attempt-scoped native fail-fast owns queue cancellation;
# the hosted leg contains only observation, every CI step has a workload gate,
# each role preserves its own timeout, and the shared source appears only once.
def test_generated_guard_is_a_separate_matrix_role_without_hosted_ci():
    """Keep CI on Power Cells while GitHub cancels an unserviceable queued matrix sibling."""
    route = SimpleNamespace(
        id="01234567-89ab-cdef-0123-456789abcdef",
        ref="refs/heads/main",
        workflow_path=".github/workflows/ci.yml",
        job_key="test",
        preparation_seconds=300,
        timeout_seconds=121,
    )
    source = render(route, "https://api.example")
    document = yaml.safe_load(source)
    target = document["jobs"]["test"]
    router = document["jobs"][target["needs"]]
    assert target["strategy"]["fail-fast"] is True
    assert target["strategy"]["max-parallel"] == 2 and target["continue-on-error"] is False
    roles = {item["role"]: item for item in target["strategy"]["matrix"]["include"]}
    assert set(roles) == {"workload", "queue_guard"}
    assert (
        roles["workload"]["timeout_minutes"] == 3 and roles["queue_guard"]["timeout_minutes"] == 6
    )
    assert "run_attempt == github.run_attempt" in roles["workload"]["runs_on"]
    assert roles["queue_guard"]["runs_on"] == '["ubuntu-24.04-arm"]'
    assert target["runs-on"] == "${{ fromJSON(matrix.runs_on) }}"
    assert target["timeout-minutes"] == "${{ matrix.timeout_minutes }}"
    assert "matrix.role" in target["name"] and "queue guard" in target["name"]
    assert target["permissions"] == {"contents": "read", "id-token": "write"}
    guard, *workload = target["steps"]
    assert guard["if"] == "matrix.role == 'queue_guard'" and guard["shell"] == "python"
    assert guard["working-directory"] == "${{ runner.temp }}"
    assert guard["env"]["POWERCELL_MODE"] == "watch"
    assert guard["run"] == router["steps"][0]["run"]
    assert workload and all(step["if"] == "matrix.role == 'workload'" for step in workload)
    assert source.count("class NoRedirect(") == 1
    assert "actions: write" not in source and "force-cancel" not in source


# Assertions: issuer/platform 429/503 delay the next request by Retry-After after
# the response arrived; malformed values use two seconds and long waits clip to the deadline.
@pytest.mark.parametrize("stage", ["identity", "watch"])
@pytest.mark.parametrize(
    "header,delay",
    [
        ("120", 120),
        ("invalid", 2),
        ("-1", 2),
        ("nan", 2),
        ("inf", 2),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 2),
    ],
)
def test_guard_respects_retry_after_without_leaking_response(
    watch, monkeypatch, stage, header, delay
):
    """Provider cooldowns apply after the failure, and retry closes its HTTP response."""
    import io

    failure_body = io.BytesIO(b"private-provider-body")
    response_error = urllib.error.HTTPError(
        "https://peer", 503, "private-provider", {"Retry-After": header}, failure_body
    )
    failed, attempts = False, []

    def sleep(seconds):
        watch.sleeps.append(seconds)
        watch.now[0] += seconds

    def peer(url, **kwargs):
        nonlocal failed
        identity = ".actions.githubusercontent.com/" in url
        if (stage == "identity") == identity:
            attempts.append(watch.now[0])
            if not failed:
                failed = True
                watch.now[0] += 7  # The response latency must not consume Retry-After.
                raise response_error
        return {"value": "private-oidc"} if identity else {"status": "started"}

    monkeypatch.setattr(ci_bootstrap.time, "sleep", sleep)
    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert attempts == [0, 7 + delay] and watch.sleeps == [delay]
    assert failure_body.closed and not watch.output.exists()


# Assertions: a cooldown longer than the remaining guard budget performs no
# follow-up request and never sleeps past the fixed deadline.
@pytest.mark.parametrize("header", ["120", "999999999999999999999999999999999", "9" * 500])
def test_guard_clips_retry_after_to_the_remaining_deadline(watch, monkeypatch, header):
    """A long provider cooldown cannot reset the guard lease budget or overrun it."""
    calls = []

    def sleep(seconds):
        watch.sleeps.append(seconds)
        watch.now[0] += seconds

    def peer(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return {"value": "private-oidc"}
        watch.now[0] = 309
        raise urllib.error.HTTPError(url, 429, "private-provider", {"Retry-After": header}, None)

    monkeypatch.setattr(ci_bootstrap.time, "sleep", sleep)
    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert watch.now[0] == 310 and watch.sleeps == [1] and len(calls) == 2


# Assertions: proactively renewing identity must not suppress observation with
# an already-issued token; issuer backoff is separate from platform polling.
@pytest.mark.parametrize("status", [429, 503])
def test_guard_keeps_observing_during_a_transient_oidc_refresh_outage(watch, monkeypatch, status):
    """A valid cached identity still observes job start when the issuer temporarily fails."""
    identities, observed = [], []

    def peer(url, **kwargs):
        if ".actions.githubusercontent.com/" in url:
            identities.append(watch.now[0])
            if len(identities) == 1:
                return {"value": "private-valid-identity"}
            raise urllib.error.HTTPError(
                url, status, "private-issuer", {"Retry-After": "120"}, None
            )
        assert url.endswith("/watch") and kwargs["token"] == "private-valid-identity"
        observed.append(watch.now[0])
        assert len(observed) <= 62  # A busy loop is a failing assertion, not a hung mutation.
        return {"status": "started" if watch.now[0] >= 122 else "waiting"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert identities == [0, 120] and observed[-2:] == [120, 122]
    assert watch.now[0] == 122 and not watch.output.exists()


# Assertions: falling back to a cached identity is allowed only for temporary
# renewal errors; malformed responses and a confirmed refusal terminate at once.
@pytest.mark.parametrize(
    "failure",
    [
        ValueError("private-json"),
        urllib.error.HTTPError("https://peer", 403, "private-refused", {}, None),
    ],
)
def test_guard_rejects_permanent_oidc_refresh_failures(watch, monkeypatch, failure):
    """Do not use an old token to hide a permanent identity renewal refusal."""
    identities, observed = [], []

    def peer(url, **kwargs):
        if ".actions.githubusercontent.com/" in url:
            identities.append(watch.now[0])
            if len(identities) == 1:
                return {"value": "private-identity"}
            raise failure
        observed.append(watch.now[0])
        return {"status": "waiting"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError) as error:
        ci_bootstrap.main()
    assert identities == [0, 120] and observed[-1] == 118 and watch.now[0] == 120
    assert "private-" not in str(error.value) and not watch.output.exists()


# Assertions: the delivered observer arms one absolute POSIX timer before any
# I/O, raises an ordinary failure at expiry, and leaves routing mode unchanged.
@pytest.mark.parametrize("observer", [False, True])
def test_delivered_guard_arms_a_process_deadline_without_changing_routing(
    bootstrap, monkeypatch, observer
):
    """An ordinary exit failure, rather than GitHub's cancelled job timeout, triggers fail-fast."""
    import io
    import json
    import signal

    armed, handlers, requests = [], [], []
    monkeypatch.setenv("POWERCELL_MODE", "watch" if observer else "route")
    monkeypatch.setattr(signal, "alarm", lambda seconds: armed.append(seconds))
    monkeypatch.setattr(
        signal, "signal", lambda number, handler: handlers.append((number, handler))
    )

    class Peer:
        """Keep the generated entrypoint intact while replacing its external HTTPS boundary."""

        def open(self, request, timeout):
            requests.append(request.full_url)
            if len(requests) == 1:
                assert armed == ([310] if observer else [])
                body = {"value": "private-identity"}
            else:
                body = (
                    {"status": "started"}
                    if observer
                    else {"status": "ready", "runs_on": ["powercell-ready"]}
                )
            return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(ci_bootstrap.urllib.request, "build_opener", lambda *args: Peer())
    # The embedded program is the packaged file verbatim. Preserve its source
    # attribution so coverage includes the delivered entrypoint and signal handler.
    exec(compile(ci_workflow.SOURCE, ci_bootstrap.__file__, "exec"), {"__name__": "__main__"})
    assert len(requests) == 2 and armed == ([310] if observer else [])
    if observer:
        assert len(handlers) == 1 and handlers[0][0] == signal.SIGALRM
        with pytest.raises(RuntimeError, match="preparation timed out"):
            handlers[0][1](signal.SIGALRM, None)
        assert not bootstrap.exists()
    else:
        assert handlers == [] and bootstrap.exists()
