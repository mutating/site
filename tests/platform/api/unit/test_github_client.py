"""App JWTs, scoped credentials and bounded external responses."""

import base64
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from throng_api.github_client import MAX_RESPONSE, GitHubClient, GitHubError

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def key():
    """Use a disposable RSA key; no actual App credentials enter the suite."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key):
    """Serialize only disposable fixture keys."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def client(key, handler):
    """Exercise the real HTTPX request construction with a deterministic peer."""
    return GitHubClient(
        "fixture-client",
        "fixture-secret",
        pem(key),
        transport=httpx.MockTransport(handler),
        clock=lambda: 2000,
    )


def test_jwt_is_signed_for_the_client_id_with_bounded_expiry(key):
    """Verify the JWT cryptographically, including backdating and expiration."""
    service = client(key, lambda request: httpx.Response(200, json={}))
    head, body, signature = service.app_token().split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    assert json.loads(decode(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(decode(body)) == {"iat": 1940, "exp": 2540, "iss": "fixture-client"}
    key.public_key().verify(
        decode(signature), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256()
    )


@pytest.mark.parametrize("kind", ["ec", "short-rsa", "invalid"])
def test_rejects_incompatible_private_keys(kind):
    """Reject keys that cannot securely authenticate as a GitHub App."""
    raw = b"invalid"
    if kind == "ec":
        raw = pem(ec.generate_private_key(ec.SECP256R1()))
    elif kind == "short-rsa":
        raw = pem(rsa.generate_private_key(public_exponent=65537, key_size=1024))
    with pytest.raises(ValueError):
        GitHubClient("client", "secret", raw)


@pytest.mark.parametrize("path", ["https://evil.test/", "//evil.test", "/a/../b", "/a?token=x", ""])
async def test_api_paths_cannot_redirect_credentials(key, path):
    """Reject URL/path injection before performing a network operation."""

    def handler(request):
        pytest.fail("Invalid paths must not produce HTTP requests")

    with pytest.raises(ValueError, match="Invalid GitHub API path"):
        await client(key, handler).api("GET", path)


@pytest.mark.parametrize("status", [200, 201, 204])
async def test_api_success_constructs_a_github_only_authenticated_request(key, status):
    """Use explicit bearer credentials and preserve successful structured responses."""

    def handler(request):
        assert str(request.url) == "https://api.github.com/app?page=2"
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        assert json.loads(request.content) == {"value": 1}
        return httpx.Response(status, json={"ok": True})

    result = await client(key, handler).api(
        "POST", "/app", token="fixture-token", payload={"value": 1}, params={"page": 2}
    )
    assert result == (None if status == 204 else {"ok": True})


@pytest.mark.parametrize("status", [301, 307, 401, 403, 404, 429, 500])
async def test_external_errors_are_redacted_and_redirects_are_not_followed(key, status):
    """Never expose response bodies or forward credentials to a redirect target."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            status, text="fixture-secret", headers={"location": "https://evil.test/"}
        )

    with pytest.raises(GitHubError) as result:
        await client(key, handler).api("GET", "/app")
    assert result.value.status == status
    assert "fixture-secret" not in str(result.value)
    assert calls == ["https://api.github.com/app"]


@pytest.mark.parametrize("body", [b"bad-json", b'"scalar"', b"null", b"12", b"\xff"])
async def test_rejects_malformed_or_unexpected_response_shapes(key, body):
    """Malformed JSON and scalar responses are treated as external failures."""
    with pytest.raises(GitHubError) as result:
        await client(key, lambda request: httpx.Response(200, content=body)).api("GET", "/app")
    assert result.value.status == 502


async def test_rejects_oversized_stream_and_closes_it(key):
    """Bound actual streamed bytes and close the HTTP response after overflow."""
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * MAX_RESPONSE
            yield b"x"
            pytest.fail("Must stop reading at the configured limit")

        async def aclose(self):
            closed.append(True)

    with pytest.raises(GitHubError):
        await client(key, lambda request: httpx.Response(200, stream=Stream())).api("GET", "/app")
    assert closed == [True]


async def test_network_failure_has_a_safe_error(key):
    """Connection errors never become successful or credential-bearing responses."""

    def handler(request):
        raise httpx.ConnectTimeout("fixture-secret", request=request)

    with pytest.raises(GitHubError) as result:
        await client(key, handler).api("GET", "/app")
    assert str(result.value) == "GitHub request failed (HTTP 502)"


@pytest.mark.parametrize("refresh", [False, True])
async def test_oauth_sends_credentials_in_the_form_and_requires_an_access_token(key, refresh):
    """Both OAuth exchanges use the fixed token endpoint without bearer headers."""

    def handler(request):
        assert str(request.url) == "https://github.com/login/oauth/access_token"
        assert "authorization" not in request.headers
        fields = parse_qs(request.content.decode())
        assert fields["client_id"] == ["fixture-client"]
        assert fields["client_secret"] == ["fixture-secret"]
        if refresh:
            assert fields["refresh_token"] == ["refresh"]
            assert fields["grant_type"] == ["refresh_token"]
        else:
            assert fields["code"] == ["code"]
        return httpx.Response(200, json={"access_token": "user-token"})

    result = await client(key, handler).oauth(
        **({"refresh_token": "refresh"} if refresh else {"code": "code"})
    )
    assert result["access_token"] == "user-token"


@pytest.mark.parametrize("arguments", [{}, {"code": "a", "refresh_token": "b"}])
async def test_oauth_requires_exactly_one_grant(key, arguments):
    """Do not accidentally exchange an empty or ambiguous OAuth grant."""
    with pytest.raises(ValueError, match="Choose an OAuth code"):
        await client(key, lambda request: httpx.Response(200)).oauth(**arguments)


@pytest.mark.parametrize("result", [{"error": "bad_verification_code"}, {"access_token": 12}, []])
async def test_oauth_rejects_error_documents_and_missing_tokens(key, result):
    """An HTTP 200 error document must not authenticate a platform account."""
    with pytest.raises(GitHubError) as error:
        await client(key, lambda request: httpx.Response(200, json=result)).oauth(code="bad")
    assert error.value.status == 401


@pytest.mark.parametrize("operation_fails", [False, True])
@pytest.mark.parametrize("revocation_fails", [False, True])
async def test_installation_token_is_scoped_and_always_revoked(
    key, operation_fails, revocation_fails
):
    """Revoke temporary credentials on success/error without hiding the operation's error."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.path == "/app/installations/123/access_tokens"
            assert json.loads(request.content) == {
                "repository_ids": [456],
                "permissions": {"contents": "read"},
            }
            assert request.headers["authorization"].startswith("Bearer ey")
            return httpx.Response(201, json={"token": "installation-token"})
        assert request.method == "DELETE" and request.url.path == "/installation/token"
        assert request.headers["authorization"] == "Bearer installation-token"
        return httpx.Response(503 if revocation_fails else 204)

    @asynccontextmanager
    async def operation():
        async with client(key, handler).installation_token(
            123, [456], {"contents": "read"}
        ) as token:
            assert token == "installation-token"
            if operation_fails:
                raise RuntimeError("operation failed")
            yield

    if operation_fails:
        with pytest.raises(RuntimeError, match="operation failed"):
            async with operation():
                pytest.fail("Failed operation must not yield")
    else:
        async with operation():
            pass
    assert len(calls) == 2


@pytest.mark.parametrize("result", [{}, {"token": 12}, []])
async def test_installation_token_requires_valid_credentials_before_yielding(key, result):
    """Do not run a repository operation when GitHub did not issue a token."""
    with pytest.raises(GitHubError):
        async with client(key, lambda request: httpx.Response(201, json=result)).installation_token(
            123, [456], {}
        ):
            pytest.fail("No valid token was issued")
