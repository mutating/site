"""Small smoke tests for the rendered site and its local assets."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
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


def test_tentacle_iframe_revision_matches_its_current_file_contents() -> None:
    """Prevent production from silently serving obsolete tentacle physics.

    The contact iframe is a static HTML asset and browsers/CDNs may retain a
    previously deployed response under an unchanged URL. That was a blind spot
    in the former tests: they navigated directly to the working-tree HTML and
    always exercised the newest code, while the real page could keep loading an
    older cached interaction whose impulse never ended.

    The page's ``v`` query must therefore equal the first twelve hexadecimal
    characters of the current file's SHA-256 digest. Any future physics edit
    changes the digest and makes this test fail until the iframe URL is bumped,
    guaranteeing that a deployed page requests the exact implementation covered
    by the test suite rather than a stale cached copy.
    """

    surface = PROJECT_ROOT / "public" / "muta-tentacles.html"
    page_source = (PROJECT_ROOT / "app" / "page.tsx").read_text(
        encoding="utf-8"
    )
    revision = hashlib.sha256(surface.read_bytes()).hexdigest()[:12]

    assert (
        f'/muta-tentacles.html?v={revision}&embedded=1"'
        in page_source
    )


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


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_animated_header_wordmark_preserves_the_eye_and_header_geometry(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Verify the supplied TMC wordmark animates without disturbing the eye.

    The new header artwork is intentionally a CSS-driven folding wordmark:
    its resting state shows only the anchors ``T``, ``M``, and ``C``; hovering
    reveals ``he``, ``utating``, and ``ompany`` over 350 ms while the supplied
    spectrum animation crosses the revealed letters. The complete expanded
    width is reserved before interaction so opening and closing the text must
    not move the existing eye, navigation, or header shell.

    This regression exercises both site themes because the source prototype
    used hard-coded black lettering while the integrated logo must inherit the
    site's light or dark foreground. It records the exact document-space
    rectangles of the eye, wordmark stage, navigation, and header before hover,
    waits for every reveal segment to reach its expanded state, and requires
    all four rectangles to remain unchanged. It also verifies that the
    gradient pseudo-element is running the original unfold animation and that
    moving the pointer away folds every segment back to zero width.

    The desktop wordmark and every revealed segment must inherit the requested
    35 px base size—exactly 2.5 times the former 14 px treatment—so the complete
    supplied effect scales as one unit instead of enlarging only the visible
    ``TMC`` anchors.

    The eye assertions are deliberate: replacing the textual half of the logo
    must never resize, reposition, recolor, or replace the independently
    animated gaze-and-blink component that the user explicitly asked to keep.
    """

    page = driver(
        stored_theme=theme,
        viewport=(1280, 720),
        load_interactive_visuals=False,
    )
    header = page.locator(".site-header")
    eye = page.locator(".animated-eye-logo")
    stage = page.locator(".brand__wordmark-stage")
    wordmark = page.locator(".brand__wordmark")
    navigation = page.locator(".main-nav")
    reveals = page.locator(".brand__word-reveal")

    expect(
        page.locator('.brand[href="#top"]')
    ).to_have_attribute(
        "aria-label",
        "The Mutating Company home",
    )
    assert wordmark.text_content().replace("\n", "").replace(" ", "") == (
        "TheMutatingCompany"
    )
    assert reveals.count() == 3

    initial = page.evaluate(
        """
        () => {
          const readRect = (selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [rect.x, rect.y, rect.width, rect.height];
          };
          const revealElements = [
            ...document.querySelectorAll(".brand__word-reveal"),
          ];
          const wordmark = document.querySelector(".brand__wordmark");
          return {
            header: readRect(".site-header"),
            eye: readRect(".animated-eye-logo"),
            stage: readRect(".brand__wordmark-stage"),
            navigation: readRect(".main-nav"),
            revealWidths: revealElements.map(
              (element) => element.getBoundingClientRect().width,
            ),
            anchorColor: getComputedStyle(
              document.querySelector(".brand__word-anchor"),
            ).color,
            wordmarkColor: getComputedStyle(wordmark).color,
            stageFontSize: getComputedStyle(
              document.querySelector(".brand__wordmark-stage"),
            ).fontSize,
            revealFontSizes: revealElements.map(
              (element) => getComputedStyle(element).fontSize,
            ),
          };
        }
        """
    )
    assert all(width <= 0.01 for width in initial["revealWidths"])
    assert initial["anchorColor"] == initial["wordmarkColor"]
    assert initial["stageFontSize"] == "35px"
    assert initial["revealFontSizes"] == ["35px", "35px", "35px"]

    wordmark.hover()
    page.wait_for_function(
        """
        () => [...document.querySelectorAll(".brand__word-reveal")].every(
          (element) =>
            element.getBoundingClientRect().width > 1 &&
            getComputedStyle(element).opacity === "1",
        )
        """
    )
    expanded = page.evaluate(
        """
        () => {
          const readRect = (selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [rect.x, rect.y, rect.width, rect.height];
          };
          const firstReveal = document.querySelector(".brand__word-reveal");
          return {
            header: readRect(".site-header"),
            eye: readRect(".animated-eye-logo"),
            stage: readRect(".brand__wordmark-stage"),
            navigation: readRect(".main-nav"),
            animationName: getComputedStyle(
              firstReveal,
              "::after",
            ).animationName,
          };
        }
        """
    )
    assert expanded["animationName"] == "brand-spectrum-unfold"
    for selector in ("header", "eye", "stage", "navigation"):
        for before, after in zip(initial[selector], expanded[selector]):
            assert abs(before - after) <= 0.5

    page.mouse.move(
        page.viewport_size["width"] - 1,
        page.viewport_size["height"] - 1,
    )
    page.wait_for_function(
        """
        () => [...document.querySelectorAll(".brand__word-reveal")].every(
          (element) => element.getBoundingClientRect().width <= 0.01,
        )
        """
    )


def test_animated_wordmark_keeps_the_existing_narrow_header_contract(
    driver: DriverFactory,
) -> None:
    """Keep the eye-only brand treatment at the smallest site breakpoint.

    The previous textual logo intentionally disappeared below 580 px so the
    theme control and mobile menu could coexist with the eye without wrapping
    or widening the sticky header. Replacing that text with an expandable logo
    must preserve the same responsive contract: the complete animated stage is
    hidden, the original eye remains visible at its existing compact geometry,
    and the brand link still exposes the full company name to assistive
    technology.

    This catches regressions where the collapsed ``TMC`` looks small enough but
    its reserved expansion width still pushes controls off-screen on a phone.
    """

    page = driver(
        stored_theme="dark",
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    stage = page.locator(".brand__wordmark-stage")
    eye = page.locator(".animated-eye-logo")
    brand = page.locator('.brand[href="#top"]')

    expect(stage).to_be_hidden()
    expect(eye).to_be_visible()
    expect(brand).to_have_attribute(
        "aria-label",
        "The Mutating Company home",
    )
    eye_box = eye.bounding_box()
    assert eye_box is not None
    assert abs(eye_box["width"] - 42) <= 0.5
    assert abs(eye_box["height"] - 39) <= 0.5


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


@pytest.mark.parametrize(
    "viewport",
    [
        (1280, 720),
        (390, 844),
    ],
)
def test_header_eye_tracks_pointer_inside_tentacle_iframe(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the header eye following the cursor over the tentacle surface.

    The contact artwork is an interactive iframe. Pointer events dispatched
    inside an iframe belong to its own window and never bubble into the parent
    document, while the header eye normally listens for ``pointermove`` on the
    parent window. Without an explicit coordinate bridge, the pupil therefore
    froze at its last target for the entire time the cursor crossed the
    tentacles—the exact defect this test is intended to prevent.

    The regression loads the real embedded tentacle document at both desktop
    and narrow responsive widths, scrolls that iframe into view, and dispatches
    two pointer moves on its actual canvas: first at one quarter of its width,
    then near its right edge. The child must relay normalized coordinates, the
    parent must translate them back into viewport coordinates, and the existing
    eye spring must consequently move the pupil rightward after both messages.
    Reverting either the child ``postMessage`` call, the parent's source-checked
    translation, or the eye's relayed-event listener makes the pupil remain
    stationary and causes this test to fail.

    The assertions compare direction and relative movement rather than exact
    frame timings, so GPU speed, display refresh rate, and WebGL rendering cost
    cannot change the expected result.
    """

    page = driver(
        stored_theme="dark",
        viewport=viewport,
    )
    frame = page.locator(".contact__tentacles")
    canvas = page.frame_locator(".contact__tentacles").locator("#muta")
    pupil = page.locator(".animated-eye-logo__pupil")

    frame.scroll_into_view_if_needed()
    expect(canvas).to_be_visible()

    def move_tentacle_pointer(normalized_x: float) -> None:
        canvas.evaluate(
            """
            (element, x) => {
              const rect = element.getBoundingClientRect();
              element.dispatchEvent(
                new PointerEvent("pointermove", {
                  bubbles: true,
                  pointerId: 71,
                  pointerType: "mouse",
                  isPrimary: true,
                  buttons: 0,
                  clientX: rect.left + rect.width * x,
                  clientY: rect.top + rect.height * 0.5,
                }),
              );
            }
            """,
            normalized_x,
        )

    move_tentacle_pointer(0.25)
    page.wait_for_function(
        """
        () =>
          Number.parseFloat(
            getComputedStyle(
              document.querySelector(".animated-eye-logo__pupil"),
            ).getPropertyValue("--gaze-x"),
          ) > 0.5
        """
    )
    first_position = pupil.evaluate(
        """
        (element) =>
          Number.parseFloat(
            getComputedStyle(element).getPropertyValue("--gaze-x"),
          )
        """
    )

    move_tentacle_pointer(0.9)
    page.wait_for_function(
        """
        (previousPosition) =>
          Number.parseFloat(
            getComputedStyle(
              document.querySelector(".animated-eye-logo__pupil"),
            ).getPropertyValue("--gaze-x"),
          ) > previousPosition + 0.5
        """,
        arg=first_position,
    )
