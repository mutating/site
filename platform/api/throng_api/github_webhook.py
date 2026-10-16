"""Signed, deduplicated GitHub lifecycle notifications; never trust redirects as events."""

import hashlib
import hmac
import re
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select

from throng_api.ci import ACTIVE
from throng_api.ci_controller import close, mark_running
from throng_api.device_security import strict_json
from throng_api.github import service
from throng_api.models import CIReservation, CIRoute, GitHubDelivery
from throng_api.powercell import bounded_body

router = APIRouter(prefix="/v1/github", tags=["GitHub webhooks"])


@router.post("/webhook", status_code=202)
async def webhook(request: Request):
    integration = service(request)
    body = await bounded_body(request, 2 * 1024 * 1024)
    expected = (
        "sha256=" + hmac.new(integration.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(expected, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    delivery = request.headers.get("x-github-delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery):
        raise HTTPException(400, "Invalid GitHub webhook delivery ID")
    try:
        payload = strict_json(body)
        if not isinstance(payload, dict):
            raise ValueError()
        for name in ("installation", "repository", "workflow_job", "workflow_run"):
            if name in payload and not isinstance(payload[name], dict):
                raise ValueError()
        removed = payload.get("repositories_removed", [])
        if not isinstance(removed, list) or any(not isinstance(row, dict) for row in removed):
            raise ValueError()
    except ValueError as error:
        raise HTTPException(400, "Invalid GitHub webhook payload") from error
    checksum = hashlib.sha256(body).hexdigest()
    now = request.app.state.clock()
    async with request.app.state.db.write() as session:
        previous = await session.get(GitHubDelivery, delivery)
        if previous:
            if previous.payload_hash != checksum:
                raise HTTPException(409, "GitHub delivery payload changed")
            return {"accepted": True}
        session.add(GitHubDelivery(id=delivery, payload_hash=checksum, created_at=now))
        await session.execute(
            delete(GitHubDelivery).where(GitHubDelivery.created_at < now - timedelta(days=7))
        )
        event, action = request.headers.get("x-github-event"), payload.get("action")
        installation = payload.get("installation", {}).get("id")
        if event in {"installation", "installation_repositories"}:
            routes = (
                await session.scalars(
                    select(CIRoute).where(CIRoute.installation_id == installation)
                )
            ).all()
            removed = {row.get("id") for row in payload.get("repositories_removed", [])}
            for route in routes:
                if action in {"deleted", "suspend"} or route.repository_id in removed:
                    route.enabled = False
        elif event in {"workflow_run", "workflow_job"}:
            repository = payload.get("repository", {}).get("id")
            item = payload.get(event, {})
            run_id = item.get("run_id") if event == "workflow_job" else item.get("id")
            attempt = item.get("run_attempt")
            if type(run_id) is int and type(attempt) is int:
                rows = (
                    await session.scalars(
                        select(CIReservation)
                        .join(CIRoute, CIRoute.id == CIReservation.route_id)
                        .where(
                            CIRoute.repository_id == repository,
                            CIRoute.installation_id == installation,
                            CIReservation.run_id == run_id,
                            CIReservation.run_attempt == attempt,
                            CIReservation.status.in_(ACTIVE),
                        )
                    )
                ).all()
                for row in rows:
                    if event == "workflow_job" and (
                        item.get("runner_id") != row.runner_id
                        or item.get("runner_name") != row.label
                        or row.runner_id is None
                    ):
                        continue
                    if action == "completed":
                        close(
                            row,
                            "cancelled" if item.get("conclusion") == "cancelled" else "completed",
                            "github_" + str(item.get("conclusion", "completed"))[:50],
                            now,
                        )
                    elif (
                        event == "workflow_job"
                        and action == "in_progress"
                        and row.status in {"ready", "selected"}
                    ):
                        route = await session.get(CIRoute, row.route_id)
                        await mark_running(session, row, route, now)
    return {"accepted": True}
