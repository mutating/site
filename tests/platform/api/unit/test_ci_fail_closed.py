"""The workflow router cannot emit a hosted runner selection."""

import urllib.error

import pytest

from tests.platform.api.unit.test_ci_bootstrap import bootstrap as bootstrap
from throng_api import ci_bootstrap


# Assertions: no capacity, API outage, malformed/legacy hosted decisions and
# expired preparation all fail without outputs; cleanup is attempted after OIDC.
@pytest.mark.parametrize(
    "response",
    [
        {"status": "failed", "reason": "no_capacity"},
        {"status": "ready", "backend": "github", "runs_on": "ubuntu-24.04-arm"},
        [],
        urllib.error.URLError("private-error-token"),
    ],
)
def test_bootstrap_never_selects_a_hosted_runner(bootstrap, monkeypatch, capsys, response):
    """Fail without output and cancel preparation after any routing error."""
    calls = []

    def peer(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return {"value": "private-oidc"}
        if url.endswith("/cancel"):
            return {"status": "failed"}
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(ci_bootstrap, "request_json", peer)
    with pytest.raises((RuntimeError, ValueError)) as error:
        ci_bootstrap.main()
    assert not bootstrap.exists()
    assert calls[-1].endswith("/cancel")
    assert "private-" not in str(error.value) + capsys.readouterr().out
