"""Embedded in generated workflows; standard library only, no long-lived CI secret."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "Redirect refused", headers, fp)


def request_json(url, *, token, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        content = response.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            raise ValueError("CI routing response too large")
        return json.loads(content)


def main():
    fallback = os.environ["POWERCELL_FALLBACK"]
    deadline = time.monotonic() + int(os.environ["POWERCELL_WAIT_SECONDS"]) + 10
    result = {"runs_on": fallback, "backend": "github", "reason": "platform_unavailable"}
    endpoint = identity = None
    try:
        url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        parts = urllib.parse.urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or not (parts.hostname or "").endswith(".actions.githubusercontent.com")
        ):
            raise ValueError("Unexpected GitHub identity endpoint")
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {"audience": os.environ["POWERCELL_AUDIENCE"]}
        )
        identity = request_json(url, token=os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])["value"]
        endpoint = (
            os.environ["POWERCELL_API"] + "/v1/github/ci/routes/" + os.environ["POWERCELL_ROUTE"]
        )
        while time.monotonic() < deadline:
            decision = request_json(
                endpoint + "/reserve",
                token=identity,
                body={"instance": os.environ.get("POWERCELL_INSTANCE", "default")},
            )
            if decision.get("status") == "ready":
                if decision.get("backend") == "powercell":
                    decision = request_json(
                        endpoint + "/commit",
                        token=identity,
                        body={"instance": os.environ.get("POWERCELL_INSTANCE", "default")},
                    )
                result = decision
                break
            if decision.get("status") == "failed":
                # The configured route explicitly declined fallback or is terminal.
                raise RuntimeError(
                    "CI reservation failed: " + str(decision.get("reason", "unknown"))
                )
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        else:
            result["reason"] = "routing_timeout"
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
        print("::warning::Power Cells routing unavailable; using configured GitHub fallback")
    if result.get("backend") != "powercell" and endpoint and identity:
        try:
            request_json(
                endpoint + "/cancel",
                token=identity,
                body={"instance": os.environ.get("POWERCELL_INSTANCE", "default")},
            )
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError, OSError):
            pass  # Preparation deadline and selection lease also bound cleanup.
    if result["runs_on"] == "none":
        raise RuntimeError("Power Cell unavailable and fallback is disabled")
    runs_on = result["runs_on"]
    if not (
        runs_on == fallback
        or isinstance(runs_on, list)
        and len(runs_on) == 2
        and runs_on[0] == "self-hosted"
        and isinstance(runs_on[1], str)
        and runs_on[1].startswith("powercell-")
    ):
        raise ValueError("Invalid runner selection")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("runs_on=" + json.dumps(runs_on, separators=(",", ":")) + "\n")
        output.write("backend=" + ("powercell" if isinstance(runs_on, list) else "github") + "\n")
    print(
        "Selected CI backend:", result["backend"], "reason:", result.get("reason", "runner_ready")
    )


if __name__ == "__main__":
    main()
