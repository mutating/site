"""Browser tests that guard the page layout while its color theme changes."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]
Rect = dict[str, float]
FooterSnapshot = dict[str, Any]
FooterAssertion = Callable[[FooterSnapshot], None]

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
    """Verify stable section coordinates across themes, viewports, and scroll levels.

    The baseline is captured only after the browser's local fonts have settled
    and two animation frames have delivered any font-dependent wordmark/media
    layout work. Theme changes themselves update only colors and transforms, so
    the former fixed 50 ms sleeps after each toggle added wall time without
    synchronizing a geometry-affecting operation; the synchronous theme
    attribute assertion is the stronger readiness signal for this contract.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    page.evaluate("() => document.fonts.ready")
    expected_scroll = page.evaluate(
        """
        (fraction) => {
          const maximum = document.documentElement.scrollHeight - innerHeight;
          scrollTo({
            top: maximum * fraction,
            behavior: "instant",
          });
          return maximum * fraction;
        }
        """,
        scroll_fraction,
    )
    page.wait_for_function(
        "expected => Math.abs(window.scrollY - expected) <= 0.5",
        arg=expected_scroll,
    )
    page.evaluate(
        """
        () => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })
        """
    )

    initial_scroll = page.evaluate("window.scrollY")
    initial_layout = _read_layout(page)

    for switch_index in range(8):
        expected_theme = "dark" if switch_index % 2 == 0 else "light"
        _click_theme_toggle(page, expected_theme)
        _assert_layout_matches(initial_layout, _read_layout(page))
        assert abs(page.evaluate("window.scrollY") - initial_scroll) <= 0.5


def _read_footer_snapshot(page: Page) -> FooterSnapshot:
    """Read every footer contract from one immutable rendered state."""

    return page.locator(".site-footer").evaluate(
        """
        (footer) => {
          const rectangle = (element) => {
            const rect = element.getBoundingClientRect();
            return {
              top: rect.top,
              bottom: rect.bottom,
              left: rect.left,
              right: rect.right,
              centerX: rect.left + rect.width / 2,
            };
          };
          const footerRect = footer.getBoundingClientRect();
          const footerStyle = getComputedStyle(footer);
          const borderTop = parseFloat(footerStyle.borderTopWidth);
          const legal = footer.querySelector(".site-footer__legal");
          const tagline = footer.querySelector(".site-footer__tagline");
          const links = footer.querySelector(".site-footer__links");
          const legalRect = legal.getBoundingClientRect();
          const taglineRect = tagline.getBoundingClientRect();
          const linksRect = links.getBoundingClientRect();
          const itemRects = Array.from(footer.children, (item) =>
            item.getBoundingClientRect(),
          );
          const rowTop = Math.min(...itemRects.map((rect) => rect.top));
          const rowBottom = Math.max(...itemRects.map((rect) => rect.bottom));
          const properties = [
            "color",
            "font-family",
            "font-size",
            "font-style",
            "font-weight",
            "font-stretch",
            "font-variation-settings",
            "line-height",
            "letter-spacing",
            "text-transform",
            "text-decoration-line",
            "text-shadow",
            "filter",
            "opacity",
          ];
          const typography = (element) => {
            const style = getComputedStyle(element);
            return Object.fromEntries(
              properties.map((property) => [
                property,
                style.getPropertyValue(property),
              ]),
            );
          };

          return {
            availableCenter:
              (footerRect.top + borderTop + footerRect.bottom) / 2,
            rowCenter: (rowTop + rowBottom) / 2,
            copyCenter: (legalRect.top + taglineRect.bottom) / 2,
            footerCenterX: footerRect.left + footerRect.width / 2,
            columnCount:
              footerStyle.gridTemplateColumns.split(" ").length,
            textAlign: footerStyle.textAlign,
            legal: rectangle(legal),
            tagline: rectangle(tagline),
            legalJustifySelf: getComputedStyle(legal).justifySelf,
            taglineJustifySelf: getComputedStyle(tagline).justifySelf,
            linksDisplay: getComputedStyle(links).display,
            linksLeft: linksRect.left,
            legalTypography: typography(legal),
            taglineTypography: typography(tagline),
          };
        }
        """
    )


def _assert_footer_desktop(snapshot: FooterSnapshot) -> None:
    """Assert the unchanged three-column desktop footer contract."""

    assert abs(snapshot["availableCenter"] - snapshot["rowCenter"]) <= 0.5
    assert snapshot["columnCount"] == 3
    assert snapshot["linksDisplay"] == "flex"
    assert snapshot["legal"]["right"] < snapshot["tagline"]["left"]
    assert snapshot["tagline"]["right"] < snapshot["linksLeft"]


def _assert_footer_compact(snapshot: FooterSnapshot) -> None:
    """Assert compact order, hidden navigation, and horizontal centering."""

    assert snapshot["columnCount"] == 1
    assert snapshot["linksDisplay"] == "none"
    assert snapshot["textAlign"] == "center"
    assert snapshot["legal"]["bottom"] < snapshot["tagline"]["top"]
    assert snapshot["legal"]["centerX"] == pytest.approx(
        snapshot["footerCenterX"],
        abs=0.5,
    )
    assert snapshot["tagline"]["centerX"] == pytest.approx(
        snapshot["footerCenterX"],
        abs=0.5,
    )
    assert snapshot["legalJustifySelf"] == "center"
    assert snapshot["taglineJustifySelf"] == "center"


def _assert_footer_narrow_center(snapshot: FooterSnapshot) -> None:
    """Assert the phone copy group is vertically centered below its divider."""

    assert snapshot["copyCenter"] == pytest.approx(
        snapshot["availableCenter"],
        abs=0.5,
    )


def _assert_footer_typography(snapshot: FooterSnapshot) -> None:
    """Assert both persistent footer lines share all rendered text effects."""

    assert snapshot["legalTypography"] == snapshot["taglineTypography"]


@pytest.mark.parametrize(
    ("viewport", "assertions"),
    [
        ((1440, 900), (_assert_footer_desktop, _assert_footer_typography)),
        ((1121, 900), (_assert_footer_desktop, _assert_footer_typography)),
        ((1120, 900), (_assert_footer_compact, _assert_footer_typography)),
        ((1024, 900), (_assert_footer_compact, _assert_footer_typography)),
        ((900, 900), (_assert_footer_compact, _assert_footer_typography)),
        ((861, 900), (_assert_footer_compact, _assert_footer_typography)),
        ((800, 900), (_assert_footer_compact, _assert_footer_typography)),
        (
            (580, 900),
            (
                _assert_footer_compact,
                _assert_footer_narrow_center,
                _assert_footer_typography,
            ),
        ),
        (
            (390, 844),
            (
                _assert_footer_compact,
                _assert_footer_narrow_center,
                _assert_footer_typography,
            ),
        ),
        (
            (280, 700),
            (
                _assert_footer_compact,
                _assert_footer_narrow_center,
                _assert_footer_typography,
            ),
        ),
    ],
    ids=[
        "wide-desktop-typography",
        "desktop-boundary-desktop-typography",
        "medium-boundary-compact-typography",
        "medium-wide-compact-typography",
        "medium-compact-typography",
        "compact-boundary-compact-typography",
        "compact-typography",
        "mobile-boundary-compact-narrow-typography",
        "phone-compact-narrow-typography",
        "minimum-compact-narrow-typography",
    ],
)
def test_footer_layout_and_typography_contracts_at_every_width(
    driver: DriverFactory,
    viewport: tuple[int, int],
    assertions: tuple[FooterAssertion, ...],
) -> None:
    """Preserve every footer contract with one browser state per viewport.

    Four former parameterized tests loaded the same light-theme footer twenty
    times even though their matrices contained only ten unique viewports. This
    explicit matrix retains the original desktop, compact, narrow-centering,
    and typography intersections while reading one superset snapshot from each
    immutable page. Named linear assertion helpers preserve contract-specific
    tracebacks and keep cyclomatic complexity at two instead of replacing the
    old tests with breakpoint branches.

    Typography is also compared at 1024, 900, and 800 pixels, strengthening the
    original seven-width sampling at no additional browser cost. Regressions in
    desktop vertical balance, compact ordering/navigation, phone vertical
    centering, or any of fourteen text effects still fail at the precise
    viewport and named helper responsible.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    snapshot = _read_footer_snapshot(page)

    for assertion in assertions:
        assertion(snapshot)


def test_coexistence_shadow_is_not_clipped_only_on_narrow_screens(
    driver: DriverFactory,
) -> None:
    """Let the HUMAN/ROBOT card cast its full side shadow on narrow screens.

    The card deliberately spans the full width of ``.coexistence`` on phones.
    Its face owns a large blurred box shadow, while the section historically
    used ``overflow: hidden``. At widths up to 580 px those two rules put the
    clipping edge exactly on both vertical sides of the card, producing a hard
    cutoff: only the upper and lower shadow remained visible. Wide layouts did
    not expose the defect because the card's 880 px maximum width left ample
    space inside its section.

    This regression exercises the exact mobile boundary, a normal phone, and
    the supported minimum width. It requires the narrow section to expose
    overflow while also proving that the card still occupies the same full
    content width and retains a real shadow. The adjacent 581 px case and a
    desktop case must preserve the original clipped section behavior, guarding
    against an unnecessarily global overflow change. Restoring the old mobile
    ``overflow: hidden`` rule makes all three narrow cases fail and recreates
    the photographed side-clipping defect.
    """

    cases = [
        ((580, 900), "visible"),
        ((390, 844), "visible"),
        ((280, 700), "visible"),
        ((581, 900), "hidden"),
        ((1440, 900), "hidden"),
    ]
    page = driver(
        stored_theme="light",
        viewport=cases[0][0],
        load_interactive_visuals=False,
    )
    page.evaluate("() => document.fonts.ready")

    for viewport, expected_overflow in cases:
        width, height = viewport
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_function(
            "size => innerWidth === size.width && innerHeight === size.height",
            arg={"width": width, "height": height},
        )
        page.evaluate(
            """
            () => new Promise((resolve) => requestAnimationFrame(
              () => requestAnimationFrame(resolve),
            ))
            """
        )
        geometry = page.locator("#the-deal").evaluate(
            """
            (section) => {
              const card = section.querySelector(".coexistence__pair");
              const face = card.querySelector(".coexistence__pair-face");
              const sectionRect = section.getBoundingClientRect();
              const cardRect = card.getBoundingClientRect();
              const sectionStyle = getComputedStyle(section);
              const faceStyle = getComputedStyle(face);

              return {
                sectionOverflow: sectionStyle.overflow,
                sectionOverflowX: sectionStyle.overflowX,
                sectionWidth: sectionRect.width,
                cardWidth: cardRect.width,
                cardLeft: cardRect.left,
                sectionLeft: sectionRect.left,
                boxShadow: faceStyle.boxShadow,
              };
            }
            """
        )
        diagnostic = f"coexistence at {width}x{height}: {expected_overflow}"

        assert geometry["sectionOverflow"] == expected_overflow, diagnostic
        assert geometry["sectionOverflowX"] == expected_overflow, diagnostic
        assert geometry["boxShadow"] != "none", diagnostic

        if width <= 580:
            assert geometry["cardWidth"] == pytest.approx(
                geometry["sectionWidth"],
                abs=0.5,
            ), diagnostic
            assert geometry["cardLeft"] == pytest.approx(
                geometry["sectionLeft"],
                abs=0.5,
            ), diagnostic


@pytest.mark.interactive_visuals
@pytest.mark.xdist_group(name="tentacle-webgl")
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
        bounded_tentacle_rendering=True,
    )
    page.evaluate(
        """
        () => scrollTo({
          top: document.querySelector("#contact").offsetTop,
          behavior: "instant",
        })
        """
    )
    iframe = page.locator(".contact__tentacles")
    expect(iframe).to_have_attribute(
        "src",
        re.compile(
            r"[?&]max-tentacles=64"
            r"&render-supersample=0\.5"
            r"&generation-seed=12345$"
        ),
        timeout=30_000,
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
