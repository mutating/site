"""Read-only execution-token preflight for personal and company clients."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from throng_api import outputs as out
from throng_api.security import access_token_scheme, authenticate, company_access

router = APIRouter(
    prefix="/v1/auth", tags=["accounts"], dependencies=[Depends(access_token_scheme)]
)


@router.get(
    "/check",
    response_model=out.TokenCheck,
    summary="Check a token's read and execute permissions",
    description=(
        "Accepts personal and company tokens through the same authentication interface. "
        "Requires both read and execute scopes; company tokens also require the issuer's "
        "current execution.read and execution.run permissions in that company. "
        "Does not create resources, renew tokens, or check available compute capacity. "
        "Success and authentication/authorization failures use Cache-Control: no-store."
    ),
    responses={
        401: {
            "model": out.TokenAuthenticationError,
            "description": "token_expired for expired credentials; token_invalid for invalid credentials",
        },
        403: {"description": "Missing read/execute scope or organization execution permission"},
    },
)
async def check_token(request: Request, response: Response):
    try:
        async with request.app.state.db.read() as session:
            principal = await authenticate(session, request)
            if "execute" not in principal.scopes:
                raise HTTPException(403, "Token lacks required scope")
            if principal.company_id:
                for permission in ("execution.read", "execution.run"):
                    await company_access(
                        session, principal, principal.company_id, permission=permission
                    )
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
        raise
    response.headers["Cache-Control"] = "no-store"
    return {"valid": True, "scopes": sorted(principal.scopes)}
