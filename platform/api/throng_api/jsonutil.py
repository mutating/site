"""Strict JSON and JOSE primitives shared by signed protocol adapters."""

import base64
import json

from cryptography.hazmat.primitives.asymmetric import ec, rsa


def strict_json(data):
    """Reject duplicate members and non-standard numeric constants."""

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = value
        return result

    return json.loads(
        data,
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def unb64(value):
    """Decode unpadded base64url with a bounded input."""
    if not isinstance(value, str) or len(value) > 512 * 1024:
        raise ValueError("Invalid base64url value")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def jwk_key(jwk, algorithm):
    """Build an EC P-256 or RSA public key from a minimal JWK."""
    if (
        algorithm == "ES256"
        and set(jwk) == {"kty", "crv", "x", "y"}
        and jwk["kty"] == "EC"
        and jwk["crv"] == "P-256"
    ):
        return ec.EllipticCurvePublicNumbers(
            int.from_bytes(unb64(jwk["x"]), "big"),
            int.from_bytes(unb64(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
    if algorithm == "RS256" and set(jwk) == {"kty", "n", "e"} and jwk["kty"] == "RSA":
        key = rsa.RSAPublicNumbers(
            int.from_bytes(unb64(jwk["e"]), "big"), int.from_bytes(unb64(jwk["n"]), "big")
        ).public_key()
        if not 2048 <= key.key_size <= 4096:
            raise ValueError("Unsupported RSA account key size")
        return key
    raise ValueError("Unsupported account algorithm or JWK")
