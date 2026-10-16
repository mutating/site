"""GitHub account authorization and installation discovery for Actions routing."""

import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, update

from throng_api.github_client import GitHubClient, GitHubError
from throng_api.github_oidc import ActionsOIDC
from throng_api.models import CIRoute, GitHubAccount, GitHubOAuthFlow
from throng_api.security import access_token_scheme, authenticate, bearer, digest, personal

router = APIRouter(
    prefix="/v1/github", tags=["GitHub Actions"], dependencies=[Depends(access_token_scheme)]
)
public_router = APIRouter(prefix="/v1/github", tags=["GitHub authorization"])


class GitHubService:
    def __init__(self, settings):
        self.client = GitHubClient(
            settings.github_client_id,
            settings.github_client_secret,
            settings.github_private_key_path.read_bytes(),
        )
        self.cipher = Fernet(settings.github_token_key.encode())
        self.app_id = settings.github_app_id
        self.slug = settings.github_app_slug
        self.webhook_secret = settings.github_webhook_secret
        self.api_origin = self.origin(settings.github_public_api_url)
        self.dashboard_origin = self.origin(settings.github_dashboard_url)
        self.oidc = ActionsOIDC(self.client, self.api_origin + "/v1/github/ci")

    @staticmethod
    def origin(value):
        parts = urlsplit(value)
        if (
            not parts.hostname
            or (
                parts.scheme != "https"
                and not (
                    parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "GitHub public URLs must be HTTPS origins (HTTP loopback for development)"
            )
        return value.rstrip("/")

    def seal(self, value):
        return self.cipher.encrypt(json.dumps(value, separators=(",", ":")).encode()).decode()

    def unseal(self, value):
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, UnicodeError) as error:
            raise HTTPException(409, "Reconnect the GitHub account") from error

    def redirect(self, result):
        return RedirectResponse(
            self.dashboard_origin + "/actions?" + urlencode({"github": result}), status_code=303
        )


def configure(settings):
    values = (
        settings.github_app_id,
        settings.github_app_slug,
        settings.github_client_id,
        settings.github_client_secret,
        settings.github_private_key_path,
        settings.github_webhook_secret,
        settings.github_token_key,
    )
    if not any(values):
        return None
    if not all(values):
        raise ValueError(
            "Configure all GitHub App credentials, slug and persistent token encryption key"
        )
    return GitHubService(settings)


def service(request):
    result = request.app.state.github
    if result is None:
        raise HTTPException(503, "GitHub Actions integration is not configured")
    return result


def external_error(error):
    status = {401: 409, 403: 403, 404: 404, 429: 503}.get(error.status, 502)
    return HTTPException(status, "GitHub request failed; reconnect the account or retry later")


def save_tokens(integration, account, tokens, now):
    expires_in = tokens.get("expires_in")
    if expires_in is not None and (type(expires_in) is not int or not 0 < expires_in <= 31536000):
        raise GitHubError()
    account.tokens = integration.seal(tokens)
    account.expires_at = now + timedelta(seconds=expires_in) if expires_in else None


async def user_token(request, user_id):
    """Refresh rotating user tokens under a durable claim, outside the DB lock."""
    integration = service(request)
    now = request.app.state.clock()
    nonce = secrets.token_urlsafe(24)
    async with request.app.state.db.write() as session:
        account = await session.get(GitHubAccount, user_id)
        if not account:
            raise HTTPException(409, "Connect a GitHub account first")
        tokens = integration.unseal(account.tokens)
        if account.expires_at is None or account.expires_at > now + timedelta(seconds=60):
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            raise HTTPException(409, "Reconnect the GitHub account")
        if account.refresh_until and account.refresh_until > now:
            raise HTTPException(409, "GitHub authorization is being refreshed; retry shortly")
        account.refresh_nonce = nonce
        account.refresh_until = now + timedelta(seconds=30)
    try:
        renewed = await integration.client.oauth(refresh_token=tokens["refresh_token"])
        async with request.app.state.db.write() as session:
            account = await session.get(GitHubAccount, user_id)
            if not account or account.refresh_nonce != nonce:
                raise HTTPException(409, "GitHub connection changed; retry")
            save_tokens(integration, account, renewed, request.app.state.clock())
            account.refresh_nonce = None
            account.refresh_until = None
        return renewed["access_token"]
    finally:
        async with request.app.state.db.write() as session:
            account = await session.get(GitHubAccount, user_id)
            if account and account.refresh_nonce == nonce:
                account.refresh_nonce = None
                account.refresh_until = None


async def principal_user(request, scope="read"):
    async with request.app.state.db.read() as session:
        principal = await authenticate(session, request, scope)
        return personal(principal)


async def begin_flow(request, purpose):
    async with request.app.state.db.write() as session:
        user_id = personal(await authenticate(session, request, "manage"))
        integration = service(request)
        if purpose == "install" and not await session.get(GitHubAccount, user_id):
            raise HTTPException(409, "Connect a GitHub account first")
        now = request.app.state.clock()
        await session.execute(delete(GitHubOAuthFlow).where(GitHubOAuthFlow.expires_at <= now))
        # Bound abandoned flows per user without affecting other accounts.
        await session.execute(
            delete(GitHubOAuthFlow).where(
                GitHubOAuthFlow.user_id == user_id, GitHubOAuthFlow.purpose == purpose
            )
        )
        state = secrets.token_urlsafe(32)
        session.add(
            GitHubOAuthFlow(
                state_hash=digest(state),
                user_id=user_id,
                session_hash=digest(bearer(request)),
                purpose=purpose,
                expires_at=now + timedelta(minutes=10),
            )
        )
    return integration, state


async def consume_flow(request, state, purpose):
    async with request.app.state.db.write() as session:
        user_id = personal(await authenticate(session, request, "manage"))
        row = await session.get(GitHubOAuthFlow, digest(state))
        if (
            row is None
            or row.user_id != user_id
            or row.purpose != purpose
            or row.session_hash != digest(bearer(request))
            or row.expires_at <= request.app.state.clock()
        ):
            raise HTTPException(400, "Invalid or expired GitHub authorization state")
        await session.delete(row)
        return user_id


@router.get("/status")
async def connection_status(request: Request):
    """Connection metadata contains no OAuth tokens or App credentials."""
    user_id = await principal_user(request)
    async with request.app.state.db.read() as session:
        account = await session.get(GitHubAccount, user_id)
    return {
        "enabled": request.app.state.github is not None,
        "account": {"id": account.github_user_id, "login": account.login} if account else None,
    }


@router.post("/authorize")
async def authorize(request: Request):
    """Begin linking GitHub to the current platform account."""
    integration, state = await begin_flow(request, "authorize")
    return {
        "url": "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": integration.client.client_id,
                "redirect_uri": integration.api_origin + "/v1/github/oauth/callback",
                "state": state,
            }
        )
    }


@router.post("/installations/authorize")
async def authorize_installation(request: Request):
    """Begin an installation flow tied to the current authenticated session."""
    integration, state = await begin_flow(request, "install")
    return {
        "url": f"https://github.com/apps/{integration.slug}/installations/new?"
        + urlencode({"state": state})
    }


@public_router.get("/oauth/callback")
async def oauth_callback(
    request: Request,
    state: str = Query(min_length=1, max_length=200),
    code: str = Query(min_length=1, max_length=2000),
):
    integration = service(request)
    user_id = await consume_flow(request, state, "authorize")
    try:
        tokens = await integration.client.oauth(code=code)
        identity = await integration.client.api("GET", "/user", token=tokens["access_token"])
        if (
            not isinstance(identity, dict)
            or type(identity.get("id")) is not int
            or identity["id"] <= 0
            or not isinstance(identity.get("login"), str)
            or not 0 < len(identity["login"]) <= 100
        ):
            raise GitHubError()
        async with request.app.state.db.write() as session:
            # The initiating session may have been revoked during the exchange.
            if personal(await authenticate(session, request, "manage")) != user_id:
                raise HTTPException(400, "GitHub authorization session changed")
            account = await session.get(GitHubAccount, user_id)
            if account is None:
                account = GitHubAccount(user_id=user_id)
                session.add(account)
            account.github_user_id = identity["id"]
            account.login = identity["login"]
            save_tokens(integration, account, tokens, request.app.state.clock())
            account.refresh_nonce = None
            account.refresh_until = None
    except GitHubError as error:
        raise external_error(error) from error
    return integration.redirect("connected")


@public_router.get("/installations/callback")
async def installation_callback(
    request: Request,
    installation_id: int = Query(gt=0),
    state: str | None = Query(default=None, min_length=1, max_length=200),
    setup_action: str = Query(default="install", max_length=30),
):
    integration = service(request)
    if state is None:
        # Manual installation, including the initial sandbox setup, cannot link
        # an arbitrary installation ID to a platform account. The user signs in
        # and discovers their authorized installations through the Actions tab.
        return integration.redirect("installed")
    user_id = await consume_flow(request, state, "install")
    try:
        token = await user_token(request, user_id)
        result = await integration.client.api(
            "GET",
            f"/user/installations/{installation_id}/repositories",
            token=token,
            params={"per_page": 1},
        )
        if not isinstance(result, dict) or "repositories" not in result:
            raise GitHubError()
    except GitHubError as error:
        raise external_error(error) from error
    return integration.redirect("installed")


@router.get("/installations")
async def installations(request: Request, page: int = Query(default=1, ge=1, le=1000)):
    user_id = await principal_user(request)
    integration = service(request)
    try:
        token = await user_token(request, user_id)
        data = await integration.client.api(
            "GET", "/user/installations", token=token, params={"per_page": 100, "page": page}
        )
        return {
            "total_count": data["total_count"],
            "installations": [
                {
                    "id": row["id"],
                    "login": row["account"]["login"],
                    "repository_selection": row["repository_selection"],
                    "suspended": row.get("suspended_at") is not None,
                }
                for row in data["installations"]
                if row["app_id"] == integration.app_id
            ],
        }
    except GitHubError as error:
        raise external_error(error) from error


@router.get("/installations/{installation_id}/repositories")
async def repositories(
    request: Request, installation_id: int = Path(gt=0), page: int = Query(default=1, ge=1, le=1000)
):
    user_id = await principal_user(request)
    integration = service(request)
    try:
        token = await user_token(request, user_id)
        installation = await integration.client.api("GET", f"/app/installations/{installation_id}")
        if installation["app_id"] != integration.app_id or installation.get("suspended_at"):
            raise HTTPException(404, "GitHub installation not available")
        data = await integration.client.api(
            "GET",
            f"/user/installations/{installation_id}/repositories",
            token=token,
            params={"per_page": 100, "page": page},
        )
        return {
            "total_count": data["total_count"],
            "repositories": [
                {
                    "id": row["id"],
                    "full_name": row["full_name"],
                    "private": row["private"],
                    "default_branch": row["default_branch"],
                    "can_administer": row.get("permissions", {}).get("admin", False),
                }
                for row in data["repositories"]
            ],
        }
    except GitHubError as error:
        raise external_error(error) from error


@router.delete("/connection", status_code=204)
async def disconnect(request: Request):
    async with request.app.state.db.write() as session:
        user_id = personal(await authenticate(session, request, "manage"))
        await session.execute(
            update(CIRoute).where(CIRoute.created_by == user_id).values(enabled=False)
        )
        await session.execute(delete(GitHubOAuthFlow).where(GitHubOAuthFlow.user_id == user_id))
        await session.execute(delete(GitHubAccount).where(GitHubAccount.user_id == user_id))
