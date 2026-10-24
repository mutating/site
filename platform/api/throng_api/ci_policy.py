"""Shared binding policy and immutable audit snapshots, independent of HTTP routes."""

import re

from throng_api.models import CIRouteRevision

WORKFLOW = r"^(?=.{1,250}$)\.github/workflows/[^/\\\x00-\x1f\x7f-\x9f\u2028\u2029]+\.ya?ml$"
CONFIGURATION_FIELDS = (
    "repository",
    "repository_owner_id",
    "workflow_path",
    "workflow_paths",
    "workflow_profiles",
    "ref",
    "job_key",
    "company_id",
    "user_id",
    "computer_ids",
    "cpu_millis",
    "memory_bytes",
    "disk_bytes",
    "preparation_seconds",
    "timeout_seconds",
    "max_parallel",
    "enabled",
    "repository_binding",
)


def workflow_allowed(route, path):
    """An all-workflow policy still requires a real workflow path in this repository."""
    if not isinstance(path, str) or not re.fullmatch(WORKFLOW, path):
        return False
    paths = getattr(route, "workflow_paths", None)
    return path == route.workflow_path if paths is None else not paths or path in paths


def authorizer(route):
    """Editing a shared binding changes its current grant, preserving its original author."""
    return getattr(route, "authorized_by", None) or route.created_by


def snapshot(route):
    return {
        name: getattr(route, name)
        for name in CONFIGURATION_FIELDS
        if route.workflow_paths is None or name not in {"workflow_path", "job_key"}
    }


def record_revision(session, route, actor, action, now, *, advance=True):
    if advance:
        route.revision += 1
    session.add(
        CIRouteRevision(
            route_id=route.id,
            revision=route.revision,
            actor_id=actor,
            action=action,
            configuration=snapshot(route),
            created_at=now,
        )
    )


def workflow_run_allowed(route, path, workflow_path=None):
    """Use the signed filename to distinguish its @ characters from GitHub's optional ref suffix."""
    expected = workflow_path or route.workflow_path
    return workflow_allowed(route, expected) and (
        path == expected or path.startswith(expected + "@")
    )
