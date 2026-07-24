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


def test_page_and_tentacle_background_switch_theme_together(
    driver: DriverFactory,
) -> None:
    """Verify that the page and tentacle surface never show opposing theme colors."""

    page = driver(stored_theme="light", viewport=(1024, 768))
    page.evaluate(
        """
        () => scrollTo({
          top: document.querySelector("#contact").offsetTop,
          behavior: "instant",
        })
        """
    )
    iframe_root = page.frame_locator(".contact__tentacles").locator("html")

    expect(iframe_root).to_have_attribute("data-theme", "light")
    _click_theme_toggle(page, "dark")
    expect(iframe_root).to_have_attribute("data-theme", "dark")

    colors = page.evaluate(
        """
        () => ({
          page: getComputedStyle(document.body).backgroundColor,
          contact: getComputedStyle(document.querySelector("#contact")).backgroundColor,
        })
        """
    )

    assert colors["page"] == colors["contact"]
