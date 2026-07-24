"""Browser regression coverage for the interactive tentacle physics clock."""

from __future__ import annotations

from collections.abc import Callable

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
