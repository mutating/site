"""Bounded GitHub App transport. Credentials and response bodies stay out of errors."""

import asyncio
import base64
import hashlib
import json
import math
import re
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import unquote, urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API_URL = "https://api.github.com"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_VERSION = "2022-11-28"
MAX_RESPONSE = 4 * 1024 * 1024


class GitHubError(Exception):
    """A deliberately redacted external failure suitable for API diagnostics."""

    def __init__(self, status=502, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"GitHub request failed (HTTP {status})")


class GitHubToken(str):
    """Carry quota identity locally; only the string is sent as a credential."""

    def __new__(cls, value, bucket):
        result = super().__new__(cls, value)
        result.bucket = bucket
        return result


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
        self._cooldowns = {}
        self._connection = ContextVar("github_connection", default=None)

    @asynccontextmanager
    async def connection(self):
        """Reuse keep-alive within one operation, without retaining credentials/cookies."""
        client = self._connection.get()
        if client is not None and not client.is_closed:
            yield client
            return
        async with httpx.AsyncClient(
            transport=self._transport,
            timeout=10,
            trust_env=False,
            follow_redirects=False,
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        ) as client:
            mark = self._connection.set(client)
            try:
                yield client
            finally:
                self._connection.reset(mark)

    def _check_rate_limit(self, bucket):
        limited = self._cooldowns.get(bucket)
        if limited and limited[0] > self._clock():
            raise GitHubError(limited[1], math.ceil(limited[0] - self._clock()))

    def _rate_limit(self, bucket, response, content):
        status, headers, now = response.status_code, response.headers, self._clock()
        if status != 429 and not (
            status == 403
            and (
                "retry-after" in headers
                or headers.get("x-ratelimit-remaining") == "0"
                or b"rate limit" in content.lower()
            )
        ):
            return None
        previous = self._cooldowns.get(bucket)
        waits = []
        for value, offset in (
            (headers.get("retry-after"), 0),
            (
                headers.get("x-ratelimit-reset")
                if headers.get("x-ratelimit-remaining") == "0"
                else None,
                now,
            ),
        ):
            try:
                delay = float(value) - offset
                if math.isfinite(delay) and 0 <= delay < 2**31:
                    waits.append(max(1, math.ceil(delay)))
            except (TypeError, ValueError, OverflowError):
                pass
        delay = max(waits, default=min(3600, 2 * previous[2]) if previous else 60)
        if previous:
            delay = max(delay, math.ceil(previous[0] - now))
        # Only rate-limited identities occupy the cache; discard abandoned limits.
        self._cooldowns = {
            key: value for key, value in self._cooldowns.items() if value[0] > now - 3600
        }
        self._cooldowns[bucket] = (now + delay, status, delay)
        return delay

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
        bucket = getattr(token, "bucket", None) or (
            ("token", hashlib.sha256(token.encode()).digest())
            if token is not None
            else ("origin", urlsplit(url).netloc)
        )
        previous_limit = self._cooldowns.get(bucket)
        self._check_rate_limit(bucket)
        headers = {"Accept": "application/json", "X-GitHub-Api-Version": API_VERSION}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        try:
            async with asyncio.timeout(10), self.connection() as client:
                async with client.stream(
                    method, url, headers=headers, json=payload, data=form, params=params
                ) as response:
                    retry_after = self._rate_limit(bucket, response, b"")
                    if retry_after is not None:
                        raise GitHubError(response.status_code, retry_after)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            raise GitHubError()
                    if response.status_code not in {200, 201, 204}:
                        raise GitHubError(
                            response.status_code, self._rate_limit(bucket, response, content)
                        )
                    if self._cooldowns.get(bucket) is previous_limit:
                        self._cooldowns.pop(bucket, None)
                    if response.status_code == 204:
                        return None
                    result = json.loads(content)
        except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError) as error:
            raise GitHubError() from error
        if not isinstance(result, (dict, list)):
            raise GitHubError()
        return result

    async def api(self, method, path, *, token=None, payload=None, params=None):
        # IDs are verified; workflow content paths may contain percent-escaped filenames.
        decoded = unquote(path)
        if (
            not re.fullmatch(r"/(?:[A-Za-z0-9_./-]|%[A-Fa-f0-9]{2})+", path)
            or any(segment in {".", ".."} for segment in decoded.split("/"))
            or "//" in decoded
            or re.search(r"[\x00-\x1f\x7f\\]", decoded)
        ):
            raise ValueError("Invalid GitHub API path")
        return await self._json(
            method,
            API_URL + path,
            token=token
            if token is not None
            else GitHubToken(self.app_token(), ("app", self.client_id)),
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
        bucket = ("installation", int(installation_id))
        self._check_rate_limit(bucket)
        result = await self.api(
            "POST",
            f"/app/installations/{int(installation_id)}/access_tokens",
            payload={"repository_ids": repository_ids, "permissions": permissions},
        )
        if not isinstance(result, dict) or not isinstance(result.get("token"), str):
            raise GitHubError()
        token = GitHubToken(result["token"], bucket)
        try:
            yield token
        finally:
            try:
                await self.api("DELETE", "/installation/token", token=token)
            except GitHubError:
                # Tokens expire at GitHub. Failed revocation must not hide the
                # result or error of the operation that used the scoped token.
                pass
