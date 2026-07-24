"""End-to-end tests for initial and persisted color-theme behavior."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]


@pytest.mark.parametrize("system_theme", ["dark", "light"])
def test_uses_system_theme_without_saved_preference(
    driver: DriverFactory,
    system_theme: str,
) -> None:
    """Verify that a first visit follows an explicit system color preference."""

    page = driver(color_scheme=system_theme)

    expect(page.locator("html")).to_have_attribute("data-theme", system_theme)


def test_defaults_to_dark_without_system_preference(
    driver: DriverFactory,
) -> None:
    """Verify that dark mode is the fallback when the system reports no theme."""

    page = driver(
        color_scheme="no-preference",
        system_theme_available=False,
    )

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize(
    ("stored_theme", "system_theme"),
    [
        ("dark", "light"),
        ("light", "dark"),
    ],
)
def test_saved_theme_overrides_system_preference(
    driver: DriverFactory,
    stored_theme: str,
    system_theme: str,
) -> None:
    """Verify that a valid saved choice always wins over the system setting."""

    page = driver(
        color_scheme=system_theme,
        stored_theme=stored_theme,
    )

    expect(page.locator("html")).to_have_attribute("data-theme", stored_theme)


def test_invalid_saved_theme_is_ignored(driver: DriverFactory) -> None:
    """Verify that an unsupported stored value cannot override the system theme."""

    page = driver(
        color_scheme="dark",
        stored_theme="sepia",
    )

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_manual_theme_choice_survives_reload(driver: DriverFactory) -> None:
    """Verify that clicking the toggle stores the choice and restores it on reload."""

    page = driver(color_scheme="light")
    theme_toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )

    theme_toggle.click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"

    page.reload(wait_until="domcontentloaded")

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_system_theme_changes_are_followed_without_manual_choice(
    driver: DriverFactory,
) -> None:
    """Verify that live system-theme changes update a page with no saved choice."""

    page = driver(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    page.emulate_media(color_scheme="dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")


def test_manual_choice_ignores_later_system_theme_changes(
    driver: DriverFactory,
) -> None:
    """Verify that system changes cannot replace a user's manual selection."""

    page = driver(color_scheme="dark")
    theme_toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )

    theme_toggle.click()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    page.emulate_media(color_scheme="light")
    page.emulate_media(color_scheme="dark")

    expect(page.locator("html")).to_have_attribute("data-theme", "light")


def test_system_theme_still_works_when_storage_is_unavailable(
    driver: DriverFactory,
) -> None:
    """Verify that blocked browser storage does not break system-theme syncing."""

    page = driver(
        color_scheme="light",
        storage_available=False,
    )
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    page.emulate_media(color_scheme="dark")

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize(
    ("theme", "expected_color"),
    [
        ("light", "rgb(22, 13, 19)"),
        ("dark", "rgb(255, 247, 251)"),
    ],
)
def test_contact_copy_uses_theme_text_color(
    driver: DriverFactory,
    theme: str,
    expected_color: str,
) -> None:
    """Verify that contact copy is dark in light mode and light in dark mode."""

    page = driver(stored_theme=theme)
    contact_heading = page.locator("#contact h2")

    expect(contact_heading).to_have_css("color", expected_color)
