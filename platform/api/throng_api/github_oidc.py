"""GitHub Actions OIDC: fixed issuer/JWKS, bounded cache and explicit RS256 claims."""

import asyncio
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import HTTPException

from throng_api.acme import jwk_key, unb64
from throng_api.device_security import strict_json
from throng_api.github_client import GitHubError

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"


class ActionsOIDC:
    def __init__(self, client, audience, *, monotonic=time.monotonic):
        self.client = client
        self.audience = audience
        self.keys = {}
        self.refreshed = float("-inf")
        self.monotonic = monotonic
        self.lock = asyncio.Lock()

    async def verify(self, token, now):
        started = self.monotonic()
        try:
            if not isinstance(token, str) or not 0 < len(token) <= 20000:
                raise ValueError()
            head, body, signature = token.split(".")
            header, claims = strict_json(unb64(head)), strict_json(unb64(body))
            if (
                header.get("alg") != "RS256"
                or not isinstance(header.get("kid"), str)
                or header.get("crit")
            ):
                raise ValueError()
            kid = header["kid"]
            async with self.lock:
                age = self.monotonic() - self.refreshed
                if age > 600 or (kid not in self.keys and age > 30):
                    data = await self.client._json("GET", JWKS_URL)
                    keys = {}
                    for row in data["keys"]:
                        if row.get("kty") == "RSA" and row.get("alg") == "RS256":
                            keys[row["kid"]] = jwk_key(
                                {name: row[name] for name in ("kty", "n", "e")}, "RS256"
                            )
                    self.keys, self.refreshed = keys, self.monotonic()
            self.keys[kid].verify(
                unb64(signature), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256()
            )
            timestamp = now.timestamp() + max(0, self.monotonic() - started)
            if (
                claims.get("iss") != ISSUER
                or claims.get("aud") != self.audience
                or any(type(claims.get(key)) is not int for key in ("exp", "iat", "nbf"))
                or not timestamp - 600 <= claims["iat"] <= timestamp + 30
                or not claims["iat"] - 600 <= claims["nbf"] <= timestamp + 30
                or not timestamp < claims["exp"] <= claims["iat"] + 600
                or claims.get("runner_environment") != "github-hosted"
            ):
                raise ValueError()
            for name in ("repository_id", "repository_owner_id", "run_id", "run_attempt"):
                value = claims.get(name)
                if (
                    not isinstance(value, str)
                    or not value.isascii()
                    or not value.isdigit()
                    or not 0 < int(value) < 2**63
                ):
                    raise ValueError()
                if name == "run_attempt" and int(value) >= 2**31:
                    raise ValueError()
            for name in ("repository", "workflow_ref", "ref", "sha", "event_name", "sub"):
                if not isinstance(claims.get(name), str) or not 0 < len(claims[name]) <= 1000:
                    raise ValueError()
            return claims
        except GitHubError as error:
            raise HTTPException(503, "GitHub signing keys unavailable") from error
        except (ValueError, KeyError, TypeError, AttributeError, InvalidSignature) as error:
            raise HTTPException(401, "Invalid GitHub Actions identity") from error
