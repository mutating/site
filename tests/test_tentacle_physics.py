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
def test_expired_click_impulse_is_not_replayed_during_slow_frame_catch_up(
    driver: DriverFactory,
    base_url: str,
    button: str,
    direction: str,
    held_multiplier: str,
) -> None:
    """Keep the original click impulse limited to its 41.5 ms wall-clock window.

    The source ``muta-tentacles.html`` gives a press one immediate strong
    attraction/repulsion impulse and lets its envelope expire after
    ``0.28 / 6.75`` seconds (about 41.5 ms). The adapted rectangular surface
    also splits a delayed rendered frame into short physics substeps. A prior
    implementation evaluated those substeps at synthetic timestamps in the
    past, replaying an already expired high-force impulse during catch-up and
    making a click feel much longer than in the source file.

    This regression test advances one animation frame by exactly 100 ms, then
    verifies two independent contracts for both left- and right-button presses:
    the configured envelope still equals the source's 41.5 ms, and no animated
    catch-up substep re-enters the impulse phase after that real-time window.
    The immediate input frame is intentionally excluded because it is the one
    strong impulse the original interaction is supposed to submit.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
    )
    page.goto(
        (
            f"{base_url}/muta-tentacles.html"
            "?max-tentacles=64&render-supersample=0.5"
            "&manual-animation-frame=1"
        ),
        wait_until="domcontentloaded",
    )
    canvas = page.locator("#muta")

    expect(canvas).to_be_visible(timeout=30_000)
    expect(canvas).to_have_attribute(
        "data-press-impulse-total-ms",
        "41.5",
    )

    page.mouse.move(120, 90)
    page.mouse.down(button=button)

    expect(canvas).to_have_attribute(
        "data-immediate-impulse-frame",
        "enabled",
        timeout=1_000,
    )
    page.evaluate(
        """
        () => {
          const canvas = document.querySelector("#muta");
          canvas.dataset.animationImpulseSubstepsSincePress = "0";
          window.__mutaTentaclesDebugFrameAfter(100);
        }
        """
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-phase",
        "held",
        timeout=1_000,
    )
    expect(canvas).to_have_attribute(
        "data-animation-impulse-substeps-since-press",
        "0",
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-force-multiplier",
        held_multiplier,
    )
    expect(canvas).to_have_attribute(
        "data-pointer-interaction-direction",
        direction,
    )

    page.mouse.up(button=button)


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
