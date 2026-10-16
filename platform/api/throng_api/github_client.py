"""Bounded GitHub App transport. Credentials and response bodies stay out of errors."""

import base64
import json
import re
import time
from contextlib import asynccontextmanager

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API_URL = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_VERSION = "2022-11-28"
MAX_RESPONSE = 4 * 1024 * 1024


class GitHubError(Exception):
    """A deliberately redacted external failure suitable for API diagnostics."""

    def __init__(self, status=502):
        self.status = status
        super().__init__(f"GitHub request failed (HTTP {status})")


class GitHubClient:
    def __init__(self, client_id, client_secret, private_key, *, transport=None, clock=time.time):
        key = serialization.load_pem_private_key(private_key, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("GitHub App requires an RSA private key of at least 2048 bits")
        self.client_id = client_id
        self._client_secret = client_secret
        self._key = key
        self._transport = transport
        self._clock = clock

    def app_token(self):
        def encode(value):
            return base64.urlsafe_b64encode(
                json.dumps(value, separators=(",", ":")).encode()
            ).rstrip(b"=")

        now = int(self._clock())
        head = encode({"alg": "RS256", "typ": "JWT"})
        body = encode({"iat": now - 60, "exp": now + 540, "iss": self.client_id})
        message = head + b"." + body
        signature = self._key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return (message + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    async def _json(self, method, url, *, token=None, payload=None, form=None, params=None):
        headers = {"Accept": "application/json", "X-GitHub-Api-Version": API_VERSION}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=10, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    method, url, headers=headers, json=payload, data=form, params=params
                ) as response:
                    if response.status_code not in {200, 201, 204}:
                        raise GitHubError(response.status_code)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            raise GitHubError()
                    if response.status_code == 204:
                        return None
                    result = json.loads(content)
        except (httpx.HTTPError, ValueError, UnicodeError) as error:
            raise GitHubError() from error
        if not isinstance(result, (dict, list)):
            raise GitHubError()
        return result

    async def api(self, method, path, *, token=None, payload=None, params=None):
        # Callers construct API paths from verified numeric IDs, not arbitrary URLs.
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path or "//" in path:
            raise ValueError("Invalid GitHub API path")
        return await self._json(
            method,
            API_URL + path,
            token=token if token is not None else self.app_token(),
            payload=payload,
            params=params,
        )

    async def oauth(self, *, code=None, refresh_token=None):
        fields = {"client_id": self.client_id, "client_secret": self._client_secret}
        if (code is None) == (refresh_token is None):
            raise ValueError("Choose an OAuth code or refresh token")
        if code is not None:
            fields["code"] = code
        else:
            fields.update(grant_type="refresh_token", refresh_token=refresh_token)
        result = await self._json("POST", TOKEN_URL, form=fields)
        if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
            raise GitHubError(401)
        return result

    @asynccontextmanager
    async def installation_token(self, installation_id, repository_ids, permissions):
        """Short operations use an explicitly scoped token and revoke it afterwards."""
        result = await self.api(
            "POST",
            f"/app/installations/{int(installation_id)}/access_tokens",
            payload={"repository_ids": repository_ids, "permissions": permissions},
        )
        if not isinstance(result, dict) or not isinstance(result.get("token"), str):
            raise GitHubError()
        token = result["token"]
        try:
            yield token
        finally:
            try:
                await self.api("DELETE", "/installation/token", token=token)
            except GitHubError:
                # Tokens expire at GitHub. Failed revocation must not hide the
                # result or error of the operation that used the scoped token.
                pass
