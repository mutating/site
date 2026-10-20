"""OIDC verifier rejects forged signatures, ambiguous algorithms and stale identities."""

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException

from throng_api.github_client import GitHubClient
from throng_api.github_oidc import ActionsOIDC

pytestmark = pytest.mark.anyio


def encoded(value):
    """Encode a wire part independently of the verifier."""
    content = json.dumps(value).encode() if not isinstance(value, bytes) else value
    return base64.urlsafe_b64encode(content).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def signing_key():
    """Reuse a disposable RSA key; tests never use the real App's key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc(signing_key):
    """Provide signed identities and a deterministic external JWKS endpoint."""
    key = signing_key
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_numbers()
    peer = SimpleNamespace(calls=0, time=0, status=200)
    keys = [
        {"kty": "EC", "alg": "ES256", "kid": "ignored"},
        {
            "kty": "RSA",
            "alg": "RS256",
            "kid": "one",
            "e": encoded(public.e.to_bytes(3, "big")),
            "n": encoded(public.n.to_bytes(256, "big")),
        },
    ]

    def transport(request):
        peer.calls += 1
        return httpx.Response(peer.status, json={"keys": keys})

    client = GitHubClient("test", "secret", pem, transport=httpx.MockTransport(transport))
    peer.verifier = ActionsOIDC(client, "ci-audience", monotonic=lambda: peer.time)
    peer.now = datetime(2026, 10, 16, tzinfo=UTC)

    def token(updates=None, header=None):
        now = int(peer.now.timestamp())
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "ci-audience",
            "iat": now,
            "nbf": now,
            "exp": now + 300,
            "runner_environment": "github-hosted",
            "repository_id": "1",
            "repository_owner_id": "2",
            "run_id": "3",
            "run_attempt": "1",
            "repository": "org/repo",
            "workflow_ref": "org/repo/.github/workflows/ci.yml@refs/heads/main",
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "event_name": "push",
            "sub": "repo:org/repo:ref:refs/heads/main",
        }
        claims.update(updates or {})
        message = encoded(header or {"alg": "RS256", "kid": "one"}) + "." + encoded(claims)
        return (
            message + "." + encoded(key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256()))
        )

    peer.token = token
    return peer


async def test_valid_and_bounded_key_cache(oidc):
    """Valid identities reuse cached keys; random kids do not cause a fetch storm."""
    assert (await oidc.verifier.verify(oidc.token(), oidc.now))["run_id"] == "3"
    await oidc.verifier.verify(oidc.token(), oidc.now)
    assert oidc.calls == 1
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(oidc.token(header={"alg": "RS256", "kid": "unknown"}), oidc.now)
    assert caught.value.status_code == 401 and oidc.calls == 1
    oidc.time = 31
    with pytest.raises(HTTPException):
        await oidc.verifier.verify(oidc.token(header={"alg": "RS256", "kid": "unknown"}), oidc.now)
    assert oidc.calls == 2
    oidc.time = 700
    await oidc.verifier.verify(oidc.token(), oidc.now)
    assert oidc.calls == 3


@pytest.mark.parametrize(
    "updates",
    [
        {"repository_id": "0"},
        {"repository_id": 1},
        {"run_id": "-1"},
        {"run_id": "١"},
        {"run_id": str(2**63)},
        {"run_attempt": str(2**31)},
        {"sha": None},
        {"sub": ""},
        {"event_name": "x" * 1001},
        {"exp": True},
        {"exp": 1},
        {"iat": 0},
        {"nbf": 0},
        {"aud": ["ci-audience"]},
        {"iss": "https://evil.test"},
        {"runner_environment": "self-hosted"},
    ],
)
async def test_invalid_claims(oidc, updates):
    """A valid signature alone cannot authorize malformed or stale claims."""
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(oidc.token(updates), oidc.now)
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "kid": "one"},
        {"alg": "HS256", "kid": "one"},
        {"alg": "RS256", "kid": 1},
        {"alg": "RS256", "kid": "one", "crit": ["extra"]},
    ],
)
async def test_rejects_algorithm_confusion(oidc, header):
    """The verifier accepts only an explicit RS256 key ID and no unknown critical extension."""
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(oidc.token(header=header), oidc.now)
    assert caught.value.status_code == 401 and oidc.calls == 0


@pytest.mark.parametrize("token", [None, "", "a" * 20001, "not.a.jwt", "a.b", "a.b.c.d"])
async def test_malformed_tokens(oidc, token):
    """Malformed wire values are rejected without leaking parsing exceptions."""
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(token, oidc.now)
    assert caught.value.status_code == 401


async def test_forged_signature_and_unavailable_keys(oidc):
    """Signature forgery is unauthorized; unavailable signing keys are a retryable provider failure."""
    value = oidc.token()
    value = value.rsplit(".", 1)[0] + "." + encoded(b"forged")
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(value, oidc.now)
    assert caught.value.status_code == 401
    oidc.time = 700
    oidc.status = 503
    with pytest.raises(HTTPException) as caught:
        await oidc.verifier.verify(oidc.token(), oidc.now)
    assert caught.value.status_code == 503


async def test_not_before_may_precede_issue_time(oidc):
    """GitHub can backdate nbf for clock skew without changing the token's issue time."""
    claims = await oidc.verifier.verify(
        oidc.token({"nbf": int(oidc.now.timestamp()) - 60}), oidc.now
    )
    assert claims["iat"] - claims["nbf"] == 60
