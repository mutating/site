"""Browser regression coverage for the interactive tentacle physics clock."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]


def test_slow_rendered_frames_are_split_into_short_physics_steps(
    driver: DriverFactory,
) -> None:
    """Keep a click impulse's real-world duration stable when rendering slows.

    The contact surface can fall below 30 frames per second at wide resolutions.
    Previously, a 100 ms rendered frame was truncated to 33 ms of simulated time,
    so only one third of the expected recovery happened and the click impulse
    appeared to linger. This test supplies 100 ms animation-frame timestamps and
    verifies that the real iframe consumes the full interval as four physics
    substeps, each no longer than the original 33 ms stability limit.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        animation_frame_interval_ms=100,
    )
    page.evaluate(
        """
        () => scrollTo({
          top: document.querySelector("#contact").offsetTop,
          behavior: "instant",
        })
        """
    )
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")

    expect(canvas).to_have_attribute(
        "data-physics-substeps",
        "4",
        timeout=30_000,
    )
    elapsed_milliseconds = float(
        canvas.get_attribute("data-physics-elapsed-ms") or "0"
    )

    assert 99.9 <= elapsed_milliseconds <= 100.0


@pytest.mark.parametrize(
    ("button", "direction", "held_multiplier"),
    [
        ("left", "1", "2.000"),
        ("right", "-1", "1.000"),
    ],
)
def test_mouse_impulse_transitions_to_the_correct_held_force(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
    held_multiplier: str,
) -> None:
    """Preserve the original two-stage interaction for both mouse buttons.

    Every press must first submit the original strong, wide impulse. After that
    short envelope expires while the button remains down, the left button must
    settle into attraction at exactly double strength, whereas the right button
    must settle into repulsion at normal strength. Releasing either button must
    return directly to ordinary hover attraction, without the extra post-release
    recovery phase that previously distorted the timing.

    Rendering density is reduced only for headless test speed; the production
    pointer and physics paths under test are unchanged.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")

    expect(canvas).to_be_visible(timeout=30_000)
    page.mouse.move(120, 90)
    page.mouse.down(button=button)

    expect(canvas).to_have_attribute(
        "data-last-impulse-peak-force-multiplier",
        "13.333",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-last-impulse-direction",
        direction,
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        held_multiplier,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-radius-multiplier",
        "1.000",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        direction,
    )

    page.mouse.up(button=button)

    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "hover",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        "1.000",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        "0",
    )
