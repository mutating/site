"""ACME error transitions, polling and exact retries exercised over TCP."""

import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from .conftest import attestation, b64, jws

pytestmark = pytest.mark.anyio


async def test_account_nonce_and_preorder_contract(client, enrollment):
    """Nonce retrieval, account retries and polling have the status semantics expected by native ACME clients."""
    e = enrollment
    key = ec.generate_private_key(ec.SECP256R1())
    for method, operation, status in [
        ("HEAD", "nonce", 200),
        ("GET", "nonce", 204),
        ("GET", "order", 405),
    ]:
        response = await client.request(method, e.path + "/" + operation)
        assert response.status_code == status
        assert response.headers["Replay-Nonce"]
    nonce = response.headers["Replay-Nonce"]
    for payload, status in [
        (None, 400),
        ({"unknown": True}, 400),
        ({"onlyReturnExisting": True}, 400),
        ({}, 201),
        ({}, 200),
    ]:
        response = await client.post(
            e.path + "/new-account", json=jws(key, e.base + "/new-account", nonce, payload)
        )
        assert response.status_code == status, response.text
        nonce = response.headers["Replay-Nonce"]
    account = e.base + "/account"
    for operation, payload, status in [
        ("account", None, 200),
        ("account", {"status": "deactivated"}, 400),
        ("orders", None, 200),
        ("orders", {}, 400),
        ("order", None, 401),
    ]:
        response = await client.post(
            e.path + "/" + operation,
            json=jws(key, e.base + "/" + operation, nonce, payload, kid=account),
        )
        assert response.status_code == status, response.text
        nonce = response.headers["Replay-Nonce"]
    other = ec.generate_private_key(ec.SECP256R1())
    response = await client.post(
        e.path + "/new-account", json=jws(other, e.base + "/new-account", nonce, {})
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "operation,payload,status",
    [
        ("challenge", None, 200),
        ("challenge", {}, 400),
        ("finalize", {}, 400),
        ("order", {}, 400),
        ("certificate", None, 400),
        ("unknown", None, 400),
        ("orders", None, 200),
    ],
)
async def test_order_requires_correct_sequence_and_post_as_get(
    client, acme_order, operation, payload, status
):
    """Out-of-sequence issuance and polling with mutation bodies are rejected without changing the order."""
    order = acme_order
    e = order.enrollment
    response = await client.post(
        e.path + "/" + operation,
        json=jws(order.key, e.base + "/" + operation, order.nonce, payload, kid=order.account),
    )
    assert response.status_code == status, response.text


async def test_finalized_order_retries_and_attestation_immutability(
    client, verified_enrollment, pki, clock
):
    """A finalized order returns the same certificate and cannot be rebound through a second attestation or CSR."""
    identity = verified_enrollment
    order = identity.order
    e = order.enrollment
    nonce = identity.nonce
    for payload, status in [
        (None, 400),
        ({"csr": b64(identity.csr.public_bytes(serialization.Encoding.DER))}, 200),
    ]:
        response = await client.post(
            e.path + "/finalize",
            json=jws(order.key, e.base + "/finalize", nonce, payload, kid=order.account),
        )
        assert response.status_code == status, response.text
        nonce = response.headers["Replay-Nonce"]
    other_csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(identity.key, hashes.SHA256())
    )
    response = await client.post(
        e.path + "/finalize",
        json=jws(
            order.key,
            e.base + "/finalize",
            nonce,
            {"csr": b64(other_csr.public_bytes(serialization.Encoding.DER))},
            kid=order.account,
        ),
    )
    assert response.status_code == 400
    nonce = response.headers["Replay-Nonce"]
    for key, status in [(identity.key, 200), (ec.generate_private_key(ec.SECP256R1()), 400)]:
        response = await client.post(
            e.path + "/challenge",
            json=jws(
                order.key,
                e.base + "/challenge",
                nonce,
                {"attObj": attestation(pki, key, order.token, clock.now)},
                kid=order.account,
            ),
        )
        assert response.status_code == status, response.text
        nonce = response.headers["Replay-Nonce"]
    response = await client.post(
        e.path + "/authorization",
        json=jws(order.key, e.base + "/authorization", nonce, None, kid=order.account),
    )
    assert response.status_code == 200 and response.json()["status"] == "valid"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_kid",
        "short_signature",
        "unsupported_alg",
        "bad_nonce_type",
        "bad_base64_type",
        "duplicate_json",
    ],
)
async def test_malformed_signed_envelopes_are_controlled_errors(client, acme_order, mutation):
    """JWS parsing rejects malformed values before database binding or state changes."""
    order = acme_order
    e = order.enrollment
    patch = {}
    if mutation == "wrong_kid":
        patch["kid"] = e.base + "/another-account"
    if mutation == "unsupported_alg":
        patch["alg"] = "none"
    if mutation == "bad_nonce_type":
        patch["nonce"] = []
    payload = jws(order.key, e.base + "/order", order.nonce, None, kid=order.account, patch=patch)
    if mutation == "short_signature":
        payload["signature"] = b64(b"1")
    if mutation == "bad_base64_type":
        payload["signature"] = 123
    if mutation == "duplicate_json":
        payload["protected"] = b64(b'{"alg":"ES256","alg":"none"}')
    response = await client.post(e.path + "/order", json=payload)
    assert response.status_code == 401, response.text


async def test_rsa_account_support_is_separate_from_attested_device_key(client, enrollment):
    """A standard RS256 account can create its order, while small account keys are rejected."""
    e = enrollment
    response = await client.head(e.path + "/nonce")
    nonce = response.headers["Replay-Nonce"]
    for size, status in [(1024, 401), (2048, 201)]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=size)
        numbers = key.public_key().public_numbers()
        public = {
            "kty": "RSA",
            "n": b64(numbers.n.to_bytes(size // 8, "big")),
            "e": b64(numbers.e.to_bytes(3, "big")),
        }
        protected = b64(
            json.dumps(
                {"alg": "RS256", "url": e.base + "/new-account", "nonce": nonce, "jwk": public}
            ).encode()
        )
        payload = b64(b"{}")
        signature = key.sign(
            (protected + "." + payload).encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        response = await client.post(
            e.path + "/new-account",
            json={"protected": protected, "payload": payload, "signature": b64(signature)},
        )
        assert response.status_code == status, response.text
        nonce = response.headers["Replay-Nonce"]
