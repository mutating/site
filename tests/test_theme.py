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
    ("stored_theme", "system_theme", "expected_contact_color"),
    [
        ("dark", "light", "rgb(255, 247, 251)"),
        ("light", "dark", "rgb(22, 13, 19)"),
    ],
)
def test_saved_theme_controls_initial_theme_and_contact_copy(
    driver: DriverFactory,
    stored_theme: str,
    system_theme: str,
    expected_contact_color: str,
) -> None:
    """Keep saved-theme precedence and contact colors across bootstrap states.

    A valid saved choice must first beat an explicitly opposite system theme.
    The old contact-color regression used a separate browser context whose
    system preference was ``no-preference``. To retain that exact initial-load
    contract without a second context, this test changes the context media,
    reloads the document so the inline theme bootstrap runs again, and waits
    for the same readiness marker as the driver. The stored choice must still
    win and the contact heading must use its exact theme text color.
    """

    page = driver(
        color_scheme=system_theme,
        stored_theme=stored_theme,
    )

    expect(page.locator("html")).to_have_attribute("data-theme", stored_theme)

    page.emulate_media(color_scheme="no-preference")
    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")

    expect(page.locator("html")).to_have_attribute("data-theme", stored_theme)
    expect(page.locator("#contact h2")).to_have_css(
        "color",
        expected_contact_color,
    )


def test_invalid_saved_theme_is_ignored(driver: DriverFactory) -> None:
    """Verify that an unsupported stored value cannot override the system theme."""

    page = driver(
        color_scheme="dark",
        stored_theme="sepia",
    )

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_theme_state_machine_follows_system_then_locks_manual_choices(
    driver: DriverFactory,
) -> None:
    """Follow live system changes, then persist and lock both manual themes.

    One fresh page begins without a saved preference and must follow every
    light/dark media change without writing storage.  A real toggle then stores
    dark, ignores later system changes, and survives a document reload.  The
    test then clears that preference and reloads under a dark system before the
    opposite real toggle stores light; both later media events must leave that
    first-time manual light choice untouched.
    Keeping every intermediate assertion preserves the three former contracts
    while avoiding three independent browser contexts.
    """

    page = driver(color_scheme="light")
    root = page.locator("html")
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('tmc-theme')") is None

    page.emulate_media(color_scheme="dark")
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") is None

    page.emulate_media(color_scheme="light")
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('tmc-theme')") is None

    theme_toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    theme_toggle.click()
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"

    page.emulate_media(color_scheme="dark")
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"
    page.emulate_media(color_scheme="light")
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"

    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"

    page.emulate_media(color_scheme="dark")
    expect(root).to_have_attribute("data-theme", "dark")

    page.evaluate("localStorage.removeItem('tmc-theme')")
    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    expect(root).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") is None

    theme_toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    theme_toggle.click()
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "light"

    page.emulate_media(color_scheme="light")
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "light"
    page.emulate_media(color_scheme="dark")
    expect(root).to_have_attribute("data-theme", "light")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "light"


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
