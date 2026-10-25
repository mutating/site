import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyCookie, HTTPBearer
from sqlalchemy import String, and_, cast, or_, select
from starlette.concurrency import run_in_threadpool

from throng_api.models import (
    AccessToken,
    AgentCredential,
    Computer,
    DeviceCertificate,
    LegacyCredential,
    LoginSession,
    Membership,
    TokenOwnership,
    User,
)
from throng_api.permissions import USER_PERMISSIONS, effective_permissions, request_permission
from throng_api.transport import ProtocolError

PASSWORDS = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
SCOPES = frozenset({"read", "manage", "execute"})
REQUEST_PERMISSION = object()
SESSION_COOKIE = "tmc_session"

# These dependencies describe and parse bearer headers. Database-backed checks
# remain in authenticate()/agent_auth(), inside each endpoint's transaction.
user_bearer_scheme = HTTPBearer(
    scheme_name="AccessToken",
    auto_error=False,
    description=(
        "Личный API-токен (SELF-…) или токен организации (ORG-…). "
        "Выпустите его через /v1/api-tokens либо /v1/access-tokens. "
        "Также принимается access_token сессии из POST /v1/auth/login. "
        "В Authorize вставьте только сам токен, без префикса Bearer."
    ),
)
browser_cookie_scheme = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="BrowserSession",
    auto_error=False,
    description="Host-only HttpOnly cookie issued by POST /v1/auth/session.",
)


async def access_token_scheme(
    token=Depends(user_bearer_scheme), cookie=Depends(browser_cookie_scheme)
):
    return token or cookie


agent_credential_scheme = HTTPBearer(
    scheme_name="AgentCredential",
    auto_error=False,
    description=(
        "Отдельный credential компьютера (agt_…), выданный через "
        "POST /v1/computers/{id}/credentials. Вставьте только сам токен. "
        "После создания сессии также передавайте её ID в X-Agent-Session."
    ),
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


async def hash_password(value: str) -> str:
    return await run_in_threadpool(PASSWORDS.hash, value)


async def verify_password(encoded: str, value: str) -> bool:
    try:
        return await run_in_threadpool(PASSWORDS.verify, encoded, value)
    except VerificationError:
        return False


def bearer(request: Request) -> str:
    if "authorization" not in request.headers and request.url.path.startswith("/v1/"):
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            return cookie
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    return token


@dataclass(frozen=True)
class Principal:
    user_id: str | None
    company_id: str | None
    actor_id: str
    scopes: frozenset[str]
    permission: str | None = None

    @property
    def key(self):
        return f"user:{self.user_id}" if self.user_id else f"company:{self.company_id}"


async def authenticate(session, request: Request, scope="read") -> Principal:
    try:
        raw = bearer(request)
    except HTTPException as error:
        raise ProtocolError(
            "token_invalid", error.detail, status_code=401, headers=error.headers
        ) from error
    hashed = digest(raw)
    token = await session.scalar(select(LoginSession).where(LoginSession.session_hash == hashed))
    if token:
        user_id, company_id, actor_id = token.user_id, None, token.user_id
    else:
        token = await session.scalar(select(AccessToken).where(AccessToken.token_hash == hashed))
        if token is None:
            token = await session.scalar(
                select(LegacyCredential).where(LegacyCredential.token_hash == hashed)
            )
        if token is None:
            raise ProtocolError("token_invalid", "Invalid token", status_code=401)
        owner = await session.get(TokenOwnership, token.ownership_id)
        if not owner:
            raise ProtocolError("token_invalid", "Invalid token owner", status_code=401)
        user_id, company_id, actor_id = owner.user_id, owner.company_id, token.created_by_user_id
        if company_id:
            issuer = await session.scalar(
                select(Membership).where(
                    Membership.user_id == actor_id,
                    Membership.company_id == company_id,
                )
            )
            if not issuer or "tokens.manage" not in effective_permissions(
                issuer.role, issuer.permissions
            ):
                raise ProtocolError(
                    "token_invalid",
                    "Token issuer no longer administers this company",
                    status_code=401,
                )
    if token.revoked_at:
        raise ProtocolError("token_invalid", "Invalid token", status_code=401)
    if not await session.get(User, actor_id):
        raise ProtocolError("token_invalid", "Invalid token issuer", status_code=401)
    if token.expires_at is not None and token.expires_at <= request.app.state.clock():
        raise ProtocolError("token_expired", "Token has expired", status_code=401)
    if scope not in token.scopes:
        raise HTTPException(403, "Token lacks required scope")
    return Principal(
        user_id,
        company_id,
        actor_id,
        frozenset(token.scopes),
        request_permission(request, scope),
    )


def personal(principal):
    if not principal.user_id:
        raise HTTPException(403, "A personal token is required")
    return principal.user_id


async def system_administrator(session, principal):
    if not principal.user_id:
        raise HTTPException(403, "A personal system administrator token is required")
    user = await session.get(User, principal.user_id)
    if not user or not user.is_system_admin:
        raise HTTPException(403, "System administrator required")


async def membership_role(session, user_id, company_id):
    return await session.scalar(
        select(Membership.role).where(
            Membership.user_id == user_id, Membership.company_id == company_id
        )
    )


async def company_access(
    session, principal, company_id, admin=False, permission=REQUEST_PERMISSION
):
    company_id = str(company_id)
    if principal.company_id:
        if principal.company_id != company_id:
            raise HTTPException(404, "Company not found")
    member = await session.scalar(
        select(Membership).where(
            Membership.user_id == (principal.user_id or principal.actor_id),
            Membership.company_id == company_id,
        )
    )
    if member is None:
        raise HTTPException(404, "Company not found")
    required = principal.permission if permission is REQUEST_PERMISSION else permission
    if admin and required and required.endswith(".read"):
        required = (
            "execution.run"
            if required == "execution.read"
            else required.replace(".read", ".manage")
        )
    if required:
        if required not in effective_permissions(member.role, member.permissions):
            raise HTTPException(403, "Organization permission required")
    elif admin and member.role not in {"creator", "admin"}:
        raise HTTPException(403, "Company administrator required")


async def owner_access(session, principal, owner, admin=False):
    if owner.company_id:
        await company_access(session, principal, owner.company_id, admin=admin)
    elif not principal.user_id or principal.user_id != str(owner.user_id):
        raise HTTPException(404, "Resource not found")


async def owner_values(session, principal, company_id=None, admin=False):
    company_id = str(company_id) if company_id else principal.company_id
    if company_id:
        await company_access(session, principal, company_id, admin=admin)
        return {"user_id": None, "company_id": company_id}
    return {"user_id": personal(principal), "company_id": None}


def membership_permission(permission):
    """Mirror effective_permissions in SQL using exact quoted enum values in either JSON store."""
    custom = cast(Membership.permissions, String)
    # Explicit JSON null and historical SQL NULL both select the role defaults.
    defaults = or_(custom.is_(None), custom == "null")
    return or_(
        Membership.role == "creator",
        custom.contains(f'"{permission}"'),
        and_(defaults, True if permission in USER_PERMISSIONS else Membership.role == "admin"),
    )


def owned_filter(model, principal):
    """Authorize live membership and owner in the same statement that reads resource data."""
    companies = select(Membership.company_id).where(
        Membership.user_id == (principal.user_id or principal.actor_id)
    )
    if principal.permission:
        companies = companies.where(membership_permission(principal.permission))
    if principal.company_id:
        companies = companies.where(membership_permission("tokens.manage"))
        return and_(model.company_id == principal.company_id, model.company_id.in_(companies))
    return or_(
        model.user_id == principal.user_id,
        and_(model.company_id.is_not(None), model.company_id.in_(companies)),
    )


async def owned(session, model, resource_id, principal, admin=False):
    row = await session.get(model, str(resource_id))
    if row is None:
        raise HTTPException(404, "Resource not found")
    await owner_access(session, principal, row, admin=admin)
    return row


async def issue_token(session, owner, actor_id, name, scopes, expires_at):
    prefix = "ORG-" if owner.company_id else "SELF-"
    raw = prefix + secrets.token_hex(32)
    token = AccessToken(
        ownership_id=owner.id,
        created_by_user_id=actor_id,
        name=name,
        scopes=sorted(scopes),
        token_hash=digest(raw),
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()
    return token, raw


async def agent_auth(session, request, require_session=True):
    credential = await session.scalar(
        select(AgentCredential).where(AgentCredential.token_hash == digest(bearer(request)))
    )
    if not credential:
        raise HTTPException(401, "Invalid agent credential")
    computer = await session.get(Computer, credential.computer_id)
    if not computer:
        raise HTTPException(401, "Invalid agent credential")
    if computer.runtime == "powercell-ubuntu-arm64-v1" and not computer.certificate_sha256:
        raise HTTPException(401, "Power Cell requires attested enrollment")
    if computer.certificate_sha256:
        certificate = await session.get(DeviceCertificate, computer.certificate_sha256)
        if (
            getattr(request.state, "device_certificate", None) != computer.certificate_sha256
            or not certificate
            or certificate.revoked_at
            or certificate.expires_at <= request.app.state.clock()
        ):
            raise HTTPException(401, "A current device possession proof is required")
    # Disabled computers may finish and clean up work, but receive no new isolates.
    if require_session:
        if (
            not computer.online
            or not computer.session_id
            or request.headers.get("x-agent-session") != computer.session_id
            or computer.lease_expires_at <= request.app.state.clock()
        ):
            from throng_api.transport import ProtocolError

            raise ProtocolError(
                "session_expired", "Agent session expired; a clean start is required"
            )
    return computer


def renew_agent(computer, now, settings):
    computer.last_seen_at = now
    computer.lease_expires_at = now + timedelta(seconds=settings.agent_lease_seconds)
