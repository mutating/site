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


@pytest.mark.parametrize(
    "viewport",
    [(1440, 900), (1121, 900)],
    ids=["wide", "desktop-boundary"],
)
def test_footer_row_is_vertically_centered_below_its_divider(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the desktop footer labels centered in the space below the rule.

    The footer previously used 26 pixels of padding above its single row and
    50 pixels below it. Although all three label groups shared a baseline, the
    whole row therefore sat 12 pixels above the vertical center of the area
    beneath the divider. This test measures the rendered row and the usable
    footer box and guards their centers with subpixel tolerance. It runs both
    on a wide viewport and one pixel above the medium-layout boundary, proving
    the requested compact footer does not leak into the unchanged desktop
    presentation.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    geometry = page.locator(".site-footer").evaluate(
        """
        (footer) => {
          const footerRect = footer.getBoundingClientRect();
          const borderTop = parseFloat(
            getComputedStyle(footer).borderTopWidth,
          );
          const itemRects = Array.from(footer.children, (item) =>
            item.getBoundingClientRect(),
          );
          const rowTop = Math.min(...itemRects.map((rect) => rect.top));
          const rowBottom = Math.max(...itemRects.map((rect) => rect.bottom));

          return {
            availableCenter:
              (footerRect.top + borderTop + footerRect.bottom) / 2,
            rowCenter: (rowTop + rowBottom) / 2,
            columnCount:
              getComputedStyle(footer).gridTemplateColumns.split(" ").length,
            legalRight:
              footer.querySelector(".site-footer__legal")
                .getBoundingClientRect().right,
            taglineLeft:
              footer.querySelector(".site-footer__tagline")
                .getBoundingClientRect().left,
            taglineRight:
              footer.querySelector(".site-footer__tagline")
                .getBoundingClientRect().right,
            linksLeft:
              footer.querySelector(".site-footer__links")
                .getBoundingClientRect().left,
            linksDisplay: getComputedStyle(
              footer.querySelector(".site-footer__links"),
            ).display,
          };
        }
        """
    )

    assert abs(
        geometry["availableCenter"] - geometry["rowCenter"]
    ) <= 0.5
    assert geometry["columnCount"] == 3
    assert geometry["linksDisplay"] == "flex"
    assert geometry["legalRight"] < geometry["taglineLeft"]
    assert geometry["taglineRight"] < geometry["linksLeft"]


@pytest.mark.parametrize(
    "viewport",
    [
        (1120, 900),
        (1024, 900),
        (900, 900),
        (861, 900),
        (800, 900),
        (580, 900),
        (390, 844),
        (280, 700),
    ],
    ids=[
        "medium-boundary",
        "medium-wide",
        "medium",
        "medium-compact-boundary",
        "compact",
        "mobile-wide",
        "phone",
        "minimum",
    ],
)
def test_medium_and_narrow_footer_hide_navigation_and_center_legal_copy(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Use a centered two-line footer when the header already owns navigation.

    The wide desktop footer intentionally remains a single three-column row
    with copyright, tagline, and navigation. On medium and narrow screens,
    repeating ``Sources``, ``Blog``, and ``Contact`` below an always-sticky
    header wastes vertical space and leaves the two remaining messages visually
    unbalanced. The requested compact order is therefore copyright first,
    tagline second, with both lines centered across the footer.

    This regression measures the complete compact range from the exact 1120 px
    medium boundary down to the 280 px minimum, including both sides of the
    site's 860 px content breakpoint. It requires one grid column, hidden
    duplicate links, copyright above the tagline, and the horizontal center of
    both visible lines to match the footer center. Reintroducing the former
    links row, reversing the two messages, left-aligning either line, or limiting
    the rule to only phones makes at least one viewport fail.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    geometry = page.locator(".site-footer").evaluate(
        """
        (footer) => {
          const rectangle = (element) => {
            const rect = element.getBoundingClientRect();
            return {
              top: rect.top,
              bottom: rect.bottom,
              centerX: rect.left + rect.width / 2,
            };
          };
          const footerRect = footer.getBoundingClientRect();
          const legal = footer.querySelector(".site-footer__legal");
          const tagline = footer.querySelector(".site-footer__tagline");
          const links = footer.querySelector(".site-footer__links");
          return {
            footerCenterX: footerRect.left + footerRect.width / 2,
            columnCount:
              getComputedStyle(footer).gridTemplateColumns.split(" ").length,
            textAlign: getComputedStyle(footer).textAlign,
            legal: rectangle(legal),
            tagline: rectangle(tagline),
            legalJustifySelf: getComputedStyle(legal).justifySelf,
            taglineJustifySelf: getComputedStyle(tagline).justifySelf,
            linksDisplay: getComputedStyle(links).display,
          };
        }
        """
    )

    assert geometry["columnCount"] == 1
    assert geometry["linksDisplay"] == "none"
    assert geometry["textAlign"] == "center"
    assert geometry["legal"]["bottom"] < geometry["tagline"]["top"]
    assert geometry["legal"]["centerX"] == pytest.approx(
        geometry["footerCenterX"],
        abs=0.5,
    )
    assert geometry["tagline"]["centerX"] == pytest.approx(
        geometry["footerCenterX"],
        abs=0.5,
    )
    assert geometry["legalJustifySelf"] == "center"
    assert geometry["taglineJustifySelf"] == "center"


@pytest.mark.parametrize(
    "viewport",
    [(580, 900), (390, 844), (280, 700)],
    ids=["mobile-boundary", "phone", "minimum"],
)
def test_narrow_footer_copy_group_is_vertically_centered(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the two visible footer lines centered below the divider on phones.

    The narrow footer used to override only its top padding to 24 pixels while
    retaining the desktop 38-pixel bottom padding. That asymmetric box placed
    the copyright/tagline group seven pixels above the visual center even
    though both individual lines were horizontally centered. This regression
    measures the center of the complete two-line group against the center of
    the footer area below its one-pixel divider at the mobile breakpoint, a
    representative phone width, and the supported minimum width. Restoring
    unequal vertical padding or shifting either row independently makes the
    measured centers diverge and fails the test.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    geometry = page.locator(".site-footer").evaluate(
        """
        (footer) => {
          const footerRect = footer.getBoundingClientRect();
          const borderTop = parseFloat(
            getComputedStyle(footer).borderTopWidth,
          );
          const legalRect = footer
            .querySelector(".site-footer__legal")
            .getBoundingClientRect();
          const taglineRect = footer
            .querySelector(".site-footer__tagline")
            .getBoundingClientRect();

          return {
            availableCenter:
              (footerRect.top + borderTop + footerRect.bottom) / 2,
            copyCenter: (legalRect.top + taglineRect.bottom) / 2,
          };
        }
        """
    )

    assert geometry["copyCenter"] == pytest.approx(
        geometry["availableCenter"],
        abs=0.5,
    )


@pytest.mark.parametrize(
    "viewport",
    [
        (1440, 900),
        (1121, 900),
        (1120, 900),
        (861, 900),
        (580, 900),
        (390, 844),
        (280, 700),
    ],
    ids=[
        "wide",
        "desktop-boundary",
        "medium-boundary",
        "compact-boundary",
        "mobile-boundary",
        "phone",
        "minimum",
    ],
)
def test_footer_copy_lines_share_identical_typography_at_every_width(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Give the copyright and tagline exactly the same rendered text styling.

    These are the footer's two persistent text lines: they appear beside one
    another on desktop and as a centered stack on medium and narrow screens.
    Separate selectors previously made it easy for their font size, weight,
    spacing, color, opacity, shadow, or filter to drift at one responsive
    breakpoint while remaining superficially similar elsewhere. The test
    samples both sides of every relevant layout boundary and compares the
    complete set of computed typographic and visual-effect properties. A
    future rule that changes either line alone therefore fails at the exact
    width where the inconsistency is introduced.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    typography = page.locator(".site-footer").evaluate(
        """
        (footer) => {
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
          const read = (selector) => {
            const style = getComputedStyle(footer.querySelector(selector));
            return Object.fromEntries(
              properties.map((property) => [
                property,
                style.getPropertyValue(property),
              ]),
            );
          };

          return {
            legal: read(".site-footer__legal"),
            tagline: read(".site-footer__tagline"),
          };
        }
        """
    )

    assert typography["legal"] == typography["tagline"]


@pytest.mark.parametrize(
    ("viewport", "expected_overflow"),
    [
        ((580, 900), "visible"),
        ((390, 844), "visible"),
        ((280, 700), "visible"),
        ((581, 900), "hidden"),
        ((1440, 900), "hidden"),
    ],
    ids=[
        "mobile-boundary",
        "phone",
        "minimum",
        "one-pixel-above-mobile",
        "desktop",
    ],
)
def test_coexistence_shadow_is_not_clipped_only_on_narrow_screens(
    driver: DriverFactory,
    viewport: tuple[int, int],
    expected_overflow: str,
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

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
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

    assert geometry["sectionOverflow"] == expected_overflow
    assert geometry["sectionOverflowX"] == expected_overflow
    assert geometry["boxShadow"] != "none"

    if viewport[0] <= 580:
        assert geometry["cardWidth"] == pytest.approx(
            geometry["sectionWidth"],
            abs=0.5,
        )
        assert geometry["cardLeft"] == pytest.approx(
            geometry["sectionLeft"],
            abs=0.5,
        )


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
