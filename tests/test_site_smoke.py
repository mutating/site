"""Small smoke tests for the rendered site and its local assets."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, expect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DriverFactory = Callable[..., Page]


def test_home_page_renders_core_content(driver: DriverFactory) -> None:
    """Verify that the local application renders its primary content and controls."""

    page = driver(color_scheme="light")

    expect(
        page.get_by_role(
            "heading",
            name="AI systems that doubt.",
            exact=True,
        )
    ).to_be_visible()
    expect(
        page.get_by_role(
            "button",
            name="Toggle color theme",
            exact=True,
        )
    ).to_be_visible()
    expect(
        page.get_by_role(
            "link",
            name="tokamak",
            exact=True,
        )
    ).to_have_attribute(
        "href",
        "https://en.wikipedia.org/wiki/Tokamak",
    )


def test_site_critical_assets_are_local_and_non_empty() -> None:
    """Verify that every critical visual asset is present in the repository."""

    required_assets = [
        "public/muta-eye-surface.webp",
        "public/muta-v2-transparent.png",
        "public/mutant-mascot-transparent.png",
        "public/mutant-mascot.jpg",
        "public/og.png",
        "public/tokamak-reactor.html",
        "public/muta-tentacles.html",
    ]

    for relative_path in required_assets:
        asset = PROJECT_ROOT / relative_path
        assert asset.is_file(), f"{relative_path} must be a file"
        assert asset.stat().st_size > 0, f"{relative_path} must not be empty"


def test_ticker_selection_inverts_only_its_own_colors(
    driver: DriverFactory,
) -> None:
    """Verify that ticker selection inverts pink and white only within the strip."""

    page = driver(stored_theme="light")
    selection_styles = page.locator(".ticker__track").evaluate(
        """
        (track) => {
          const ticker = track.closest(".ticker");
          const tickerStyle = getComputedStyle(ticker);
          const tickerSelection = getComputedStyle(track, "::selection");
          const globalSelection = getComputedStyle(document.body, "::selection");

          return {
            tickerColor: tickerStyle.color,
            tickerBackground: tickerStyle.backgroundColor,
            selectionColor: tickerSelection.color,
            selectionBackground: tickerSelection.backgroundColor,
            globalColor: globalSelection.color,
            globalBackground: globalSelection.backgroundColor,
          };
        }
        """
    )

    assert selection_styles["selectionColor"] == selection_styles["tickerBackground"]
    assert selection_styles["selectionBackground"] == selection_styles["tickerColor"]
    assert selection_styles["globalColor"] == selection_styles["tickerColor"]
    assert selection_styles["globalBackground"] == selection_styles["tickerBackground"]


def test_header_eye_whitens_with_a_black_outline_without_changing_geometry(
    driver: DriverFactory,
) -> None:
    """Verify the scrolled eye becomes white, not gray, while retaining its shape.

    The header eye used to apply ``grayscale()`` to the complete logo as the
    page moved down. That produced a dull gray eyeball and also recolored the
    pupil. The intended behavior is narrower: over the first viewport of
    scrolling, only the yellow eyeball must reach pure white and acquire a thin
    black outline. The pupil and the logo's element dimensions must remain
    unchanged, and hovering the eye must still animate it back to its original
    yellow state before the scroll-dependent white state returns on mouse-out.
    """

    page = driver(stored_theme="light")
    eye = page.locator(".animated-eye-logo")
    surface = page.locator(".animated-eye-logo__surface")
    outline = page.locator(".animated-eye-logo__outline")
    pupil = page.locator(".animated-eye-logo__pupil")

    initial_geometry = eye.evaluate(
        """
        (element) => {
          const surface = element.querySelector(".animated-eye-logo__surface");
          const pupil = element.querySelector(".animated-eye-logo__pupil");
          const eyeRect = element.getBoundingClientRect();
          const surfaceRect = surface.getBoundingClientRect();
          const pupilRect = pupil.getBoundingClientRect();
          return {
            eye: [eyeRect.width, eyeRect.height],
            surface: [surfaceRect.width, surfaceRect.height],
            pupil: [pupilRect.width, pupilRect.height],
          };
        }
        """
    )

    assert surface.evaluate("(element) => getComputedStyle(element).backgroundColor") == (
        "rgb(255, 210, 10)"
    )
    assert outline.evaluate("(element) => getComputedStyle(element).opacity") == "0"
    initial_pupil_color = pupil.evaluate(
        "(element) => getComputedStyle(element).backgroundColor"
    )

    page.evaluate("window.scrollTo(0, window.innerHeight)")
    page.wait_for_function(
        """
        () => {
          const surface = document.querySelector(".animated-eye-logo__surface");
          const outline = document.querySelector(".animated-eye-logo__outline");
          return (
            getComputedStyle(surface).backgroundColor === "rgb(255, 255, 255)" &&
            getComputedStyle(outline).opacity === "1"
          );
        }
        """
    )

    assert eye.evaluate("(element) => getComputedStyle(element).filter") == "none"
    assert outline.evaluate(
        "(element) => getComputedStyle(element).backgroundColor"
    ) == "rgb(0, 0, 0)"
    assert pupil.evaluate(
        "(element) => getComputedStyle(element).backgroundColor"
    ) == initial_pupil_color

    scrolled_geometry = eye.evaluate(
        """
        (element) => {
          const surface = element.querySelector(".animated-eye-logo__surface");
          const pupil = element.querySelector(".animated-eye-logo__pupil");
          const eyeRect = element.getBoundingClientRect();
          const surfaceRect = surface.getBoundingClientRect();
          const pupilRect = pupil.getBoundingClientRect();
          return {
            eye: [eyeRect.width, eyeRect.height],
            surface: [surfaceRect.width, surfaceRect.height],
            pupil: [pupilRect.width, pupilRect.height],
          };
        }
        """
    )
    assert scrolled_geometry == initial_geometry

    eye.hover()
    page.wait_for_function(
        """
        () => {
          const surface = document.querySelector(".animated-eye-logo__surface");
          const outline = document.querySelector(".animated-eye-logo__outline");
          return (
            getComputedStyle(surface).backgroundColor === "rgb(255, 210, 10)" &&
            getComputedStyle(outline).opacity === "0"
          );
        }
        """
    )

    page.mouse.move(page.viewport_size["width"] - 1, page.viewport_size["height"] - 1)
    page.wait_for_function(
        """
        () =>
          getComputedStyle(
            document.querySelector(".animated-eye-logo__surface"),
          ).backgroundColor === "rgb(255, 255, 255)"
        """
    )


def test_header_eye_gaze_speed_is_stable_when_animation_frames_are_delayed(
    driver: DriverFactory,
) -> None:
    """Verify the pupil does not become sluggish when rendering loses frames.

    The original gaze spring applied fixed acceleration and damping once per
    animation callback. Its apparent speed therefore depended on refresh rate:
    if compositing the eye, scrolling, resizing, or other page visuals reduced
    the callback rate from 60 Hz to 30 Hz, the pupil covered much less distance
    during the same elapsed time and felt noticeably heavy. This regression
    test drives two identical pages with synthetic 60 Hz and 30 Hz animation
    timestamps, moves the pointer to the same target, and samples each pupil
    after approximately 150 milliseconds. Both must reach nearly the same
    horizontal position, proving that gaze physics follows elapsed time rather
    than the number of delivered frames.
    """

    cases = (
        (1000 / 60, 9),
        (1000 / 30, 5),
    )
    sampled_positions: list[float] = []

    for frame_interval, frame_count in cases:
        page = driver(
            stored_theme="dark",
            viewport=(1280, 720),
            animation_frame_interval_ms=frame_interval,
            load_interactive_visuals=False,
        )
        sampled_position = page.evaluate(
            """
            async ({ frameCount }) => {
              const eye = document.querySelector(".animated-eye-logo");
              const pupil = document.querySelector(".animated-eye-logo__pupil");
              const eyeRect = eye.getBoundingClientRect();

              window.dispatchEvent(
                new PointerEvent("pointermove", {
                  bubbles: true,
                  clientX: innerWidth - 1,
                  clientY: eyeRect.top + eyeRect.height / 2,
                }),
              );

              for (let frame = 0; frame < frameCount; frame += 1) {
                await new Promise((resolve) => requestAnimationFrame(resolve));
              }

              return Number.parseFloat(
                getComputedStyle(pupil).getPropertyValue("--gaze-x"),
              );
            }
            """,
            {"frameCount": frame_count},
        )
        sampled_positions.append(sampled_position)

    sixty_hz_position, thirty_hz_position = sampled_positions
    assert sixty_hz_position > 5
    assert thirty_hz_position > 5
    assert abs(sixty_hz_position - thirty_hz_position) <= 1
