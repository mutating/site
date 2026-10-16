"""Generated bootstrap behavior with external HTTPS peers replaced at their boundary."""

import io
import urllib.error
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
        "POWERCELL_FALLBACK": "ubuntu-24.04-arm",
        "POWERCELL_WAIT_SECONDS": "10",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token?api-version=1",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fake-request-token",
        "GITHUB_OUTPUT": str(output),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    return output


@pytest.mark.parametrize(
    "decision,expected",
    [
        (
            {
                "status": "ready",
                "backend": "powercell",
                "runs_on": ["self-hosted", "powercell-123"],
            },
            '["self-hosted","powercell-123"]',
        ),
        (
            {
                "status": "ready",
                "backend": "github",
                "runs_on": "ubuntu-24.04-arm",
                "reason": "no_capacity",
            },
            '"ubuntu-24.04-arm"',
        ),
    ],
)
def test_ready_selection(bootstrap, monkeypatch, decision, expected):
    """Ready local and hosted decisions produce valid JSON for fromJSON(runs-on)."""
    calls = []

    def peer(url, **kwargs):
        calls.append((url, kwargs))
        return {"value": "oidc-identity"} if len(calls) == 1 else decision

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert "runs_on=" + expected in bootstrap.read_text()
    assert calls[1][1]["token"] == "oidc-identity"
    assert calls[1][1]["body"] == {"instance": "default"}
    assert "audience=https%3A%2F%2Fapi.example%2Fv1%2Fgithub%2Fci" in calls[0][0]


def test_preparing_poll_and_outage_fallback(bootstrap, monkeypatch, capsys):
    """Preparation is polled; network failure chooses the configured fallback without credentials in logs."""
    responses = iter(
        [
            {"value": "private-oidc"},
            {"status": "preparing"},
            urllib.error.URLError("private-error"),
            {"status": "ready", "backend": "github", "runs_on": "ubuntu-24.04-arm"},
        ]
    )

    def peer(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: None)
    ci_bootstrap.main()
    assert 'runs_on="ubuntu-24.04-arm"' in bootstrap.read_text()
    assert "private-" not in capsys.readouterr().out


def test_timeout_and_no_fallback(bootstrap, monkeypatch):
    """The wall-clock bound ends preparation; explicit none never queues an unintended hosted job."""
    timeline = iter([0, 1, 21, 21])
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: next(timeline))
    monkeypatch.setattr(ci_bootstrap.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        ci_bootstrap,
        "request_json",
        lambda *args, **kwargs: {"value": "oidc", "status": "preparing"},
    )
    ci_bootstrap.main()
    assert 'runs_on="ubuntu-24.04-arm"' in bootstrap.read_text()
    monkeypatch.setenv("POWERCELL_FALLBACK", "none")
    monkeypatch.setattr(ci_bootstrap.time, "monotonic", lambda: 0)
    monkeypatch.setattr(
        ci_bootstrap,
        "request_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    with pytest.raises(RuntimeError, match="fallback is disabled"):
        ci_bootstrap.main()


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
    ci_bootstrap.main()
    assert 'runs_on="ubuntu-24.04-arm"' in bootstrap.read_text()


@pytest.mark.parametrize(
    "decision",
    [
        {"status": "failed", "reason": "cancelled"},
        {
            "status": "ready",
            "runs_on": ["self-hosted", "other-tenant"],
            "backend": "powercell",
        },
    ],
)
def test_failed_or_foreign_runner_never_becomes_an_output(
    bootstrap, monkeypatch, decision
):
    """A terminal reservation or foreign runner label fails routing explicitly."""
    responses = iter([{"value": "oidc"}, decision, decision])
    monkeypatch.setattr(
        ci_bootstrap, "request_json", lambda *args, **kwargs: next(responses)
    )
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
    assert ci_bootstrap.request_json(
        "https://api.example", token="private-token", body={}
    ) == {"ok": True}
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
    runpy.run_path(ci_bootstrap.__file__, run_name="__main__")
    assert 'runs_on="ubuntu-24.04-arm"' in bootstrap.read_text()


@pytest.mark.parametrize("cancel_error", [False, True])
def test_lost_commit_response_is_cancelled_before_fallback(
    bootstrap, monkeypatch, cancel_error
):
    """A failed selection acknowledgement withdraws the prepared runner before returning hosted output."""
    calls = []

    def peer(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return {"value": "oidc"}
        if url.endswith("/reserve"):
            return {
                "status": "ready",
                "backend": "powercell",
                "runs_on": ["self-hosted", "powercell-one"],
            }
        if url.endswith("/commit"):
            raise urllib.error.URLError("lost response")
        assert url.endswith("/cancel")
        if cancel_error:
            raise urllib.error.URLError("cancel unavailable")
        return {"status": "ready", "backend": "github", "runs_on": "ubuntu-24.04-arm"}

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    ci_bootstrap.main()
    assert calls[-1].endswith("/cancel")
    assert 'runs_on="ubuntu-24.04-arm"' in bootstrap.read_text()
