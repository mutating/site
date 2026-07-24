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


def test_click_impulse_recovery_has_a_strict_short_tail(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Prevent the tentacle click impulse from appearing to linger.

    The force envelope itself is very short, but it can leave the simulated
    chains displaced after the mouse button is released. The defect reported
    on the contact surface was that this residual deformation kept settling
    under the normal, slow ambient physics, so the click looked active for far
    longer than its configured impulse. The surface now exposes the bounded
    post-release recovery phase used to remove that visual tail. This test
    performs a real click inside the production WebGL surface and verifies that
    recovery completes within 300 ms, including headless-renderer overhead.
    Animation frames are deliberately stopped as soon as the pointer goes down:
    without the independent recovery timer, the clicked frame would remain
    visible forever and the completion attribute would never be written.
    The test lowers rendering density and limits generated tentacles so
    Chromium's headless software renderer cannot hide JavaScript timing behind
    multi-second GPU stalls; the click and recovery paths remain unchanged.
    """

    page = driver(
        stored_theme="dark",
        viewport=(240, 180),
        pause_animation_frames_on_pointerdown=True,
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
    canvas.click(
        position={"x": 190, "y": 130},
        force=True,
    )
    expect(canvas).to_have_attribute(
        "data-impulse-recovery-active",
        "false",
        timeout=1_000,
    )

    recovery_milliseconds = float(
        canvas.get_attribute("data-last-impulse-recovery-ms") or "inf"
    )

    assert 160 <= recovery_milliseconds <= 300
