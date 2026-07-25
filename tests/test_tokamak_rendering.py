"""Browser tests for theme-aware rendering of the tokamak wireframe."""

from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Locator, Page, expect


DriverFactory = Callable[..., Page]


def _reactor_canvas(page: Page) -> Locator:
    """Return the rendered canvas inside the self-contained tokamak frame."""

    canvas = page.frame_locator(".tokamak-model__frame").locator("canvas")
    expect(canvas).to_be_visible()
    return canvas


def test_light_tokamak_uses_invisible_depth_occluders(
    driver: DriverFactory,
) -> None:
    """Keep rear and internal wireframe edges hidden on the light background.

    The light theme previously hid every solid reactor mesh while leaving all
    line objects visible. Removing those surfaces also removed their depth
    information, so edges on the back and inside of the reactor showed through
    the front and formed dense, illegible clumps that did not exist in the dark
    theme. The light renderer must therefore keep a nonzero set of solid meshes
    as colorless depth occluders: they may write only depth, never color, while
    the ordinary page background and the pink wireframe remain visible.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _reactor_canvas(page)

    expect(canvas).to_have_attribute("data-reactor-theme", "light")
    diagnostics = canvas.evaluate(
        """
        (element) => ({
          wireObjects: Number(element.dataset.wireObjects),
          depthOccluders: Number(element.dataset.lightDepthOccluders),
        })
        """
    )

    assert diagnostics["wireObjects"] > 0
    assert diagnostics["depthOccluders"] > 0


def test_theme_switches_do_not_add_wireframe_geometry(
    driver: DriverFactory,
) -> None:
    """Preserve one wireframe and switch only its theme-specific occlusion.

    The visible light-theme defect looked as though extra geometry had been
    added, but it was caused by rear edges becoming visible through hidden
    surfaces. This regression test records the number of line objects, toggles
    repeatedly between light and dark, and proves that the count never changes.
    It also requires depth-only occluders in every light pass and none in every
    dark pass, catching both duplicated lines and a return of the original
    see-through rendering bug after a repeated theme change.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=True,
    )
    canvas = _reactor_canvas(page)
    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )

    expect(canvas).to_have_attribute("data-reactor-theme", "dark")
    initial_wire_count = int(canvas.get_attribute("data-wire-objects") or "0")
    assert initial_wire_count > 0
    assert canvas.get_attribute("data-light-depth-occluders") == "0"

    for expected_theme in ("light", "dark", "light", "dark"):
        toggle.click()
        expect(page.locator("html")).to_have_attribute(
            "data-theme",
            expected_theme,
        )
        expect(canvas).to_have_attribute(
            "data-reactor-theme",
            expected_theme,
        )

        wire_count = int(canvas.get_attribute("data-wire-objects") or "0")
        depth_occluders = int(
            canvas.get_attribute("data-light-depth-occluders") or "0"
        )
        assert wire_count == initial_wire_count

        if expected_theme == "light":
            assert depth_occluders > 0
        else:
            assert depth_occluders == 0
