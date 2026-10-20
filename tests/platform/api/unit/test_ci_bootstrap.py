"""Generated bootstrap behavior with external HTTPS peers replaced at their boundary."""

import io
import urllib.error
import urllib.parse
from contextlib import closing

import pytest

from throng_api import ci_bootstrap


@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    """Configure a workflow invocation without real Actions credentials or network calls."""
    output = tmp_path / "output"
    environment = {
        "POWERCELL_API": "https://api.example",
        "POWERCELL_AUDIENCE": "https://api.example/v1/github/ci",
        "POWERCELL_ROUTE": "route",
        "POWERCELL_WAIT_SECONDS": "10",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token?api-version=1",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fake-request-token",
        "GITHUB_OUTPUT": str(output),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    return output


def test_ready_selection(bootstrap, monkeypatch):
    """A ready reservation publishes the exact output without another selection request."""
    decision = {"status": "ready", "runs_on": ["powercell-123"]}
    calls = []

    def peer(url, **kwargs):
        calls.append((url, kwargs))
        return {"value": "oidc-identity"} if len(calls) == 1 else decision

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert bootstrap.read_text() == 'runs_on=["powercell-123"]\n'
    assert [urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] for url, _ in calls] == [
        "token",
        "reserve",
    ]
    assert calls[1][1]["token"] == "oidc-identity"
    assert calls[1][1]["body"] == {"instance": "default"}
    assert "audience=https%3A%2F%2Fapi.example%2Fv1%2Fgithub%2Fci" in calls[0][0]


def test_preparing_poll_and_outage_failure(bootstrap, monkeypatch, capsys):
    """Preparation is polled; network failure exits without output or credentials in logs."""
    responses = iter(
        [
            {"value": "private-oidc"},
            {"status": "preparing"},
            urllib.error.URLError("private-error"),
            {"status": "failed", "reason": "bootstrap_cancelled"},
        ]
    )

    def peer(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert not bootstrap.exists()
    assert "private-" not in capsys.readouterr().out


def test_preparation_timeout_has_no_output(bootstrap, monkeypatch):
    """The wall-clock bound ends preparation without queuing another executor."""
    now = [0]
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        ci_bootstrap,
        "request_json",
        lambda *args, **kwargs: (
            (now.__setitem__(0, 21) or {"status": "preparing"})
            if "/reserve" in args[0]
            else {"value": "oidc"}
        ),
    )
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert not bootstrap.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://pipelines.actions.githubusercontent.com/token",
        "https://evil.example/token",
        "https://user:password@pipelines.actions.githubusercontent.com/token",
    ],
)
def test_identity_endpoint_is_fixed_to_github(bootstrap, monkeypatch, url):
    """Forged identity endpoint environment variables cannot receive a GitHub credential."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", url)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected credential transmission")

    monkeypatch.setattr(ci_bootstrap, "request_json", forbidden)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert not bootstrap.exists()


@pytest.mark.parametrize(
    "decision",
    [
        {"status": "failed", "reason": "cancelled"},
        {
            "status": "ready",
            "runs_on": ["self-hosted", "other-tenant"],
        },
        {"status": "ready", "runs_on": ["other-tenant"]},
        {"status": "ready", "runs_on": ["self-hosted", "powercell-extra"]},
    ],
)
def test_failed_or_foreign_runner_never_becomes_an_output(bootstrap, monkeypatch, decision):
    """A terminal reservation or foreign runner label fails routing explicitly."""
    responses = iter([{"value": "oidc"}, decision, decision])
    monkeypatch.setattr(ci_bootstrap, "request_json", lambda *args, **kwargs: next(responses))
    with pytest.raises((RuntimeError, ValueError)):
        ci_bootstrap.main()
    assert not bootstrap.exists()


def test_http_json_bound_and_redirect_refusal(monkeypatch):
    """HTTPS responses are bounded and a redirect cannot forward the Authorization header."""

    class Opener:
        def __init__(self, content):
            self.content = content

        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer private-token"
            return closing(io.BytesIO(self.content))

    monkeypatch.setattr(
        ci_bootstrap.urllib.request,
        "build_opener",
        lambda *args: Opener(b'{"ok":true}'),
    )
    assert ci_bootstrap.request_json("https://api.example", token="private-token", body={}) == {
        "ok": True
    }
    monkeypatch.setattr(
        ci_bootstrap.urllib.request,
        "build_opener",
        lambda *args: Opener(b"x" * (256 * 1024 + 1)),
    )
    with pytest.raises(ValueError, match="too large"):
        ci_bootstrap.request_json("https://api.example", token="private-token")
    request = ci_bootstrap.urllib.request.Request("https://api.example")
    with pytest.raises(urllib.error.HTTPError):
        ci_bootstrap.NoRedirect().redirect_request(
            request, None, 302, "redirect", {}, "https://evil.example"
        )


def test_embedded_script_entrypoint(bootstrap, monkeypatch):
    """The exact embedded Python source also works under GitHub's script entrypoint."""
    import runpy

    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "http://invalid.example/token")
    with pytest.raises(RuntimeError):
        runpy.run_path(ci_bootstrap.__file__, run_name="__main__")
    assert not bootstrap.exists()


@pytest.mark.parametrize("cancel_error", [False, True])
def test_lost_reservation_response_is_cancelled_before_failure(
    bootstrap, monkeypatch, cancel_error
):
    """A failed selection acknowledgement withdraws the runner and produces no output."""
    calls = []

    def peer(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return {"value": "oidc"}
        if urllib.parse.urlsplit(url).path.endswith("/reserve"):
            raise urllib.error.URLError("lost response")
        assert url.endswith("/cancel")
        if cancel_error:
            raise urllib.error.URLError("cancel unavailable")
        return {"status": "failed", "reason": "bootstrap_cancelled"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: pytest.fail("Reservation retry"))
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert calls[-1].endswith("/cancel")
    assert not bootstrap.exists()


# Assertions: malformed JSON shapes from identity/reserve behave as an
# outage, fail without output, cancel any reservation and leak no token.
@pytest.mark.parametrize(
    "stage,value",
    [
        ("identity", []),
        ("identity", {"value": None}),
        ("identity", {"value": ""}),
        ("reserve", []),
        ("reserve", {"status": "ready"}),
        ("reserve", {"status": "ready", "runs_on": 42}),
        ("reserve", {"status": "unknown"}),
        ("reserve", {"status": "ready", "runs_on": [None]}),
    ],
)
def test_malformed_response_fails_without_output(bootstrap, monkeypatch, capsys, stage, value):
    """Malformed identity and reservation responses fail without outputs."""
    calls = []

    def peer(url, **kwargs):
        current = "identity" if not calls else urllib.parse.urlsplit(url).path.rsplit("/", 1)[1]
        calls.append(current)
        if current == stage:
            return value
        if current == "identity":
            return {"value": "private-oidc"}
        return {"status": "ready", "runs_on": ["powercell-one"]}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert not bootstrap.exists()
    assert (calls[-1] == "cancel") == (stage != "identity")
    assert "private-oidc" not in capsys.readouterr().out


# Assertions: a five-minute cold start refreshes GitHub identity every two minutes;
# reserve uses the newest token, keep the original deadline and expose
# only the selected runner in outputs, with no credentials in logs.
def test_long_preparation_refreshes_oidc_before_reserving(bootstrap, monkeypatch, capsys):
    """Verify long preparation refreshes oidc before reserving."""
    monkeypatch.setenv("POWERCELL_WAIT_SECONDS", "300")
    now, identities, reserve_tokens = [0], [], []
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: now.__setitem__(0, now[0] + 60))

    def peer(url, *, token, **kwargs):
        if ".actions.githubusercontent.com/" in url:
            assert token == "fake-request-token"
            identities.append("private-" + str(len(identities) + 1))
            return {"value": identities[-1]}
        assert token == identities[-1]
        if urllib.parse.urlsplit(url).path.endswith("/reserve"):
            reserve_tokens.append(token)
            if now[0] < 240:
                return {"status": "preparing"}

        return {"status": "ready", "runs_on": ["powercell-cold"]}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert reserve_tokens == ["private-1", "private-1", "private-2", "private-2", "private-3"]
    assert now[0] == 240
    assert bootstrap.read_text() == 'runs_on=["powercell-cold"]\n'
    assert "private-" not in capsys.readouterr().out


# Assertions: malformed/unavailable refresh never sends the request credential to
# the platform or publishes a runner; it cancels with the last valid identity and
# fails without leaking the provider error.
@pytest.mark.parametrize("failure", [[], {"value": ""}, urllib.error.URLError("private-error")])
def test_failed_oidc_refresh_cancels_preparation(bootstrap, monkeypatch, capsys, failure):
    """Verify failed oidc refresh cancels preparation."""
    monkeypatch.setenv("POWERCELL_WAIT_SECONDS", "300")
    now, identities, platform_calls = [0], [], []
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: now.__setitem__(0, 120))

    def peer(url, *, token, **kwargs):
        if ".actions.githubusercontent.com/" in url:
            identities.append(url)
            if len(identities) == 1:
                return {"value": "private-identity"}
            if isinstance(failure, Exception):
                raise failure
            return failure
        assert token == "private-identity"
        action = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        if action == "reserve":
            assert not platform_calls, "A failed refresh must cancel instead of retrying reserve"
        platform_calls.append(action)
        return {"status": "preparing"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert len(identities) == 2 and platform_calls == ["reserve", "cancel"]
    assert not bootstrap.exists()
    assert "private-" not in capsys.readouterr().out


# Assertions: the workflow asks the platform to hold reserve for up to twenty
# seconds, gives HTTP five seconds of transport headroom, and publishes immediately
# when a held response selects a runner, without adding the old two-second sleep.
def test_held_reservation_has_no_client_sleep(bootstrap, monkeypatch):
    """Publish a held ready response without another request or delay."""
    monkeypatch.setenv("POWERCELL_WAIT_SECONDS", "60")
    now, calls = [0.0], []
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: pytest.fail("Extra client sleep"))

    def peer(url, **kwargs):
        path = urllib.parse.urlsplit(url).path
        calls.append(path)
        if path.endswith("/token"):
            return {"value": "oidc"}
        if path.endswith("/reserve"):
            assert urllib.parse.parse_qs(urllib.parse.urlsplit(url).query) == {
                "wait_seconds": ["20"]
            }
            assert kwargs["timeout"] == 25
            now[0] += 20
            if calls.count(path) == 1:
                return {"status": "preparing"}

        return {"status": "ready", "runs_on": ["powercell-held"]}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert calls[-1].endswith("/reserve") and len(calls) == 3 and now[0] == 40
    assert bootstrap.read_text() == 'runs_on=["powercell-held"]\n'


# Assertions: a legacy server that ignores wait cannot cause busy polling; the
# client backs off briefly, clips waits/HTTP timeouts to the remaining deadline,
# and cancels at the unchanged overall deadline without output.
def test_legacy_server_backoff_and_remaining_wait_are_bounded(bootstrap, monkeypatch):
    """Bound polling and transport time when a server does not hold the request."""
    now, sleeps, waits = [0.0], [], []
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(seconds)
        assert 0 < seconds <= 0.25
        now[0] += seconds

    def peer(url, **kwargs):
        parts = urllib.parse.urlsplit(url)
        if parts.path.endswith("/token"):
            return {"value": "oidc"}
        if parts.path.endswith("/reserve"):
            wait = float(urllib.parse.parse_qs(parts.query)["wait_seconds"][0])
            assert 0 < wait <= 20 - now[0]
            assert kwargs["timeout"] == wait + 5
            waits.append(wait)
        return {"status": "preparing"}

    monkeypatch.setattr(ci_bootstrap.time, "sleep", sleep)
    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises(RuntimeError):
        ci_bootstrap.main()
    assert waits[0] == 20 and waits[-1] == 0.25 and now[0] == 20
    assert len(sleeps) == 80
    assert not bootstrap.exists()
