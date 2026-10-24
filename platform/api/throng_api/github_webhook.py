"""Signed, deduplicated GitHub lifecycle notifications; never trust redirects as events."""

import hashlib
import hmac
import re
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import and_, delete, or_, select

from throng_api.ci import ACTIVE
from throng_api.ci_controller import close_github, mark_running
from throng_api.ci_policy import record_revision
from throng_api.device_security import strict_json
from throng_api.github import disconnect_user, service
from throng_api.models import CIReservation, CIRoute, GitHubAccount, GitHubDelivery
from throng_api.powercell import bounded_body

router = APIRouter(prefix="/v1/github", tags=["GitHub webhooks"])


@router.post("/webhook", status_code=202)
async def webhook(request: Request):
    integration = service(request)
    body = await bounded_body(request, 2 * 1024 * 1024)
    expected = (
        "sha256=" + hmac.new(integration.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(
        expected.encode(), request.headers.get("x-hub-signature-256", "").encode()
    ):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    delivery = request.headers.get("x-github-delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery):
        raise HTTPException(400, "Invalid GitHub webhook delivery ID")
    try:
        payload = strict_json(body)
        if not isinstance(payload, dict):
            raise ValueError()
        for name in ("installation", "repository", "workflow_job", "workflow_run", "sender"):
            if name in payload and not isinstance(payload[name], dict):
                raise ValueError()
        removed = payload.get("repositories_removed", [])
        if not isinstance(removed, list) or any(not isinstance(row, dict) for row in removed):
            raise ValueError()
    except ValueError as error:
        raise HTTPException(400, "Invalid GitHub webhook payload") from error
    checksum = hashlib.sha256(body).hexdigest()
    async with request.app.state.db.write() as session:
        now = request.app.state.clock()
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
        if event == "github_app_authorization" and action == "revoked":
            sender = payload.get("sender", {}).get("id")
            if type(sender) is int and sender > 0:
                account = await session.scalar(
                    select(GitHubAccount).where(GitHubAccount.github_user_id == sender)
                )
                if account:
                    await disconnect_user(session, account.user_id)
        elif event in {"installation", "installation_repositories"}:
            routes = (
                await session.scalars(
                    select(CIRoute).where(CIRoute.installation_id == installation)
                )
            ).all()
            removed = {row.get("id") for row in payload.get("repositories_removed", [])}
            for route in routes:
                if route.enabled and (
                    action in {"deleted", "suspend"} or route.repository_id in removed
                ):
                    route.enabled = False
                    record_revision(session, route, None, "disabled", now)
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
                            or_(
                                CIReservation.status.in_(ACTIVE),
                                and_(
                                    CIReservation.status == "completed",
                                    CIReservation.reason == "runner_exited",
                                ),
                                and_(
                                    event == "workflow_job" and action == "completed",
                                    CIReservation.status.in_({"completed", "cancelled"}),
                                    CIReservation.reason.startswith("github_", autoescape=True),
                                ),
                            ),
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
                        if event == "workflow_job":
                            row.started_at = row.started_at or row.finished_at or now
                        close_github(row, item.get("conclusion", "completed"), now)
                    elif (
                        event == "workflow_job"
                        and action == "in_progress"
                        and row.status in {"ready", "selected"}
                    ):
                        route = await session.get(CIRoute, row.route_id)
                        await mark_running(session, row, route, now)
    return {"accepted": True}
