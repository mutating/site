"""Power Cell image routing; legacy agents only implement the bundled Ubuntu root."""

import re

RUNNER_IMAGE_FAMILY = "ghcr.io/actions/actions-runner"
IMAGE_ALIASES = {
    "ubuntu": "ubuntu:24.04",
    "actions": RUNNER_IMAGE_FAMILY + ":latest",
}
RUNNER_IMAGE = re.compile(
    re.escape(RUNNER_IMAGE_FAMILY) + r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[a-f0-9]{64})"
)


def supports_image(computer, image):
    if computer.runtime != "powercell-ubuntu-arm64-v1":
        return True
    if image == "ubuntu:24.04":
        return True
    return bool(
        RUNNER_IMAGE.fullmatch(image)
        and computer.network_enabled
        and RUNNER_IMAGE_FAMILY in (computer.capacity_report or {}).get("image_families", [])
    )
