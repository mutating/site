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
