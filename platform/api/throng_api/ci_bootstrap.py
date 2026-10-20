"""Embedded in generated workflows; standard library only, no long-lived CI secret."""

import json
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException, IncompleteRead


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
        if remaining := getattr(response, "length", 0):
            raise IncompleteRead(content, remaining)
        return json.loads(content)


def identity_token(url):
    response = request_json(url, token=os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])
    if not isinstance(response, dict):
        raise ValueError("Invalid identity response")
    identity = response["value"]
    if not isinstance(identity, str) or not identity:
        raise ValueError("Invalid identity token")
    return identity


def runner_selection(decision):
    runs_on = decision.get("runs_on") if isinstance(decision, dict) else None
    if not (
        isinstance(decision, dict)
        and decision.get("status") == "ready"
        and isinstance(runs_on, list)
        and len(runs_on) == 1
        and isinstance(runs_on[0], str)
        and runs_on[0].startswith("powercell-")
    ):
        raise ValueError("Invalid runner selection")
    return runs_on


def retry_delay(error):
    """Bound observer retries by provider cooldown; close failed HTTP responses."""
    if not isinstance(error, urllib.error.HTTPError):
        return 2
    try:
        if error.code != 429 and not 500 <= error.code < 600:
            raise error
        try:
            return min(310, max(2, int((error.headers or {}).get("Retry-After", "2"))))
        except (TypeError, ValueError):
            return 2
    finally:
        error.close()


def watch_timeout(signum, frame):
    """Fail normally before GitHub's job timeout, including a trickling HTTP read."""
    raise RuntimeError("CI preparation timed out")


def main():
    started = time.monotonic()
    watch = os.environ.get("POWERCELL_MODE") == "watch"
    deadline = started + (300 if watch else int(os.environ["POWERCELL_WAIT_SECONDS"])) + 10
    published = False
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
        refresh_at = started
        endpoint = (
            os.environ["POWERCELL_API"] + "/v1/github/ci/routes/" + os.environ["POWERCELL_ROUTE"]
        )
        while (now := time.monotonic()) < deadline:
            requested_at = time.monotonic()
            wait = min(20, max(0, deadline - requested_at))
            interval = 2 if watch else 0.25
            try:
                if identity is None or now >= refresh_at:
                    try:
                        identity = identity_token(url)
                        refresh_at = now + 120
                    except (OSError, HTTPException) as error:
                        if not watch or identity is None:
                            raise
                        refresh_at = time.monotonic() + retry_delay(error)
                decision = request_json(
                    endpoint + ("/watch" if watch else f"/reserve?wait_seconds={wait:g}"),
                    token=identity,
                    body={"instance": os.environ.get("POWERCELL_INSTANCE", "default")},
                    timeout=min(5, deadline - requested_at) if watch else wait + 5,
                )
            except (OSError, HTTPException) as error:
                if not watch:
                    raise
                interval = retry_delay(error)
                requested_at = time.monotonic()
                decision = {"status": "waiting"}
            if not isinstance(decision, dict):
                raise ValueError("Invalid routing response")
            if watch and decision.get("status") == "started":
                return
            if not watch and decision.get("status") == "ready":
                runs_on = runner_selection(decision)
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.write("runs_on=" + json.dumps(runs_on, separators=(",", ":")) + "\n")
                published = True
                print("Power Cell runner ready:", runs_on[0])
                return
            if decision.get("status") == "failed":
                raise RuntimeError(
                    "CI reservation failed: " + str(decision.get("reason", "unknown"))
                )
            if decision.get("status") != ("waiting" if watch else "preparing"):
                raise ValueError("Invalid routing status")
            # Older servers may ignore wait_seconds: avoid a busy retry loop.
            pause = min(
                interval - (time.monotonic() - requested_at),
                deadline - time.monotonic(),
            )
            if pause > 0:
                time.sleep(pause)
        raise RuntimeError("CI preparation timed out")
    except (OSError, HTTPException, ValueError, KeyError):
        raise RuntimeError("Power Cells routing unavailable") from None
    finally:
        if not watch and not published and endpoint and identity:
            try:
                request_json(
                    endpoint + "/cancel",
                    token=identity,
                    body={"instance": os.environ.get("POWERCELL_INSTANCE", "default")},
                )
            except (OSError, HTTPException, ValueError, KeyError):
                pass  # Preparation deadline and selection lease also bound cleanup.


if __name__ == "__main__":
    if os.environ.get("POWERCELL_MODE") == "watch":
        signal.signal(signal.SIGALRM, watch_timeout)
        signal.alarm(310)
    main()
