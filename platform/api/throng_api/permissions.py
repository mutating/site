"""Organization permissions layered under API-token scopes and live membership."""

from typing import Literal, get_args

Permission = Literal[
    "members.read",
    "members.manage",
    "resources.read",
    "resources.manage",
    "execution.read",
    "execution.run",
    "tokens.read",
    "tokens.manage",
    "company.manage",
]
PERMISSIONS = frozenset(get_args(Permission))
USER_PERMISSIONS = frozenset(
    {
        "members.read",
        "resources.read",
        "execution.read",
        "execution.run",
        "tokens.read",
    }
)


def effective_permissions(role, custom=None):
    if role == "creator":
        return PERMISSIONS
    if custom is not None:
        return frozenset(custom)
    return PERMISSIONS if role == "admin" else USER_PERMISSIONS


def request_permission(request, scope):
    """Classify protected organization data; token scopes remain an independent check."""
    parts = request.url.path.removeprefix("/v1/").split("/")
    resource = parts[0]
    if resource == "memberships":
        return "members.manage" if scope == "manage" else "members.read"
    if (
        resource
        in {
            "computers",
            "computer-ownerships",
            "computer-resources",
            "agent-enrollments",
            "benchmark-results",
        }
        or "resources" in parts
    ):
        return "resources.manage" if scope == "manage" else "resources.read"
    if resource in {"isolates", "commands", "operations", "artifacts"}:
        return "execution.run" if scope == "execute" else "execution.read"
    if resource in {"access-tokens", "token-ownerships"}:
        return "tokens.manage" if scope == "manage" else "tokens.read"
    if resource == "companies" and request.method == "PATCH":
        return "company.manage"
    return None
