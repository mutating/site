"""Browser tests that guard the page layout while its color theme changes."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]
Rect = dict[str, float]

LAYOUT_SELECTORS = (
    ".site-header",
    ".hero",
    ".mascot-card",
    ".ticker",
    "#complexity",
    "#containment",
    ".tokamak-model",
    "#thesis",
    "#what-we-build",
    "#scale",
    "#feedback",
    "#the-deal",
    "#open-source",
    "#contact",
    ".contact__clip",
    ".contact__tentacles",
    ".contact__copy",
    ".site-footer",
)


def _read_layout(page: Page) -> dict[str, Rect]:
    """Return document-space rectangles for every primary page block."""

    return page.evaluate(
        """
        (selectors) => Object.fromEntries(
          selectors.map((selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [
              selector,
              {
                x: rect.x,
                y: rect.y + window.scrollY,
                width: rect.width,
                height: rect.height,
              },
            ];
          }),
        )
        """,
        LAYOUT_SELECTORS,
    )


def _click_theme_toggle(page: Page, expected_theme: str) -> None:
    """Click the fixed toggle at its visible coordinates without auto-scrolling."""

    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    bounds = toggle.bounding_box()
    assert bounds is not None

    page.mouse.click(
        bounds["x"] + bounds["width"] / 2,
        bounds["y"] + bounds["height"] / 2,
    )
    expect(page.locator("html")).to_have_attribute("data-theme", expected_theme)
    page.wait_for_timeout(50)


def _assert_layout_matches(
    expected: dict[str, Rect],
    actual: dict[str, Rect],
) -> None:
    """Compare all measured coordinates with subpixel rendering tolerance."""

    assert actual.keys() == expected.keys()

    for selector in LAYOUT_SELECTORS:
        for dimension in ("x", "y", "width", "height"):
            difference = abs(
                actual[selector][dimension] - expected[selector][dimension]
            )
            assert difference <= 0.5, (
                f"{selector} changed {dimension} by {difference:.3f}px"
            )


@pytest.mark.parametrize(
    ("viewport", "scroll_fraction"),
    [
        ((1440, 900), 0.0),
        ((1024, 768), 0.5),
        ((755, 1169), 0.9),
        ((390, 844), 0.82),
    ],
)
def test_primary_layout_does_not_move_after_repeated_theme_switches(
    driver: DriverFactory,
    viewport: tuple[int, int],
    scroll_fraction: float,
) -> None:
    """Verify stable section coordinates across themes, viewports, and scroll levels."""

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    page.evaluate(
        """
        (fraction) => {
          const maximum = document.documentElement.scrollHeight - innerHeight;
          scrollTo({
            top: maximum * fraction,
            behavior: "instant",
          });
        }
        """,
        scroll_fraction,
    )
    page.wait_for_timeout(100)

    initial_scroll = page.evaluate("window.scrollY")
    initial_layout = _read_layout(page)

    for switch_index in range(8):
        expected_theme = "dark" if switch_index % 2 == 0 else "light"
        _click_theme_toggle(page, expected_theme)
        _assert_layout_matches(initial_layout, _read_layout(page))
        assert abs(page.evaluate("window.scrollY") - initial_scroll) <= 0.5


def test_rapid_theme_switches_keep_tentacle_surface_and_layout_in_lockstep(
    driver: DriverFactory,
) -> None:
    """Reproduce the rapid-toggle defect recorded around the tentacle block.

    The defect appeared when the page background was still interpolating between
    themes while the real WebGL tentacle iframe had already switched its
    substrate color. Repeated clicks made the two surfaces show opposing colors
    for several frames, creating the illusion that the rounded contact block had
    shifted even though its DOM rectangle was unchanged.

    This regression test keeps the real iframe loaded, scrolls to the affected
    block, and invokes the actual React-controlled theme button nine times
    back-to-back—well below the former 260-millisecond transition. Immediately
    after every click, before a CSS transition could settle, it verifies that the
    page and contact substrate expose the same computed background color.

    The test also guards the scroll position and every primary section rectangle,
    because a genuine layout jump must not be hidden by fixing only the color
    mismatch. Nine switches deliberately leave the page in the opposite theme,
    allowing the final assertion against the real tentacle iframe to prove that
    the cross-document theme message was applied too. Reintroducing the removed
    body background transition makes this test fail on its first color check and
    therefore recreates the specific recorded defect.
    """

    page = driver(
        stored_theme="light",
        viewport=(390, 844),
    )
    page.evaluate(
        """
        () => scrollTo({
          top: document.querySelector("#contact").offsetTop,
          behavior: "instant",
        })
        """
    )
    iframe_root = page.frame_locator(".contact__tentacles").locator("html")
    expect(iframe_root).to_have_attribute(
        "data-theme",
        "light",
        timeout=30_000,
    )

    expect(
        page.get_by_role(
            "button",
            name="Toggle color theme",
            exact=True,
        )
    ).to_be_visible()

    initial_scroll = page.evaluate("window.scrollY")
    initial_layout = _read_layout(page)

    samples = page.evaluate(
        """
        (selectors) => {
          const toggle = document.querySelector(
            'button[aria-label="Toggle color theme"]',
          );
          const readLayout = () => Object.fromEntries(
            selectors.map((selector) => {
              const rect = document.querySelector(selector).getBoundingClientRect();
              return [
                selector,
                {
                  x: rect.x,
                  y: rect.y + window.scrollY,
                  width: rect.width,
                  height: rect.height,
                },
              ];
            }),
          );
          const samples = [];

          for (let clickIndex = 0; clickIndex < 9; clickIndex += 1) {
            toggle.click();
            const contact = document.querySelector("#contact");
            samples.push({
              changedAt: performance.now(),
              expectedTheme: document.documentElement.dataset.theme,
              pageBackground:
                getComputedStyle(document.body).backgroundColor,
              contactBackground:
                getComputedStyle(contact).backgroundColor,
              scrollY: window.scrollY,
              layout: readLayout(),
            });
          }

          return samples;
        }
        """,
        LAYOUT_SELECTORS,
    )

    expect(iframe_root).to_have_attribute(
        "data-theme",
        "dark",
        timeout=30_000,
    )

    intervals = [
        current["changedAt"] - previous["changedAt"]
        for previous, current in zip(samples, samples[1:])
    ]
    assert all(interval < 260 for interval in intervals), intervals

    for switch_index, sample in enumerate(samples):
        expected_theme = "dark" if switch_index % 2 == 0 else "light"
        assert sample["expectedTheme"] == expected_theme
        assert sample["pageBackground"] == sample["contactBackground"]
        assert abs(sample["scrollY"] - initial_scroll) <= 0.5
        _assert_layout_matches(initial_layout, sample["layout"])
