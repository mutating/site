"""Small smoke tests for the rendered site and its local assets."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat
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


@pytest.mark.parametrize(
    "viewport",
    [(390, 844), (800, 900), (1440, 900)],
    ids=["phone", "compact", "desktop"],
)
def test_all_github_navigation_links_use_sources_label_and_icon(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the GitHub destination consistently branded across navigation.

    The desktop header, responsive menu, and footer all point to the same
    source repository. Each occurrence must use the concise visible label
    ``Sources`` followed by the decorative GitHub mark, while the old
    ``Open source`` label must not survive in any of those navigation areas.
    The icon is hidden from assistive technology so the accessible link name
    remains exactly ``Sources`` instead of repeating or garbling the label.
    It also locks the requested word-to-icon gap at ``0.675em``—one and a half
    times the original ``0.45em``—while allowing each navigation context to
    retain its own font size.

    The GitHub path reaches the exact right edge of its 16-unit viewBox. At
    fractional responsive scales the browser needs to paint antialiasing just
    beyond that edge; the SVG viewport's default clipping previously shaved a
    visible sliver from the mark. Across phone, compact, and desktop widths this
    test therefore requires visible SVG overflow while locking the original
    ``1.15em`` rendered size. Removing that overflow fix recreates the
    resolution-dependent clipping; expanding the icon as a workaround also
    fails because it would alter the established navigation spacing.
    """

    page = driver(color_scheme="light", viewport=viewport)
    links = page.locator(
        'a.source-link[href="https://github.com/mutating"]',
    )

    expect(links).to_have_count(3)
    for index in range(3):
        link = links.nth(index)
        expect(link).to_have_text("Sources")
        expect(link.locator(".source-link__icon")).to_have_count(1)
        expect(link.locator(".source-link__icon")).to_have_attribute(
            "aria-hidden",
            "true",
        )
        expect(link.locator(".source-link__icon")).to_have_css(
            "overflow",
            "visible",
        )
        spacing = link.evaluate(
            """
            (element) => {
              const style = getComputedStyle(element);
              const iconStyle = getComputedStyle(
                element.querySelector(".source-link__icon"),
              );
              return {
                gap: Number.parseFloat(style.columnGap),
                fontSize: Number.parseFloat(style.fontSize),
                iconWidth: Number.parseFloat(iconStyle.width),
                iconHeight: Number.parseFloat(iconStyle.height),
              };
            }
            """
        )
        assert spacing["gap"] == pytest.approx(
            spacing["fontSize"] * 0.675,
            abs=0.1,
        )
        if link.is_visible():
            assert spacing["iconWidth"] == pytest.approx(
                spacing["fontSize"] * 1.15,
                abs=0.1,
            )
            assert spacing["iconHeight"] == pytest.approx(
                spacing["fontSize"] * 1.15,
                abs=0.1,
            )

    expect(
        page.locator(".main-nav, .mobile-menu, .site-footer__links").get_by_text(
            "Open source",
            exact=True,
        ),
    ).to_have_count(0)


def test_site_critical_assets_are_local_and_non_empty() -> None:
    """Verify that every critical visual asset is present in the repository."""

    required_assets = [
        "public/muta-eye-surface.webp",
        "public/muta-v2-transparent.png",
        "public/muta-v2-transparent-v10.webp",
        "public/muta-v2-transparent-2x-v10.webp",
        "public/mutant-mascot-transparent.png",
        "public/mutant-mascot.jpg",
        "public/og.png",
        "public/pantograph-variant-20-clean-v2.svg",
        "public/tokamak-reactor.html",
        "public/muta-tentacles.html",
    ]

    for relative_path in required_assets:
        asset = PROJECT_ROOT / relative_path
        assert asset.is_file(), f"{relative_path} must be a file"
        assert asset.stat().st_size > 0, f"{relative_path} must not be empty"


def test_distribution_section_runs_the_supplied_pantograph_animation(
    driver: DriverFactory,
) -> None:
    """Keep the supplied SVG animated, frameless, and synchronized to the theme.

    The pantograph's linkage motion lives in a script inside the SVG file.
    Loading that file through a regular ``img`` element would show the correct
    first frame but browser security rules would prevent the script—and thus
    the requested animation—from running. This test verifies both halves of
    the integration: the old network-orbit illustration is gone, and the
    replacement is mounted inline in the page whose pantograph API reports an
    actively changing progress value after the section enters the viewport.
    Inline mounting is essential: an iframe/object owns a separate document
    canvas which can remain opaque white even when all of its authored layers
    say ``background: transparent``. The assertion waits for a state change
    instead of expecting an exact frame timestamp, so normal differences in
    browser and CI speed do not make the test flaky.

    The illustration must also sit directly on the section background instead
    of retaining either the old card border/shadow or the supplied SVG's opaque
    paper rectangle. Its original black structural ink stays black in the light
    theme and becomes white in the dark theme, including after a manual theme
    switch made while the animation is already running. This guards the inline
    theme cascade: without it the SVG would keep the palette that was active at
    load time and drift out of sync with the rest of the page.
        The same switch must invert every neutral interior surface, texture speckle,
        card dot, highlight, and secondary outline instead of leaving bright white
        structures beneath the newly white contour. Every formerly coral flat
        accent deliberately inherits the site's current hero-button pink, while
        the active circle retains a three-step light/mid/deep gradient built from
        that same theme-aware pink family.

    Finally, the normalized SVG hash locks every original geometry and
    ``stroke-width`` byte from the supplied extended-handle revision. Only the
    deliberately removed paper background and the theme-aware replacement of
    black ink are ignored. This catches subtle integration regressions such as
    changing a 3.25-unit line to 3 units or altering a handle path while still
    allowing the site-specific transparent background and dark-theme palette.
    Because the viewport is intentionally enlarged to 112%, the browser-level
    assertions also require every normally scaling stroke to use the exact
    reciprocal width. The resulting screen-space weight therefore matches the
    original SVG instead of becoming 12% heavier with the larger illustration.
    The supplied connector bar is a special regression case: its
    ``non-scaling-stroke`` made the two side rails stay at 4.5 CSS pixels while
    the rest of the mechanism shrank with the responsive viewport, so they
    looked disproportionately heavy on the site. The integration preserves the
    source marker as data for fidelity checks but deliberately lets that bar
    scale with its neighbouring arms. Its dedicated assertion prevents the
    overly thick rails from returning.

    The component must request a revisioned SVG URL with ``no-store`` caching
    and must not recreate an iframe/object wrapper. Reusing the old stable
    asset URL lets a browser retain the pre-inversion light artwork across
    deployments; restoring a nested document recreates the opaque white panel
    this regression test is specifically intended to catch.
    """

    page_source = (PROJECT_ROOT / "app/page.tsx").read_text()
    assert '"/pantograph-variant-20-clean-v2.svg"' in page_source
    assert 'cache: "no-store"' in page_source
    assert "pantograph-frame" not in page_source
    assert not (PROJECT_ROOT / "public/pantograph-frame-v2.html").exists()
    assert not (
        PROJECT_ROOT / "public/pantograph-variant-20-clean.svg"
    ).exists()

    svg_source = (
        PROJECT_ROOT / "public/pantograph-variant-20-clean-v2.svg"
    ).read_text()
    assert '<rect width="1536" height="1024" fill="url(#paper)"/>' not in (
        svg_source
    )
    assert 'stroke="var(--pantograph-ink)"' in svg_source
    assert 'fill="var(--pantograph-ink)"' in svg_source
    normalized_svg = re.sub(
        r"<style>.*?</style>\n?",
        "",
        svg_source,
        flags=re.DOTALL,
    )
    normalized_svg = normalized_svg.replace(
        "var(--pantograph-ink)",
        "#171316",
    )
    normalized_svg = normalized_svg.replace(
        "var(--pantograph-surface-soft)",
        "#fffafa",
    )
    normalized_svg = normalized_svg.replace(
        "var(--pantograph-surface)",
        "#fffdfd",
    )
    normalized_svg = normalized_svg.replace(
        '<svg id="pantographArtwork" ',
        "<svg ",
    )
    normalized_svg = normalized_svg.replace(
        'data-source-vector-effect="non-scaling-stroke"',
        'vector-effect="non-scaling-stroke"',
    )
    normalized_svg = normalized_svg.replace(
        '<rect width="1536" height="1024" fill="url(#paper)"/>\n',
        "",
    )
    normalized_svg = re.sub(r"\n{3,}", "\n\n", normalized_svg)
    assert hashlib.sha256(normalized_svg.encode()).hexdigest() == (
        "fbe48b2a2696f16d4ef701c610a9ed83cdce7627b20595c0705bdb369f99dba3"
    )

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
    )
    visual = page.locator(".pantograph-visual")
    embedded_svg = visual.locator(".pantograph-visual__object")

    expect(page.locator(".network-visual")).to_have_count(0)
    expect(embedded_svg.locator("#pantographArtwork")).to_have_count(1)
    container_style = visual.evaluate(
        """
        (element) => {
          const style = getComputedStyle(element);
          return {
            backgroundColor: style.backgroundColor,
            borderTopWidth: style.borderTopWidth,
            boxShadow: style.boxShadow,
          };
        }
        """
    )
    assert container_style == {
        "backgroundColor": "rgba(0, 0, 0, 0)",
        "borderTopWidth": "0px",
        "boxShadow": "none",
    }
    scaled_geometry = visual.evaluate(
        """
        (container) => {
          const embedded = container.querySelector(
            ".pantograph-visual__object",
          );
          const containerRect = container.getBoundingClientRect();
          const embeddedRect = embedded.getBoundingClientRect();
          return {
            widthRatio: embeddedRect.width / containerRect.width,
            heightRatio: embeddedRect.height / containerRect.height,
            centerDeltaX:
              (embeddedRect.left + embeddedRect.width / 2)
              - (containerRect.left + containerRect.width / 2),
            centerDeltaY:
              (embeddedRect.top + embeddedRect.height / 2)
              - (containerRect.top + containerRect.height / 2),
          };
        }
        """
    )
    assert scaled_geometry["widthRatio"] == pytest.approx(1.12, abs=0.002)
    assert scaled_geometry["heightRatio"] == pytest.approx(1.12, abs=0.002)
    assert scaled_geometry["centerDeltaX"] == pytest.approx(0, abs=0.25)
    assert scaled_geometry["centerDeltaY"] == pytest.approx(0, abs=0.25)
    visual.scroll_into_view_if_needed()
    page.wait_for_function(
        """
        () => {
          return Boolean(
            window.__pantograph
            && document.documentElement.dataset.progress,
          );
        }
        """
    )
    transparent_layers = embedded_svg.evaluate(
        """
        (embedded) => ({
          objectBackground: getComputedStyle(embedded).backgroundColor,
          svgBackground: getComputedStyle(
            embedded.querySelector("#pantographArtwork"),
          ).backgroundColor,
        })
        """
    )
    assert transparent_layers == {
        "objectBackground": "rgba(0, 0, 0, 0)",
        "svgBackground": "rgba(0, 0, 0, 0)",
    }
    visual.evaluate(
        "(element) => { element.style.background = 'rgb(12, 34, 56)'; }"
    )
    transparency_probe = Image.open(BytesIO(visual.screenshot())).convert(
        "RGB"
    )
    assert transparency_probe.getpixel((4, 4)) == (12, 34, 56)
    visual.evaluate("(element) => { element.style.background = ''; }")
    initial_progress = embedded_svg.evaluate(
        """
        (embedded) => Number.parseFloat(
          embedded.ownerDocument.documentElement.dataset.progress,
        )
        """
    )
    page.wait_for_function(
        """
        (initialProgress) => {
          const embedded = document.querySelector(
            ".pantograph-visual__object",
          );
          const currentProgress = Number.parseFloat(
            embedded.ownerDocument.documentElement.dataset.progress,
          );
          return Math.abs(currentProgress - initialProgress) > 0.003;
        }
        """,
        arg=initial_progress,
    )
    metrics = embedded_svg.evaluate(
        """
        (embedded) =>
          embedded.ownerDocument.defaultView.__pantograph.metrics()
        """,
    )
    assert metrics["playing"] is True
    assert metrics["config"]["duration"] == 8000
    assert metrics["config"]["springMode"] == "removed"
    assert metrics["config"]["penMode"] == "extended-solid"
    assert metrics["finite"] is True

    stroke_metrics = embedded_svg.evaluate(
        """
        (embedded) => [
          ...embedded.querySelectorAll("[stroke-width]"),
        ].map((element) => ({
          source: Number.parseFloat(element.getAttribute("stroke-width")),
          computed: Number.parseFloat(getComputedStyle(element).strokeWidth),
          vectorEffect: getComputedStyle(element).vectorEffect,
        }))
        """
    )
    assert stroke_metrics
    for stroke in stroke_metrics:
        if stroke["vectorEffect"] == "non-scaling-stroke":
            assert stroke["computed"] == pytest.approx(
                stroke["source"],
                abs=0.0001,
            )
        else:
            assert stroke["computed"] * 1.12 == pytest.approx(
                stroke["source"],
                abs=0.0001,
            )

    connector_stroke = embedded_svg.evaluate(
        """
        (embedded) => {
          const connector = embedded.querySelector(
            "#bConnector > rect",
          );
          const style = getComputedStyle(connector);
          return {
            source: Number.parseFloat(
              connector.getAttribute("stroke-width"),
            ),
            computed: Number.parseFloat(style.strokeWidth),
            vectorEffect: style.vectorEffect,
          };
        }
        """
    )
    assert connector_stroke["vectorEffect"] == "none"
    assert connector_stroke["computed"] * 1.12 == pytest.approx(
        connector_stroke["source"],
        abs=0.0001,
    )

    adaptive_ink = embedded_svg.evaluate(
        """
        (embedded) => {
          const stroke = embedded.querySelector(
            '[stroke="var(--pantograph-ink)"]',
          );
          const fill = embedded.querySelector(
            '[fill="var(--pantograph-ink)"]',
          );
          return {
            strokeCount: embedded.querySelectorAll(
              '[stroke="var(--pantograph-ink)"]',
            ).length,
            fillCount: embedded.querySelectorAll(
              '[fill="var(--pantograph-ink)"]',
            ).length,
            stroke: getComputedStyle(stroke).stroke,
            fill: getComputedStyle(fill).fill,
          };
        }
        """
    )
    assert adaptive_ink["strokeCount"] > 0
    assert adaptive_ink["fillCount"] > 0
    assert adaptive_ink["stroke"] == "rgb(23, 19, 22)"
    assert adaptive_ink["fill"] == "rgb(23, 19, 22)"

    page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    ).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    dark_ink = embedded_svg.evaluate(
        """
        (embedded) => {
          const stroke = embedded.querySelector(
            '[stroke="var(--pantograph-ink)"]',
          );
          const fill = embedded.querySelector(
            '[fill="var(--pantograph-ink)"]',
          );
          return {
            stroke: getComputedStyle(stroke).stroke,
            fill: getComputedStyle(fill).fill,
          };
        }
        """
    )
    assert dark_ink["stroke"] == "rgb(255, 250, 250)"
    assert dark_ink["fill"] == "rgb(255, 250, 250)"

    dark_palette = embedded_svg.evaluate(
        """
        (embedded) => {
          const neutral = new Set([
            "#fffdfd", "#fffafa", "#fff8fa", "#fffafb", "#fff", "#fcf7f8",
            "#b37889", "#b77f90", "#bb7f90", "#c28c9c", "#c28e9e",
            "#c48d9e", "#c58b9c", "#ca95a4", "#d0a2af", "#d4a9b4",
            "#d8afb9", "#d9bcc5", "#dfbbc5",
          ]);
          const gradientAccent = new Set([
            "#f51659", "#fd1c62", "#ff3374",
          ]);
          const flatAccent = new Set([
            "#f72467", "#f72468", "#f72567", "#fa1c61", "#fa2466",
          ]);
          const properties = [
            ["fill", "fill"],
            ["stroke", "stroke"],
            ["stop-color", "stopColor"],
          ];
          const sourceRgb = (hex) => {
            const normalized = hex.length === 4
              ? `#${[...hex.slice(1)].map((value) => value.repeat(2)).join("")}`
              : hex;
            const value = Number.parseInt(normalized.slice(1), 16);
            return `rgb(${value >> 16}, ${(value >> 8) & 255}, ${value & 255})`;
          };
          const records = [];

          for (const element of embedded.querySelectorAll("*")) {
            const style = getComputedStyle(element);
            for (const [attribute, styleProperty] of properties) {
              const source = element.getAttribute(attribute)?.toLowerCase();
              if (
                !source ||
                (
                  !neutral.has(source) &&
                  !gradientAccent.has(source) &&
                  !flatAccent.has(source)
                )
              ) {
                continue;
              }
              records.push({
                source,
                category: neutral.has(source)
                  ? "neutral"
                  : flatAccent.has(source)
                    ? "flatAccent"
                    : "gradientAccent",
                property: attribute,
                computed: style[styleProperty],
                sourceRgb: sourceRgb(source),
              });
            }
          }

          const cardDots = records.filter(
            (record) => record.property === "fill"
              && ["#c58b9c", "#ca95a4"].includes(record.source),
          );
          return {
            records,
            cardDots,
            siteHot: sourceRgb(
              getComputedStyle(document.documentElement)
                .getPropertyValue("--hot")
                .trim(),
            ),
          };
        }
        """
    )
    neutral_records = [
        record
        for record in dark_palette["records"]
        if record["category"] == "neutral"
    ]
    gradient_records = [
        record
        for record in dark_palette["records"]
        if record["category"] == "gradientAccent"
    ]
    flat_accent_records = [
        record
        for record in dark_palette["records"]
        if record["category"] == "flatAccent"
    ]
    assert neutral_records
    assert gradient_records
    assert flat_accent_records
    assert dark_palette["cardDots"]
    assert all(
        record["computed"] != record["sourceRgb"]
        for record in neutral_records
    )
    assert all(
        record["computed"] != record["sourceRgb"]
        for record in gradient_records
    )
    assert all(
        record["computed"] == dark_palette["siteHot"]
        for record in flat_accent_records
    )
    assert all(
        record["computed"] != record["sourceRgb"]
        for record in dark_palette["cardDots"]
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_pantograph_handle_and_fasteners_match_hero_button_pink(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Keep every formerly coral detail synchronized with the site's pink.

    The supplied SVG used several subtly different coral source colors for
    the pantograph handle, joint screws, rail-mount screws, active drawing
    handle, inactive oscilloscope traces, and active-card border. On the site
    every one of those details must instead use exactly the same theme-aware
    pink as the ``Read our thesis`` button, while neutral materials remain
    independent.

    This regression compares computed browser colors rather than hard-coding a
    duplicate hex value. It covers every authored coral fill and stroke family
    in both themes. Restoring any original coral shade, missing an
    oscilloscope/card/control family, or letting the SVG drift from a future
    brand-color update makes the test fail.
    """

    page = driver(stored_theme=theme, viewport=(1440, 900))
    embedded_svg = page.locator(
        ".pantograph-visual__object #pantographArtwork"
    )
    expect(embedded_svg).to_have_count(1)

    palette = embedded_svg.evaluate(
        """
        (svg) => {
          const selectors = [
            ['[fill="#f72467"]', "fill"],
            ['[fill="#f72468"]', "fill"],
            ['[fill="#f72567"]', "fill"],
            ['[fill="#fa2466"]', "fill"],
            ['[fill="#fa1c61"]', "fill"],
            ['[fill="#ff2c92"]', "fill"],
            ['[stroke="#f72567"]', "stroke"],
            ['[stroke="#fa2466"]', "stroke"],
            ['[stroke="#fa1c61"]', "stroke"],
            ['[stroke="#ff2c92"]', "stroke"],
          ];
          const controls = [];

          for (const [selector, property] of selectors) {
            for (const element of svg.querySelectorAll(selector)) {
              controls.push({
                source: element.getAttribute(property),
                property,
                computed: getComputedStyle(element)[property],
              });
            }
          }

          return {
            controls,
            button: getComputedStyle(
              document.querySelector(".button--primary"),
            ).backgroundColor,
          };
        }
        """
    )

    assert len(palette["controls"]) == 16
    assert all(
        control["computed"] == palette["button"]
        for control in palette["controls"]
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_pantograph_active_gradient_uses_site_pink_family(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Prevent the active card gradient from reverting to coral-red stops.

    The active oscilloscope circle is the largest saturated area in the
    pantograph. Its original three-stop gradient used independent red-coral
    literals, so recoloring only screws and strokes left a conspicuous
    off-brand patch. This test reads the rendered stop colors in both themes:
    the highlight must inherit the site's bright pink, the middle stop must
    exactly equal the primary hero-button pink, and the shadow must remain a
    darker shade of that same magenta hue. It deliberately avoids screenshot
    pixels and animation timing, while still failing if any legacy gradient
    stop becomes visible again.
    """

    page = driver(stored_theme=theme, viewport=(1440, 900))
    embedded_svg = page.locator(
        ".pantograph-visual__object #pantographArtwork"
    )
    expect(embedded_svg).to_have_count(1)

    palette = embedded_svg.evaluate(
        """
        (svg) => {
          const stops = [...svg.querySelectorAll("#activePink stop")].map(
            (stop) => getComputedStyle(stop).stopColor,
          );
          const root = getComputedStyle(document.documentElement);
          const probe = document.createElement("span");
          document.body.append(probe);
          const resolve = (value) => {
            probe.style.color = value.trim();
            return getComputedStyle(probe).color;
          };
          const result = {
            stops,
            hot: resolve(root.getPropertyValue("--hot")),
            hotBright: resolve(root.getPropertyValue("--hot-bright")),
          };
          probe.remove();
          return result;
        }
        """
    )

    assert len(palette["stops"]) == 3
    assert palette["stops"][0] == palette["hotBright"]
    assert palette["stops"][1] == palette["hot"]
    assert palette["stops"][2] not in {
        "rgb(245, 22, 89)",
        palette["stops"][0],
        palette["stops"][1],
    }


def test_dark_pantograph_reusable_handles_inherit_dark_surfaces(
    driver: DriverFactory,
) -> None:
    """Prevent the repeated handles from retaining white fills in dark mode.

    The rail mounts and seven inactive drawing needles are authored once in
    SVG ``defs`` and rendered repeatedly through ``use`` elements. Attribute
    selectors successfully recolor ordinary SVG nodes but do not reliably
    cross the browser's anonymous ``use`` instance tree. That precise mismatch
    previously made the page background and cards dark while every repeated
    handle stayed visibly filled with white.

    The reusable source geometry must therefore use inherited custom
    properties rather than literal light colors. This test checks both sides
    of that contract: the source shapes reference the shared surface variable,
    and every rendered ``use`` instance receives the dark surface values from
    the inline SVG root. Replacing those fills with ``#fffdfd``/``#fffafa`` or
    moving the variables outside the inherited SVG tree will fail the test
    without relying on animation timing or pixel-antialiasing differences.
    """

    page = driver(stored_theme="dark", viewport=(1440, 900))
    stage = page.locator(".pantograph-visual__object")
    expect(stage.locator("#pantographArtwork")).to_have_count(1)

    surfaces = stage.evaluate(
        """
        (stage) => {
          const artwork = stage.querySelector("#pantographArtwork");
          const needle = artwork.querySelector("#needle");
          const mount = artwork.querySelector("#railMount");
          const fastener = artwork.querySelector("#fastener");
          const uses = [
            ...artwork.querySelectorAll(
              'use[href="#needle"], use[href="#railMount"]',
            ),
          ];
          return {
            needleBodyAttribute:
              needle.querySelector("rect").getAttribute("fill"),
            needleTipAttribute:
              needle.querySelector("path").getAttribute("fill"),
            mountAttribute:
              mount.querySelector("rect").getAttribute("fill"),
            fastenerAttribute:
              fastener.querySelector("circle").getAttribute("fill"),
            needleBodyComputed:
              getComputedStyle(needle.querySelector("rect")).fill,
            mountComputed:
              getComputedStyle(mount.querySelector("rect")).fill,
            fastenerComputed:
              getComputedStyle(fastener.querySelector("circle")).fill,
            useSurfaceValues: uses.map((element) =>
              getComputedStyle(element)
                .getPropertyValue("--pantograph-surface")
                .trim()
            ),
            useSoftSurfaceValues: uses.map((element) =>
              getComputedStyle(element)
                .getPropertyValue("--pantograph-surface-soft")
                .trim()
            ),
          };
        }
        """
    )

    assert surfaces["needleBodyAttribute"] == "var(--pantograph-surface)"
    assert surfaces["needleTipAttribute"] == "var(--pantograph-surface)"
    assert surfaces["mountAttribute"] == "var(--pantograph-surface)"
    assert surfaces["fastenerAttribute"] == (
        "var(--pantograph-surface-soft)"
    )
    assert surfaces["needleBodyComputed"] == "rgb(9, 6, 8)"
    assert surfaces["mountComputed"] == "rgb(9, 6, 8)"
    assert surfaces["fastenerComputed"] == "rgb(12, 9, 11)"
    assert surfaces["useSurfaceValues"]
    assert set(surfaces["useSurfaceValues"]) == {"#090608"}
    assert set(surfaces["useSoftSurfaceValues"]) == {"#0c090b"}


def test_every_neutral_pantograph_dot_inverts_between_themes(
    driver: DriverFactory,
) -> None:
    """Verify every dot against the semantic palette that authored it.

    The illustration contains hundreds of circles: texture speckles, card
    dots, fasteners, joints, and small highlights. Many of them inherit their
    fill from a containing ``g`` rather than declaring a color themselves, so
    checking only the circle's own attribute can miss an entire rendered dot
    family. This regression resolves the nearest authored fill through the
    ancestor chain, records every computed circle fill in the light theme,
    switches the already-mounted SVG to dark mode, and compares the same DOM
    nodes by stable source order.

    Neutral, white, surface, and ink-colored circles must all invert. The
    original coral fasteners are a separate semantic category: the site
    deliberately remaps them to its theme-aware ``--hot`` color, so each one
    must equal the hero-button pink in both themes rather than retain its old
    source RGB. Saturated illustration paints and URL paints remain unchanged;
    the individual neutral circles inside the stipple patterns are still
    present in—and checked by—the neutral set. ``fill: none`` is explicitly
    accounted for as well. The classification uses authored SVG values instead
    of serialized computed RGB, avoiding the stale assumption that previously
    treated the remapped coral controls as an invariant pink family. It also
    stays independent of animation timing and pixel antialiasing.
    """

    page = driver(stored_theme="light", viewport=(1440, 900))
    stage = page.locator(".pantograph-visual__object")
    expect(stage.locator("#pantographArtwork")).to_have_count(1)

    read_palette = """
      (stage) => {
        const artwork = stage.querySelector("#pantographArtwork");
        const authoredFill = (element) => {
          for (
            let current = element;
            current && current !== stage;
            current = current.parentElement
          ) {
            const source = current.getAttribute?.("fill");
            if (source) {
              return source.toLowerCase();
            }
          }
          return "(css/default)";
        };
        const toRgb = (value) => {
          const probe = stage.ownerDocument.createElement("span");
          probe.style.color = value;
          stage.ownerDocument.body.append(probe);
          const color = getComputedStyle(probe).color;
          probe.remove();
          return color;
        };
        return {
          circles: [...artwork.querySelectorAll("circle")].map(
            (element, index) => ({
              index,
              source: authoredFill(element),
              computed: getComputedStyle(element).fill,
            }),
          ),
          siteHot: toRgb(
            getComputedStyle(stage.ownerDocument.documentElement)
              .getPropertyValue("--hot")
              .trim(),
          ),
        };
      }
    """
    light_palette = stage.evaluate(read_palette)

    page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    ).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    dark_palette = stage.evaluate(read_palette)

    light_circles = light_palette["circles"]
    dark_circles = dark_palette["circles"]
    assert len(light_circles) == len(dark_circles)
    assert len(light_circles) > 300

    brand_accent_sources = {
        "#f72467",
        "#f72468",
        "#f72567",
    }
    saturated_pink_sources = {
        "#f51659",
        "#fa1c61",
        "#fa2466",
        "#fd1c62",
        "#ff3374",
    }
    neutral_pairs = []
    accent_pairs = []
    saturated_pink_pairs = []
    url_pairs = []
    unpainted_pairs = []

    for light, dark in zip(light_circles, dark_circles, strict=True):
        assert light["index"] == dark["index"]
        assert light["source"] == dark["source"]
        pair = (light, dark)
        if light["source"] == "none":
            unpainted_pairs.append(pair)
        elif light["source"] in brand_accent_sources:
            accent_pairs.append(pair)
        elif light["source"] in saturated_pink_sources:
            saturated_pink_pairs.append(pair)
        elif light["source"].startswith("url("):
            url_pairs.append(pair)
        else:
            neutral_pairs.append(pair)

    assert len(neutral_pairs) > 300
    assert accent_pairs
    assert url_pairs
    assert unpainted_pairs
    assert all(
        light["computed"] != dark["computed"]
        for light, dark in neutral_pairs
    )
    assert all(
        light["computed"] == light_palette["siteHot"]
        and dark["computed"] == dark_palette["siteHot"]
        and light["computed"] != dark["computed"]
        for light, dark in accent_pairs
    )
    assert all(
        light["computed"] == dark["computed"]
        for light, dark in saturated_pink_pairs
    )
    assert all(
        light["computed"] == dark["computed"]
        for light, dark in url_pairs
    )
    assert all(
        light["computed"] == dark["computed"] == "none"
        for light, dark in unpainted_pairs
    )


def test_pantograph_straightens_only_in_the_two_column_layout(
    driver: DriverFactory,
) -> None:
    """Increase desktop pantograph height without scaling any of its parts.

    The two-column composition has room for a slightly straighter linkage, but
    a CSS ``scaleY`` would also stretch circles, line widths, cards, needles,
    and the animation itself. The intended implementation instead lifts the
    fixed head by 20% of the original head-to-rail height and lets the existing
    rigid-link intersection math solve new joint angles with the original bar
    lengths.

    This test begins below the 860 px single-column breakpoint and locks the
    original mobile coordinates. It then resizes the *same mounted animation*
    to desktop width, proving the responsive geometry updates without a reload:
    the head-to-rail height becomes exactly 1.2× while every rigid bar and the
    105-unit vertical connector retain their source length. The target curve,
    carriage, active needle tip, duration, and progress-driven displacement
    must remain identical at the same animation progress, so only the linkage
    angles change. Finally the viewport returns to mobile width and every fixed
    and solved coordinate must return to the supplied SVG geometry. This last
    round-trip catches implementations that permanently mutate the source or
    leave desktop transforms behind after a responsive transition.
    """

    page = driver(stored_theme="light", viewport=(800, 900))
    stage = page.locator(".pantograph-visual__object")
    expect(stage.locator("#pantographArtwork")).to_have_count(1)

    read_frame = """
      (stage, progress) => {
        const api = stage.ownerDocument.defaultView.__pantograph;
        api.seek(progress);
        const metrics = api.metrics();
        const fixedTransforms = [
          ...stage.querySelectorAll("[data-fixed-head]"),
        ].map((element) => element.getAttribute("transform"));
        return {metrics, fixedTransforms};
      }
    """
    mobile_start = stage.evaluate(read_frame, 0.25)
    mobile_metrics = mobile_start["metrics"]
    assert mobile_metrics["layoutMode"] == "single-column"
    assert mobile_metrics["heightScale"] == 1
    assert mobile_metrics["viewBox"] == "0 0 1536 885"
    assert mobile_metrics["headLift"] == 0
    assert mobile_metrics["A1"] == {"x": 773, "y": 143}
    assert mobile_metrics["A2"] == {"x": 773, "y": 248}
    assert all(transform is None for transform in mobile_start["fixedTransforms"])

    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_function(
        """
        () => window.__pantograph?.metrics()?.layoutMode === "wide"
        """
    )
    wide = stage.evaluate(read_frame, 0.25)
    wide_metrics = wide["metrics"]

    original_height = 525 - 101
    wide_height = 525 - (101 + wide_metrics["headLift"])
    assert wide_metrics["layoutMode"] == "wide"
    assert wide_metrics["heightScale"] == pytest.approx(1.2, abs=1e-12)
    assert wide_metrics["viewBox"] == "0 0 1536 885"
    assert wide_height / original_height == pytest.approx(1.2, abs=1e-12)
    assert wide_metrics["headLift"] == pytest.approx(-84.8, abs=1e-12)
    assert wide_metrics["maxBarDrift"] < 1e-12
    assert wide_metrics["verticalLinkDrift"] < 1e-12
    assert set(wide["fixedTransforms"]) == {"translate(0 -84.8)"}

    for property_name in ("progress", "travel", "dx", "railDy", "extension"):
        assert wide_metrics[property_name] == pytest.approx(
            mobile_metrics[property_name],
            abs=1e-12,
        )
    for point_name in ("target", "tip", "c1", "c2"):
        assert wide_metrics[point_name] == pytest.approx(
            mobile_metrics[point_name],
            abs=1e-12,
        )
    assert wide_metrics["config"] == mobile_metrics["config"]
    assert wide_metrics["barLengths"] == mobile_metrics["barLengths"]

    page.set_viewport_size({"width": 800, "height": 900})
    page.wait_for_function(
        """
        () => window.__pantograph?.metrics()?.layoutMode === "single-column"
        """
    )
    mobile_return = stage.evaluate(read_frame, 0.25)
    assert mobile_return["metrics"] == mobile_metrics
    assert all(
        transform is None
        for transform in mobile_return["fixedTransforms"]
    )


def test_pantograph_uses_the_cropped_desktop_artwork_at_every_width(
    driver: DriverFactory,
) -> None:
    """Use the centered desktop crop in both wide and narrow layouts.

    The original 1536×1024 viewBox contains substantially more unused space
    below the static cards than above the raised desktop head. Centering the
    outer div therefore centered the SVG viewport but left the visible machine
    noticeably high. The 1536×885 crop removes that empty area without scaling
    or distorting any of the pantograph's individual parts.

    This regression first measures the visible wide artwork and requires its
    midpoint to coincide with the visual column. It then resizes to the
    single-column layout and requires the exact same 1536×885 viewBox to remain
    active there. Reintroducing the former mobile-only 1536×1024 viewport,
    switching viewBoxes after the first animation frame, or disturbing the
    established desktop centering makes the test fail.
    """

    desktop = driver(stored_theme="light", viewport=(1536, 900))
    expect(desktop.locator("#pantographArtwork")).to_have_count(1)
    desktop_geometry = desktop.locator(".pantograph-visual").evaluate(
        """
        (visual) => {
          const stage = visual.getBoundingClientRect();
          const artwork = visual.querySelector("#pantographArtwork");
          const head = artwork.querySelector(
            "rect[data-fixed-head='true']",
          ).getBoundingClientRect();
          const cards = [
            ...artwork.querySelectorAll(
              ':scope > g > rect[y="646"][height="224"]',
            ),
          ].map((card) => card.getBoundingClientRect());
          const contentBottom = Math.max(
            ...cards.map((card) => card.bottom),
          );
          return {
            overflow: getComputedStyle(visual).overflow,
            viewBox: artwork.getAttribute("viewBox"),
            cardCount: cards.length,
            centerDelta:
              (head.top + contentBottom) / 2
              - (stage.top + stage.height / 2),
          };
        }
        """
    )
    assert desktop_geometry["viewBox"] == "0 0 1536 885"
    assert desktop_geometry["cardCount"] == 8
    assert desktop_geometry["centerDelta"] == pytest.approx(0, abs=1)
    assert desktop_geometry["overflow"] == "visible"

    mobile = driver(stored_theme="light", viewport=(800, 900))
    expect(mobile.locator("#pantographArtwork")).to_have_count(1)
    mobile_geometry = mobile.locator(".pantograph-visual").evaluate(
        """
        (visual) => ({
          overflow: getComputedStyle(visual).overflow,
          viewBox: visual
            .querySelector("#pantographArtwork")
            .getAttribute("viewBox"),
        })
        """
    )
    assert mobile_geometry == {
        "overflow": "hidden",
        "viewBox": "0 0 1536 885",
    }


def test_thesis_precedes_containment_and_both_sections_are_renumbered(
    driver: DriverFactory,
) -> None:
    """Keep the visible section order and micro-numbering synchronized.

    The thesis and containment blocks were deliberately exchanged so the
    argument about stronger resistance now appears before the reactor analogy.
    Moving only their text labels would leave keyboard navigation, scrolling,
    and fragment targets in the old document order; moving only the DOM blocks
    would leave the on-page sequence numbered ``03`` then ``02``.

    This regression checks the first four real content sections as one ordered
    sequence and also verifies each swapped section's exact anchor label. It
    therefore fails if either block drifts back to its old position, if only
    their headings are exchanged, or if future reordering forgets to recalculate
    the visible section numbers.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    ordered_ids = page.locator(".section-shell[id]").evaluate_all(
        "(sections) => sections.map((section) => section.id)"
    )

    assert ordered_ids[:4] == [
        "complexity",
        "thesis",
        "containment",
        "what-we-build",
    ]
    expect(
        page.locator("#thesis > .section-index")
    ).to_have_text("02 / OUR THESIS")
    expect(
        page.locator("#containment .section-index")
    ).to_have_text("03 / CONTAINMENT")


@pytest.mark.parametrize("viewport_width", [580, 390, 280])
def test_mobile_containment_operators_are_centered_without_changing_desktop(
    driver: DriverFactory,
    viewport_width: int,
) -> None:
    """Center the stacked formula operators only in the narrow composition.

    At 580 pixels and below the containment formula changes from five columns
    to one. Its bordered terms already fill and center themselves, but the
    inline ``×`` and ``=`` elements kept the grid default and appeared at the
    far left of otherwise full-width rows. The desktop formula is a separate
    DOM copy and must retain its established five-column geometry.

    This regression checks three narrow widths, including the 280-pixel header
    edge case, and requires both operators to share the formula's horizontal
    center. It then opens an independent desktop page and verifies that only
    the desktop copy is visible, still has five columns, and its operators have
    not been stretched to the formula width. A broad selector that accidentally
    changes desktop styling therefore fails alongside a missing mobile fix.
    """

    mobile = driver(
        stored_theme="light",
        viewport=(viewport_width, 844),
        load_interactive_visuals=False,
    )
    mobile_geometry = mobile.locator(
        ".containment__formula--mobile"
    ).evaluate(
        """
        (formula) => {
          const rect = formula.getBoundingClientRect();
          return {
            display: getComputedStyle(formula).display,
            columns: getComputedStyle(formula).gridTemplateColumns,
            centerX: rect.left + rect.width / 2,
            operators: [...formula.querySelectorAll("b")].map((operator) => {
              const operatorRect = operator.getBoundingClientRect();
              return {
                text: operator.textContent.trim(),
                centerX: operatorRect.left + operatorRect.width / 2,
                textAlign: getComputedStyle(operator).textAlign,
              };
            }),
          };
        }
        """
    )
    assert mobile_geometry["display"] == "grid"
    assert len(mobile_geometry["columns"].split()) == 1
    assert [operator["text"] for operator in mobile_geometry["operators"]] == [
        "×",
        "=",
    ]
    for operator in mobile_geometry["operators"]:
        assert operator["centerX"] == pytest.approx(
            mobile_geometry["centerX"],
            abs=0.5,
        )
        assert operator["textAlign"] == "center"

    desktop = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    desktop_geometry = desktop.locator("#containment").evaluate(
        """
        (section) => {
          const desktopFormula = section.querySelector(
            ".containment__formula--desktop",
          );
          const mobileFormula = section.querySelector(
            ".containment__formula--mobile",
          );
          const formulaRect = desktopFormula.getBoundingClientRect();
          return {
            desktopDisplay: getComputedStyle(desktopFormula).display,
            desktopColumns:
              getComputedStyle(desktopFormula).gridTemplateColumns,
            mobileDisplay: getComputedStyle(mobileFormula).display,
            formulaWidth: formulaRect.width,
            operatorWidths: [...desktopFormula.querySelectorAll("b")].map(
              (operator) => operator.getBoundingClientRect().width,
            ),
          };
        }
        """
    )
    assert desktop_geometry["desktopDisplay"] == "grid"
    assert len(desktop_geometry["desktopColumns"].split()) == 5
    assert desktop_geometry["mobileDisplay"] == "none"
    assert all(
        width < desktop_geometry["formulaWidth"] / 10
        for width in desktop_geometry["operatorWidths"]
    )


def test_desktop_manifesto_places_visual_left_of_stacked_copy(
    driver: DriverFactory,
) -> None:
    """Place the thesis illustration left of one coherent desktop copy column.

    The thesis originally consisted only of a heading on the left and prose on
    the right. Its new transparent illustration must occupy the full left
    column while the section index, heading, and all paragraphs form a single
    right-hand stack with the same 24/26-pixel vertical rhythm used by other
    illustrated sections. This test measures rendered geometry rather than
    source order: the visual must end before the copy begins, all copy labels
    must share one left edge, and the expected gaps must remain intact.

    A regression that restores the old three-column composition, moves the
    image to the right, separates the heading from the body, or disturbs the
    established spacing will therefore fail without constraining the separate
    one-column mobile presentation.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    geometry = page.locator("#thesis").evaluate(
        """
        (section) => {
          const anchor = section.querySelector(":scope > .section-index");
          const heading = section.querySelector(".manifesto__headline");
          const body = section.querySelector(".manifesto__body");
          const visual = section.querySelector(".manifesto__visual");
          const anchorRect = anchor.getBoundingClientRect();
          const headingRect = heading.getBoundingClientRect();
          const bodyRect = body.getBoundingClientRect();
          const visualRect = visual.getBoundingClientRect();
          return {
            anchorLeft: anchorRect.left,
            anchorBottom: anchorRect.bottom,
            headingLeft: headingRect.left,
            headingTop: headingRect.top,
            headingBottom: headingRect.bottom,
            bodyLeft: bodyRect.left,
            bodyTop: bodyRect.top,
            visualRight: visualRect.right,
          };
        }
        """
    )

    assert geometry["headingLeft"] == pytest.approx(
        geometry["anchorLeft"],
        abs=0.5,
    )
    assert geometry["bodyLeft"] == pytest.approx(
        geometry["anchorLeft"],
        abs=0.5,
    )
    assert geometry["headingTop"] - geometry["anchorBottom"] == pytest.approx(
        24,
        abs=0.5,
    )
    assert geometry["bodyTop"] - geometry["headingBottom"] == pytest.approx(
        26,
        abs=0.5,
    )
    assert geometry["visualRight"] < geometry["anchorLeft"]


def test_desktop_pillars_index_sits_directly_above_left_aligned_heading(
    driver: DriverFactory,
) -> None:
    """Keep the desktop What We Build introduction in one left column.

    The section previously used a two-column intro grid: its anchor occupied a
    narrow column at the section edge while the heading began hundreds of
    pixels farther right. The intended desktop composition places
    ``04 / WHAT WE BUILD`` directly above the heading, with both elements
    sharing the section's left boundary and the same 24-pixel vertical rhythm
    already used by other section indices and headings.

    This regression measures rendered coordinates instead of relying on source
    order alone. It fails if the old ``0.42fr / 1.58fr`` split returns, if the
    anchor and title drift onto different horizontal axes, or if a responsive
    override introduces an excessive gap between them.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    geometry = page.locator("#what-we-build").evaluate(
        """
        (section) => {
          const intro = section.querySelector(".pillars__intro");
          const anchor = intro.querySelector(".section-index");
          const heading = intro.querySelector("h2");
          const sectionRect = section.getBoundingClientRect();
          const anchorRect = anchor.getBoundingClientRect();
          const headingRect = heading.getBoundingClientRect();
          const style = getComputedStyle(intro);
          return {
            columns: style.gridTemplateColumns,
            columnCount: style.gridTemplateColumns.split(" ").length,
            introWidth: intro.getBoundingClientRect().width,
            sectionLeft: sectionRect.left,
            anchorLeft: anchorRect.left,
            headingLeft: headingRect.left,
            verticalGap: headingRect.top - anchorRect.bottom,
          };
        }
        """
    )

    assert geometry["columnCount"] == 1
    assert float(geometry["columns"].removesuffix("px")) == pytest.approx(
        geometry["introWidth"],
        abs=0.5,
    )
    assert geometry["anchorLeft"] == pytest.approx(
        geometry["sectionLeft"],
        abs=0.5,
    )
    assert geometry["headingLeft"] == pytest.approx(
        geometry["sectionLeft"],
        abs=0.5,
    )
    assert geometry["headingLeft"] == pytest.approx(
        geometry["anchorLeft"],
        abs=0.5,
    )
    assert geometry["verticalGap"] == pytest.approx(24, abs=0.5)


@pytest.mark.parametrize(
    ("theme", "viewport"),
    [
        ("light", (1440, 900)),
        ("dark", (1440, 900)),
        ("light", (390, 844)),
        ("dark", (390, 844)),
    ],
    ids=["desktop-light", "desktop-dark", "phone-light", "phone-dark"],
)
def test_pillar_illustrations_are_transparent_local_assets_that_fit_cards(
    driver: DriverFactory,
    theme: str,
    viewport: tuple[int, int],
) -> None:
    """Render the three new pillar illustrations safely in every theme.

    Each What We Build card must use its intended local PNG rather than the old
    text glyph or a temporary clipboard path. The PNGs must retain a real alpha
    channel with transparent corners, load successfully in Chromium, and remain
    fully contained by their cards on both desktop and phone layouts. Running
    the same geometry checks in light and dark themes protects against a
    theme-specific background, filter, or sizing rule that could reintroduce a
    rectangular matte, crop an illustration, or let it cover the card copy.
    """

    expected_sources = [
        "/pillar-verifiability-v1.png",
        "/pillar-interpretability-v1.png",
        "/pillar-testing-speed-v1.png",
    ]

    for source in expected_sources:
        with Image.open(PROJECT_ROOT / "public" / source.removeprefix("/")) as image:
            assert image.mode == "RGBA"
            alpha = image.getchannel("A")
            assert alpha.getextrema() == (0, 255)
            assert all(
                alpha.getpixel(corner) == 0
                for corner in [
                    (0, 0),
                    (image.width - 1, 0),
                    (0, image.height - 1),
                    (image.width - 1, image.height - 1),
                ]
            )

    page = driver(
        stored_theme=theme,
        viewport=viewport,
        load_interactive_visuals=False,
    )
    rendered = page.locator("#what-we-build").evaluate(
        """
        (section) => ({
          theme: document.documentElement.dataset.theme,
          cards: [...section.querySelectorAll(".pillar-card")].map((card) => {
            const image = card.querySelector(".pillar-card__visual img");
            const cardRect = card.getBoundingClientRect();
            const imageRect = image.getBoundingClientRect();
            return {
              source: image.getAttribute("src"),
              complete: image.complete,
              naturalWidth: image.naturalWidth,
              naturalHeight: image.naturalHeight,
              cardBackground: getComputedStyle(card).backgroundColor,
              imageBackground: getComputedStyle(image).backgroundColor,
              contained:
                imageRect.left >= cardRect.left - 0.5
                && imageRect.right <= cardRect.right + 0.5
                && imageRect.top >= cardRect.top - 0.5
                && imageRect.bottom <= cardRect.bottom + 0.5,
            };
          }),
        })
        """
    )

    assert rendered["theme"] == theme
    assert [card["source"] for card in rendered["cards"]] == expected_sources
    assert all(card["complete"] for card in rendered["cards"])
    assert all(card["naturalWidth"] > 0 for card in rendered["cards"])
    assert all(card["naturalHeight"] > 0 for card in rendered["cards"])
    assert all(card["contained"] for card in rendered["cards"])
    assert all(
        card["imageBackground"] == "rgba(0, 0, 0, 0)"
        for card in rendered["cards"]
    )
    assert all(
        card["cardBackground"] != "rgba(0, 0, 0, 0)"
        for card in rendered["cards"]
    )


def test_distribution_mobile_order_places_visual_before_numbered_list(
    driver: DriverFactory,
) -> None:
    """Reorder only the single-column distribution section by visual position.

    On desktop the copy—including its numbered list—must remain one intact
    left column beside the pantograph. Below the 860 px breakpoint the desired
    reading sequence is different: section index, heading and explanatory
    paragraph; then the complete illustration; then the numbered list. Merely
    moving the list in JSX would also alter the established two-column layout,
    while relying on source order alone previously left the list above the
    illustration on phones.

    This test measures rendered rectangles rather than checking DOM order, so
    it catches missing ``display: contents``, ineffective grid ``order`` rules,
    and later margin/grid changes that cause the blocks to overlap. A second
    desktop context verifies the responsive rule does not flatten the copy
    wrapper or disturb the two-column structure at wide widths.
    """

    mobile = driver(stored_theme="light", viewport=(800, 1100))
    mobile_layout = mobile.locator("#scale").evaluate(
        """
        (section) => {
          const copy = section.querySelector(".distribution__copy");
          const heading = copy.querySelector("h2");
          const paragraph = copy.querySelector(":scope > p");
          const visual = section.querySelector(".pantograph-visual");
          const list = copy.querySelector("ul");
          const rect = (element) => {
            const bounds = element.getBoundingClientRect();
            return {top: bounds.top, bottom: bounds.bottom};
          };
          return {
            copyDisplay: getComputedStyle(copy).display,
            gridColumns: getComputedStyle(section).gridTemplateColumns,
            orders: {
              heading: getComputedStyle(heading).order,
              paragraph: getComputedStyle(paragraph).order,
              visual: getComputedStyle(visual).order,
              list: getComputedStyle(list).order,
            },
            heading: rect(heading),
            paragraph: rect(paragraph),
            visual: rect(visual),
            list: rect(list),
          };
        }
        """
    )
    assert mobile_layout["copyDisplay"] == "contents"
    assert mobile_layout["orders"] == {
        "heading": "2",
        "paragraph": "3",
        "visual": "4",
        "list": "5",
    }
    assert mobile_layout["heading"]["bottom"] <= (
        mobile_layout["paragraph"]["top"]
    )
    assert mobile_layout["paragraph"]["bottom"] < (
        mobile_layout["visual"]["top"]
    )
    assert mobile_layout["visual"]["bottom"] < mobile_layout["list"]["top"]

    desktop = driver(stored_theme="light", viewport=(1440, 900))
    desktop_layout = desktop.locator("#scale").evaluate(
        """
        (section) => {
          const copy = section.querySelector(".distribution__copy");
          const visual = section.querySelector(".pantograph-visual");
          const copyRect = copy.getBoundingClientRect();
          const visualRect = visual.getBoundingClientRect();
          return {
            copyDisplay: getComputedStyle(copy).display,
            gridColumns: getComputedStyle(section).gridTemplateColumns,
            copyRight: copyRect.right,
            visualLeft: visualRect.left,
            visualMarginTop: getComputedStyle(visual).marginTop,
          };
        }
        """
    )
    assert desktop_layout["copyDisplay"] != "contents"
    assert desktop_layout["gridColumns"].split(" ")
    assert len(desktop_layout["gridColumns"].split(" ")) == 2
    assert desktop_layout["copyRight"] < desktop_layout["visualLeft"]
    assert desktop_layout["visualMarginTop"] == "0px"


@pytest.mark.parametrize(
    "viewport",
    [
        (800, 1100),
        (700, 1000),
        (600, 950),
        (580, 900),
        (478, 900),
        (390, 844),
        (320, 800),
        (280, 700),
    ],
    ids=[
        "single-column-wide",
        "single-column-medium",
        "single-column-compact",
        "mobile-wide",
        "reference",
        "phone",
        "narrow",
        "minimum",
    ],
)
def test_mobile_pantograph_has_reduced_symmetric_visible_spacing(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Center the *visible* pantograph between its paragraph and list.

    The embedded SVG deliberately uses the same cropped desktop viewBox at
    every breakpoint. Its actual drawing, however, does not occupy that
    viewport symmetrically: the raised pantograph head leaves a different
    amount of transparent SVG space above and below the visible machine.
    Merely giving the outer container equal CSS margins therefore produced the
    reported mobile defect—considerably more blank space above the illustration
    than below it—and a tall fixed container made both gaps unnecessarily large.

    For each supported single-column width this regression measures the first
    visible head plate and the bottoms of all eight worker cards, rather than
    trusting the invisible SVG or container bounds. Between 581 and 860 px the
    previous 520 px stage produced very large unequal gaps; its responsive
    replacement is exactly the reduced ``clamp(450px, 14vw + 366px, 486px)``
    stage with equal 32 px outer margins. At 580 px and below the already
    approved compact stage and equal 24 px margins remain unchanged.

    Painted bounds must sit equally far from the preceding paragraph and the
    following numbered list. The test therefore fails if the old 520 px stage
    returns, if only one outer margin is changed, if the artwork loses its
    compensating vertical offset, or if a future responsive rule reintroduces
    asymmetric transparent padding at any narrow or intermediate width.
    """

    page = driver(stored_theme="light", viewport=viewport)
    section = page.locator("#scale")
    expect(section.locator("#pantographArtwork")).to_have_count(1)

    geometry = section.evaluate(
        """
        (section) => {
          const copy = section.querySelector(".distribution__copy");
          const paragraph = copy.querySelector(":scope > p");
          const list = copy.querySelector(":scope > ul");
          const visual = section.querySelector(".pantograph-visual");
          const artwork = visual.querySelector("#pantographArtwork");
          const headParts = [
            ...artwork.querySelectorAll(
              "rect[data-fixed-head='true']",
            ),
          ].map((part) => part.getBoundingClientRect());
          const cards = [
            ...artwork.querySelectorAll(
              ':scope > g > rect[y="646"][height="224"]',
            ),
          ].map((card) => card.getBoundingClientRect());
          const paragraphRect = paragraph.getBoundingClientRect();
          const listRect = list.getBoundingClientRect();
          const paintedTop = Math.min(
            ...headParts.map((part) => part.top),
          );
          const paintedBottom = Math.max(
            ...cards.map((card) => card.bottom),
          );

          return {
            headPartCount: headParts.length,
            cardCount: cards.length,
            visualHeight: visual.getBoundingClientRect().height,
            visualMarginTop: parseFloat(getComputedStyle(visual).marginTop),
            listMarginTop: parseFloat(getComputedStyle(list).marginTop),
            topGap: paintedTop - paragraphRect.bottom,
            bottomGap: listRect.top - paintedBottom,
          };
        }
        """
    )

    assert geometry["headPartCount"] > 0
    assert geometry["cardCount"] == 8
    width = viewport[0]
    if width > 580:
        expected_margin = 32
        expected_height = min(486, max(450, width * 0.14 + 366))
        maximum_gap = 105
    else:
        expected_margin = 24
        expected_height = min(330, max(196, width * 0.70))
        maximum_gap = 70

    assert geometry["visualMarginTop"] == pytest.approx(
        expected_margin,
        abs=0.1,
    )
    assert geometry["listMarginTop"] == pytest.approx(
        expected_margin,
        abs=0.1,
    )
    assert geometry["visualHeight"] == pytest.approx(
        expected_height,
        abs=0.5,
    )
    assert geometry["topGap"] == pytest.approx(
        geometry["bottomGap"],
        abs=2,
    )
    assert max(geometry["topGap"], geometry["bottomGap"]) <= maximum_gap


def test_coexistence_card_flips_to_an_identical_reversed_face(
    driver: DriverFactory,
) -> None:
    """Flip the complete HUMAN/ROBOT card while changing only word order.

    The front must retain the approved pill geometry with HUMAN on the left,
    ROBOT on the right, the centered exchange arrow, and the existing shared
    evidence caption. Clicking anywhere on the card should rotate the complete
    object in 3D and expose a second, visually identical face whose only content
    difference is the reversed ROBOT/HUMAN order. A second click must restore
    the original side rather than stacking another transform or changing size.

    This test captures the rendered rectangle and all visual face properties,
    performs both clicks, and checks the active face after each completed
    transition. It catches a simple text swap with no flip, a square back face,
    shifted arrow or caption, altered border/background/shadow, card growth
    during rotation, and one-way state implementations that cannot return to
    the initial presentation.
    """

    page = driver(stored_theme="light", viewport=(1280, 800))
    card = page.get_by_role(
        "button",
        name="HUMAN and ROBOT. Flip to place ROBOT first.",
        exact=True,
    )
    expect(card).to_be_visible()

    read_card = """
      (card) => {
        const activeFace = card.querySelector(
          ".coexistence__pair-face[aria-hidden='false']",
        );
        const faceStyle = getComputedStyle(activeFace);
        const innerStyle = getComputedStyle(
          card.querySelector(".coexistence__pair-inner"),
        );
        const rect = card.getBoundingClientRect();
        return {
          width: rect.width,
          height: rect.height,
          words: [...activeFace.querySelectorAll(":scope > span")]
            .map((element) => element.textContent),
          arrow: activeFace.querySelector(":scope > i").textContent,
          caption: activeFace.querySelector(":scope > small").textContent,
          face: {
            background: faceStyle.backgroundColor,
            borderColor: faceStyle.borderColor,
            borderRadius: faceStyle.borderRadius,
            boxShadow: faceStyle.boxShadow,
            padding: faceStyle.padding,
          },
          innerTransform: innerStyle.transform,
          transitionDuration: innerStyle.transitionDuration,
        };
      }
    """
    front = card.evaluate(read_card)
    assert front["words"] == ["HUMAN", "ROBOT"]
    assert front["arrow"] == "⇄"
    assert front["caption"] == "SHARED GROUND: EVIDENCE"
    assert front["transitionDuration"] == "0.72s"

    card.click()
    reversed_card = page.get_by_role(
        "button",
        name="ROBOT and HUMAN. Flip to place HUMAN first.",
        exact=True,
    )
    expect(reversed_card).to_have_attribute("aria-pressed", "true")
    page.wait_for_timeout(800)
    back = reversed_card.evaluate(read_card)

    assert back["words"] == ["ROBOT", "HUMAN"]
    assert back["arrow"] == front["arrow"]
    assert back["caption"] == front["caption"]
    assert back["face"] == front["face"]
    assert back["width"] == pytest.approx(front["width"], abs=0.01)
    assert back["height"] == pytest.approx(front["height"], abs=0.01)
    assert back["innerTransform"] != "none"

    reversed_card.click()
    original_card = page.get_by_role(
        "button",
        name="HUMAN and ROBOT. Flip to place ROBOT first.",
        exact=True,
    )
    expect(original_card).to_have_attribute("aria-pressed", "false")
    page.wait_for_timeout(800)
    restored = original_card.evaluate(read_card)

    assert restored["words"] == front["words"]
    assert restored["face"] == front["face"]
    assert restored["width"] == pytest.approx(front["width"], abs=0.01)
    assert restored["height"] == pytest.approx(front["height"], abs=0.01)
    assert restored["innerTransform"] == "none"


@pytest.mark.parametrize(
    "viewport",
    [(800, 1000), (580, 900), (390, 844), (280, 700)],
    ids=["single-column", "mobile-wide", "phone", "minimum"],
)
def test_mobile_feedback_quote_follows_the_animated_diagram(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Place the feedback quote after the diagram only in narrow layouts.

    The desktop section intentionally keeps the rotating loop in the left
    column and all prose—including the quote—in the right column. In the
    single-column presentation the copy needs a different reading sequence:
    section label, heading, both explanatory paragraphs, animated diagram, and
    finally the quote. Leaving the quote inside the copy wrapper previously
    forced it to render above the diagram, as shown in the reported phone
    layout.

    This regression checks rendered coordinates at the full single-column
    breakpoint and several phone widths instead of relying only on source order
    or CSS declarations. It also verifies the explicit grid orders so later
    wrapper changes cannot accidentally make the geometry pass through overlap.
    Reverting ``display: contents``, restoring the old visual order, moving the
    quote ahead of the loop, or allowing any adjacent block to overlap makes
    the test fail. A desktop assertion protects the established two-column
    composition from these mobile-only rules.
    """

    page = driver(stored_theme="light", viewport=viewport)
    layout = page.locator("#feedback").evaluate(
        """
        (section) => {
          const copy = section.querySelector(".feedback__copy");
          const index = copy.querySelector(".section-index");
          const heading = copy.querySelector("h2");
          const paragraphs = [
            ...copy.querySelectorAll(":scope > p:not(.section-index)"),
          ];
          const visual = section.querySelector(".feedback__visual");
          const quote = copy.querySelector("blockquote");
          const rect = (element) => {
            const bounds = element.getBoundingClientRect();
            return {top: bounds.top, bottom: bounds.bottom};
          };

          return {
            copyDisplay: getComputedStyle(copy).display,
            gridColumns: getComputedStyle(section).gridTemplateColumns,
            orders: {
              index: getComputedStyle(index).order,
              heading: getComputedStyle(heading).order,
              firstParagraph: getComputedStyle(paragraphs[0]).order,
              secondParagraph: getComputedStyle(paragraphs[1]).order,
              visual: getComputedStyle(visual).order,
              quote: getComputedStyle(quote).order,
            },
            index: rect(index),
            heading: rect(heading),
            firstParagraph: rect(paragraphs[0]),
            secondParagraph: rect(paragraphs[1]),
            visual: rect(visual),
            quote: rect(quote),
          };
        }
        """
    )

    assert layout["copyDisplay"] == "contents"
    assert len(layout["gridColumns"].split(" ")) == 1
    assert layout["orders"] == {
        "index": "1",
        "heading": "2",
        "firstParagraph": "3",
        "secondParagraph": "4",
        "visual": "5",
        "quote": "6",
    }
    sequence = [
        layout["index"],
        layout["heading"],
        layout["firstParagraph"],
        layout["secondParagraph"],
        layout["visual"],
        layout["quote"],
    ]
    for current, following in zip(
        sequence[:-1],
        sequence[1:],
        strict=True,
    ):
        assert current["bottom"] <= following["top"]

    desktop = driver(stored_theme="light", viewport=(1440, 900))
    desktop_layout = desktop.locator("#feedback").evaluate(
        """
        (section) => {
          const copy = section.querySelector(".feedback__copy");
          const visual = section.querySelector(".feedback__visual");
          const copyRect = copy.getBoundingClientRect();
          const visualRect = visual.getBoundingClientRect();
          return {
            copyDisplay: getComputedStyle(copy).display,
            columnCount:
              getComputedStyle(section).gridTemplateColumns.split(" ").length,
            visualRight: visualRect.right,
            copyLeft: copyRect.left,
          };
        }
        """
    )
    assert desktop_layout["copyDisplay"] != "contents"
    assert desktop_layout["columnCount"] == 2
    assert desktop_layout["visualRight"] < desktop_layout["copyLeft"]


def test_feedback_orbits_share_one_continuous_rotation_and_centered_cards(
    driver: DriverFactory,
) -> None:
    """Keep the feedback diagram moving as one synchronized circular object.

    Both dashed/dotted rings, the four circular card borders, and the four
    tangential arrows represent one loop, so they must use the same clockwise
    direction, linear angular velocity, infinite duration, and instantaneous
    rotation phase. The arrows must also stay softly translucent so they remain
    visible without competing with the four labels. Previously the large rings
    used different 36 s and 24 s periods, one ran in reverse, and the arrows
    remained fixed in four corners.
    A later circular-card treatment left the small dashed borders stationary,
    which made them feel detached from the rotating system. The four text cards
    also once aligned their numbers and labels to the left instead of their own
    geometric centers.

    This regression reads all three animation contracts in a single rendered
    frame, making the comparison independent of machine speed and screenshot
    timing. It requires matching animation names, periods, directions, easing,
    iteration counts, play states, and transform matrices for the outer ring,
    inner ring, and arrow layer. It then compares the horizontal center of each
    card with both its number and label. Reintroducing a reverse direction,
    mismatched angular speed, static arrows or small borders, a finite
    animation, phase drift, or left-aligned card content makes the test fail.
    """

    page = driver(stored_theme="light", viewport=(1440, 900))
    motion = page.locator(".feedback-loop").evaluate(
        """
        (loop) => {
          const readAnimation = (style) => ({
            name: style.animationName,
            duration: style.animationDuration,
            direction: style.animationDirection,
            timing: style.animationTimingFunction,
            iterations: style.animationIterationCount,
            state: style.animationPlayState,
            transform: style.transform,
          });
          const outer = readAnimation(getComputedStyle(loop, "::before"));
          const inner = readAnimation(getComputedStyle(loop, "::after"));
          const arrows = readAnimation(
            getComputedStyle(loop.querySelector(".feedback-arrows")),
          );
          arrows.opacity = getComputedStyle(
            loop.querySelector(".feedback-arrows"),
          ).opacity;
          const cards = [...loop.querySelectorAll(".feedback-node")]
            .map((card) => {
              const cardRect = card.getBoundingClientRect();
              const numberRect = card.querySelector("span")
                .getBoundingClientRect();
              const labelRect = card.querySelector("strong")
                .getBoundingClientRect();
              return {
                textAlign: getComputedStyle(card).textAlign,
                border: readAnimation(getComputedStyle(card, "::after")),
                cardCenter: cardRect.left + cardRect.width / 2,
                numberCenter: numberRect.left + numberRect.width / 2,
                labelCenter: labelRect.left + labelRect.width / 2,
              };
            });
          return {outer, inner, arrows, cards};
        }
        """
    )

    expected_animation = {
        "name": "spin",
        "duration": "24s",
        "direction": "normal",
        "timing": "linear",
        "iterations": "infinite",
        "state": "running",
    }
    for animated_part in ("outer", "inner", "arrows"):
        contract = motion[animated_part]
        for property_name, expected_value in expected_animation.items():
            assert contract[property_name] == expected_value

    assert motion["outer"]["transform"] == motion["inner"]["transform"]
    assert motion["outer"]["transform"] == motion["arrows"]["transform"]
    assert motion["outer"]["transform"] != "none"
    assert float(motion["arrows"]["opacity"]) == pytest.approx(
        0.55,
        abs=0.001,
    )

    assert len(motion["cards"]) == 4
    for card in motion["cards"]:
        for property_name, expected_value in expected_animation.items():
            assert card["border"][property_name] == expected_value
        assert card["border"]["transform"] == motion["outer"]["transform"]
        assert card["textAlign"] == "center"
        assert card["numberCenter"] == pytest.approx(
            card["cardCenter"],
            abs=0.1,
        )
        assert card["labelCenter"] == pytest.approx(
            card["cardCenter"],
            abs=0.1,
        )


def test_feedback_geometry_uses_two_dashed_rings_and_one_shared_middle_orbit(
    driver: DriverFactory,
) -> None:
    """Keep the simplified feedback illustration on one exact radial system.

    The intended composition has only two dashed circles. The larger one must
    replace the removed solid outer border while preserving the illustration's
    original 610 px footprint, and the second circle must be enlarged by the
    same scale factor. Every text card and every arrow then belongs on the
    geometric midpoint between those two radii. The four text containers must
    themselves be true circles, not rounded rectangles: equal rendered width
    and height, a 50% radius, and exactly the same dashed pink border treatment
    as the larger rings. Their interiors use a 50%-alpha surface with no
    backdrop blur: this halves the strength of arrows passing underneath
    without erasing them as the former 20 px filter did. Readability is
    protected locally by a background-colored shadow around the number and
    label glyphs instead of by obscuring the whole circular surface. A separate
    pseudo-element
    continues to reuse the center's radial pink halo, and the proportional
    diameter must keep shrinking with the whole illustration on narrow screens.

    The arrows are deliberately SVG geometry rather than font glyphs: all four
    must reuse one thin circular arc and one matching open arrowhead, rotated
    by quarter turns. This matters because the former Unicode arrows varied in
    weight and curvature, while a nearly straight line merely positioned near
    a circle could still look tangential without actually following its radius.

    The assertions read only computed geometry and SVG path data, so they do
    not depend on animation timing or screenshot antialiasing. Restoring the
    solid border, shrinking either dashed ring, restoring rectangular,
    fully opaque or fully transparent cards, restoring a backdrop blur,
    removing their halos or label shadows, changing their dash color,
    placing them off the shared middle orbit, using different arrow shapes, or
    replacing the circular ``A 39 39`` arc with a straight segment makes the
    regression fail.
    """

    page = driver(stored_theme="light", viewport=(1440, 900))
    geometry = page.locator(".feedback-loop").evaluate(
        """
        (loop) => {
          const loopStyle = getComputedStyle(loop);
          const outerStyle = getComputedStyle(loop, "::before");
          const innerStyle = getComputedStyle(loop, "::after");
          const loopRect = loop.getBoundingClientRect();
          const center = {
            x: loopRect.left + loopRect.width / 2,
            y: loopRect.top + loopRect.height / 2,
          };
          const alphaOf = (color) => {
            const slashAlpha = color.match(/\\/\\s*([\\d.]+)\\s*\\)$/);
            if (slashAlpha) return Number.parseFloat(slashAlpha[1]);
            const rgbaAlpha = color.match(
              /rgba\\([^,]+,[^,]+,[^,]+,\\s*([\\d.]+)\\)/,
            );
            return rgbaAlpha ? Number.parseFloat(rgbaAlpha[1]) : 1;
          };
          const radiusOf = (element) => {
            const rect = element.getBoundingClientRect();
            const x = rect.left + rect.width / 2 - center.x;
            const y = rect.top + rect.height / 2 - center.y;
            return Math.hypot(x, y);
          };
          const arrowLayer = loop.querySelector(".feedback-arrows");
          const headerStyle = getComputedStyle(
            document.querySelector(".site-header"),
          );
          const cards = [...loop.querySelectorAll(".feedback-node")]
            .map((card) => {
              const rect = card.getBoundingClientRect();
              const style = getComputedStyle(card);
              const borderStyle = getComputedStyle(card, "::after");
              const haloStyle = getComputedStyle(card, "::before");
              return {
                radius: radiusOf(card),
                width: rect.width,
                height: rect.height,
                background: style.backgroundColor,
                backgroundAlpha: alphaOf(style.backgroundColor),
                backdropFilter: style.backdropFilter,
                borderStyle: borderStyle.borderTopStyle,
                borderColor: borderStyle.borderTopColor,
                borderRadius: borderStyle.borderTopLeftRadius,
                haloImage: haloStyle.backgroundImage,
                numberShadow: getComputedStyle(
                  card.querySelector("span"),
                ).textShadow,
                labelShadow: getComputedStyle(
                  card.querySelector("strong"),
                ).textShadow,
              };
            });
          const arrows = [...arrowLayer.querySelectorAll(".feedback-arrow")]
            .map((arrow) => {
              const paths = [...arrow.querySelectorAll("path")];
              return {
                transform: arrow.getAttribute("transform"),
                shaft: paths[0]?.getAttribute("d"),
                head: paths[1]?.getAttribute("d"),
                strokeWidth: getComputedStyle(arrow).strokeWidth,
              };
            });

          return {
            width: loopRect.width,
            borderWidth: loopStyle.borderTopWidth,
            outer: {
              inset: Number.parseFloat(outerStyle.top),
              style: outerStyle.borderTopStyle,
              color: outerStyle.borderTopColor,
            },
            inner: {
              inset: Number.parseFloat(innerStyle.top),
              style: innerStyle.borderTopStyle,
            },
            headerGlass: {
              background: headerStyle.backgroundColor,
              backgroundAlpha: alphaOf(headerStyle.backgroundColor),
              backdropFilter: headerStyle.backdropFilter,
            },
            arrowZIndex: getComputedStyle(arrowLayer).zIndex,
            cards,
            arrows,
            arrowText: arrowLayer.textContent.trim(),
          };
        }
        """
    )

    assert geometry["borderWidth"] == "0px"
    assert geometry["outer"]["style"] == "dashed"
    assert geometry["inner"]["style"] == "dashed"
    assert geometry["outer"]["inset"] == pytest.approx(0, abs=0.1)
    assert geometry["inner"]["inset"] == pytest.approx(
        geometry["width"] * 0.2143,
        abs=0.2,
    )

    outer_radius = geometry["width"] / 2 - geometry["outer"]["inset"]
    inner_radius = geometry["width"] / 2 - geometry["inner"]["inset"]
    middle_radius = (outer_radius + inner_radius) / 2
    assert len(geometry["cards"]) == 4
    for card in geometry["cards"]:
        assert card["radius"] == pytest.approx(middle_radius, abs=0.3)
        assert card["width"] == pytest.approx(card["height"], abs=0.1)
        assert card["background"] != "rgba(0, 0, 0, 0)"
        assert card["background"] != geometry["headerGlass"]["background"]
        assert card["backgroundAlpha"] == pytest.approx(0.5, abs=0.001)
        assert card["backdropFilter"] == "none"
        assert card["numberShadow"] != "none"
        assert card["labelShadow"] == card["numberShadow"]
        assert card["borderStyle"] == "dashed"
        assert card["borderColor"] == geometry["outer"]["color"]
        assert card["borderRadius"] == "50%"
        assert "radial-gradient" in card["haloImage"]
    assert int(geometry["arrowZIndex"]) < 4

    assert geometry["arrowText"] == ""
    assert [arrow["transform"] for arrow in geometry["arrows"]] == [
        "rotate(0 50 50)",
        "rotate(90 50 50)",
        "rotate(180 50 50)",
        "rotate(270 50 50)",
    ]
    assert len({arrow["shaft"] for arrow in geometry["arrows"]}) == 1
    assert len({arrow["head"] for arrow in geometry["arrows"]}) == 1
    assert len({arrow["strokeWidth"] for arrow in geometry["arrows"]}) == 1
    assert "A 39 39" in geometry["arrows"][0]["shaft"]


def test_hero_muta_uses_a_fidelity_preserving_high_density_asset(
    driver: DriverFactory,
) -> None:
    """Keep the hero illustration crisp without changing its visible design.

    The original 1254 px transparent PNG was only marginally larger than the
    650 CSS-pixel hero card, so a Retina display could expose its source pixels.
    The replacement must provide exactly twice the width and height while
    retaining the same square canvas, transparent background, and alpha bounds.
    Those bounds matter because even a small change to the transparent padding
    would make Muta appear to move or resize inside the otherwise unchanged
    hero composition.

    Resolution alone is not enough: a generative redraw could satisfy the
    dimensions while silently changing the silhouette, teeth, highlights, or
    colors. The test therefore downsamples the new lossless asset and composites
    both versions over the site's light and dark background extremes. Their
    mean channel difference must remain below a little more than one 8-bit
    color step, proving this is a conservative reconstruction rather than new
    artwork.

    A literal Lanczos resize enlarged the source's uneven eye contour and the
    striping in its glossy highlights. The reconstructed 2× asset must differ
    measurably from that naive interpolation before it is downsampled, ensuring
    a future replacement cannot silently restore the artifact-bearing version.
    Conversely, the very small downsampled difference above protects Muta's
    actual design. The alpha channel is also checked for isolated translucent
    pixels outside the antialiased silhouette: reconstructed colors beneath
    such pixels would otherwise appear as horizontal streaks on some renderers.

    Both density candidates must come from the reconstructed artwork. A prior
    regression left the legacy PNG as ``src`` and the 1× ``srcset`` candidate,
    so standard-density displays and some browser zoom levels silently showed
    the obsolete artifact-bearing image even though Retina displays looked
    correct. The test checks the actual ``currentSrc`` at both density factors
    to make that fallback impossible.

    The 2× browser must still retain the 1254 px intrinsic CSS size, the
    existing ``contain`` fit, and 50% 46% positioning. This catches a density
    descriptor mistake that would visibly resize the hero after replacement.
    """

    original_path = PROJECT_ROOT / "public/muta-v2-transparent.png"
    standard_path = PROJECT_ROOT / "public/muta-v2-transparent-v10.webp"
    upscaled_path = PROJECT_ROOT / "public/muta-v2-transparent-2x-v10.webp"

    with (
        Image.open(original_path).convert("RGBA") as original,
        Image.open(standard_path).convert("RGBA") as standard,
        Image.open(upscaled_path).convert("RGBA") as upscaled,
    ):
        assert standard.size == original.size
        assert upscaled.size == (
            original.width * 2,
            original.height * 2,
        )
        assert upscaled.getchannel("A").getbbox() == tuple(
            coordinate * 2
            for coordinate in original.getchannel("A").getbbox()
        )
        upscaled_alpha = upscaled.getchannel("A")
        opaque_alpha_core = upscaled_alpha.point(
            lambda value: 255 if value >= 64 else 0,
        )
        valid_antialias_neighborhood = opaque_alpha_core.filter(
            ImageFilter.MaxFilter(13),
        )
        isolated_alpha = ImageChops.multiply(
            upscaled_alpha,
            ImageChops.invert(valid_antialias_neighborhood),
        )
        assert isolated_alpha.getbbox() is None

        normalized = upscaled.resize(
            original.size,
            Image.Resampling.LANCZOS,
        )
        assert ImageChops.difference(standard, normalized).getbbox() is None
        naive_upscale = original.resize(
            upscaled.size,
            Image.Resampling.LANCZOS,
        )
        for background_color in (
            (9, 6, 8, 255),
            (255, 250, 251, 255),
        ):
            background = Image.new(
                "RGBA",
                original.size,
                background_color,
            )
            original_composite = Image.alpha_composite(
                background,
                original,
            ).convert("RGB")
            normalized_composite = Image.alpha_composite(
                background,
                standard,
            ).convert("RGB")
            difference = ImageChops.difference(
                original_composite,
                normalized_composite,
            )
            assert max(ImageStat.Stat(difference).mean) < 1.1

            high_density_background = Image.new(
                "RGBA",
                upscaled.size,
                background_color,
            )
            upscaled_composite = Image.alpha_composite(
                high_density_background,
                upscaled,
            ).convert("RGB")
            naive_composite = Image.alpha_composite(
                high_density_background,
                naive_upscale,
            ).convert("RGB")
            reconstruction_difference = ImageChops.difference(
                upscaled_composite,
                naive_composite,
            )
            assert min(
                ImageStat.Stat(reconstruction_difference).mean
            ) > 0.8

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        device_scale_factor=2,
        load_interactive_visuals=False,
    )
    mascot = page.locator(".mascot-card__art")
    expect(mascot).to_have_attribute(
        "src",
        "/muta-v2-transparent-v10.webp",
    )
    expect(mascot).to_have_attribute(
        "srcset",
        "/muta-v2-transparent-v10.webp 1x, /muta-v2-transparent-2x-v10.webp 2x",
    )
    rendered = mascot.evaluate(
        """
        (element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return {
            currentSource: element.currentSrc,
            naturalWidth: element.naturalWidth,
            naturalHeight: element.naturalHeight,
            renderedWidth: rect.width,
            objectFit: style.objectFit,
            objectPosition: style.objectPosition,
          };
        }
        """
    )
    assert rendered["currentSource"].endswith(
        "/muta-v2-transparent-2x-v10.webp",
    )
    assert rendered["naturalWidth"] == 1254
    assert rendered["naturalHeight"] == 1254
    assert rendered["renderedWidth"] <= 650.5
    assert 2508 >= rendered["renderedWidth"] * 3
    assert rendered["objectFit"] == "contain"
    assert rendered["objectPosition"] == "50% 46%"

    standard_density_page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        device_scale_factor=1,
        load_interactive_visuals=False,
    )
    standard_density_source = standard_density_page.locator(
        ".mascot-card__art",
    ).evaluate("(element) => element.currentSrc")
    assert standard_density_source.endswith(
        "/muta-v2-transparent-v10.webp",
    )


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

    expect(page.locator(".brand")).to_have_attribute(
        "aria-label",
        "Reload The Mutating Company home page",
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


@pytest.mark.parametrize(
    "viewport",
    [(1280, 720), (580, 844), (390, 844), (280, 700)],
    ids=["desktop", "mobile-boundary", "phone", "minimum"],
)
def test_header_is_english_only_without_a_language_control(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Keep the header free of a premature language selector.

    The site currently ships only English content, so the former ``EN ↘``
    control suggested an unavailable interaction and consumed valuable header
    width. This regression covers the desktop layout, the exact mobile
    breakpoint, a normal phone, and the supported minimum width. It requires
    the obsolete language element and its accessible label to be absent—not
    merely hidden with CSS—while preserving the document's English language
    metadata and the two real header controls: theme switching and the mobile
    navigation container. Reintroducing the old selector, leaving an invisible
    focusable copy behind, or accidentally removing ``lang="en"`` makes this
    test fail.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    header = page.locator(".site-header")

    expect(page.locator("html")).to_have_attribute("lang", "en")
    assert header.locator(".language").count() == 0
    assert header.get_by_text("EN", exact=True).count() == 0
    assert page.get_by_label("Current language: English", exact=True).count() == 0
    assert (
        header.get_by_role(
            "button",
            name="Toggle color theme",
            exact=True,
        ).count()
        == 1
    )
    assert header.locator(".mobile-menu").count() == 1


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tmc_wordmark_uses_the_approved_science_gothic_variant(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Pin the exact Science Gothic treatment selected as font option five.

    The approved comparison did not use an arbitrary Science Gothic default.
    Its compact ``TMC`` state used weight 780, width 128, and contrast 34;
    the expanded company name used weight 680, width 76, and contrast 20.
    The three anchor letters must smoothly adopt the expanded width/contrast
    axes during the same 350 ms hover transition so the finished wordmark
    matches the chosen preview instead of mixing two unrelated silhouettes.

    This test first waits for the locally hosted, deliberately subsetted web
    font and proves that Chromium has actually loaded it, preventing a quiet
    fallback to Helvetica when the asset path or font-face declaration breaks.
    It then checks the collapsed axes, the lighter/condensed reveal axes, the
    transition contract, and the final axes after deterministically finishing
    the CSS animations. Finally, both collapsed and expanded lettering must
    fit inside the pre-reserved stage so switching fonts cannot make the logo
    collide with the navigation even though the outer header remains fixed.
    Running the contract in both themes also confirms that theme changes only
    recolor the approved type treatment and never substitute another font.
    """

    page = driver(
        stored_theme=theme,
        viewport=(1280, 720),
        load_interactive_visuals=False,
    )
    initial = page.evaluate(
        """
        async () => {
          await document.fonts.load('780 35px "Science Gothic"', "TMC");
          await document.fonts.ready;

          const readAxes = (style) => Object.fromEntries(
            [...style.fontVariationSettings.matchAll(
              /"([^"]+)"\\s+(-?\\d+(?:\\.\\d+)?)/g,
            )].map((match) => [match[1], Number(match[2])]),
          );
          const stage = document.querySelector(".brand__wordmark-stage");
          const wordmark = document.querySelector(".brand__wordmark");
          const anchors = [
            ...document.querySelectorAll(".brand__word-anchor"),
          ];
          const reveals = [
            ...document.querySelectorAll(".brand__word-reveal"),
          ];
          const stageRect = stage.getBoundingClientRect();
          const wordmarkRect = wordmark.getBoundingClientRect();
          const firstAnchorStyle = getComputedStyle(anchors[0]);

          return {
            fontLoaded: document.fonts.check(
              '780 35px "Science Gothic"',
              "TMC",
            ),
            family: getComputedStyle(stage).fontFamily,
            anchorWeights: anchors.map(
              (element) => getComputedStyle(element).fontWeight,
            ),
            anchorAxes: anchors.map(
              (element) => readAxes(getComputedStyle(element)),
            ),
            revealWeights: reveals.map(
              (element) => getComputedStyle(element).fontWeight,
            ),
            revealAxes: reveals.map(
              (element) => readAxes(getComputedStyle(element)),
            ),
            transitionProperty: firstAnchorStyle.transitionProperty,
            transitionDuration: firstAnchorStyle.transitionDuration,
            stageWidth: stageRect.width,
            wordmarkWidth: wordmarkRect.width,
          };
        }
        """
    )

    assert initial["fontLoaded"] is True
    assert initial["family"].startswith('"Science Gothic"')
    assert initial["anchorWeights"] == ["780", "780", "780"]
    assert initial["anchorAxes"] == [
        {"CTRS": 34, "wdth": 128},
        {"CTRS": 34, "wdth": 128},
        {"CTRS": 34, "wdth": 128},
    ]
    assert initial["revealWeights"] == ["680", "680", "680"]
    assert initial["revealAxes"] == [
        {"CTRS": 20, "wdth": 76},
        {"CTRS": 20, "wdth": 76},
        {"CTRS": 20, "wdth": 76},
    ]
    assert initial["transitionProperty"] == "font-variation-settings"
    assert initial["transitionDuration"] == "0.35s"
    assert initial["wordmarkWidth"] <= initial["stageWidth"] + 0.5

    wordmark = page.locator(".brand__wordmark")
    wordmark.hover()
    expanded = wordmark.evaluate(
        """
        (element) => {
          for (const animation of element.getAnimations({subtree: true})) {
            animation.finish();
          }

          const readAxes = (style) => Object.fromEntries(
            [...style.fontVariationSettings.matchAll(
              /"([^"]+)"\\s+(-?\\d+(?:\\.\\d+)?)/g,
            )].map((match) => [match[1], Number(match[2])]),
          );
          const stage = element.closest(".brand__wordmark-stage");
          const stageRect = stage.getBoundingClientRect();
          const wordmarkRect = element.getBoundingClientRect();
          return {
            anchorAxes: [
              ...element.querySelectorAll(".brand__word-anchor"),
            ].map((anchor) => readAxes(getComputedStyle(anchor))),
            stageWidth: stageRect.width,
            wordmarkWidth: wordmarkRect.width,
          };
        }
        """
    )

    assert expanded["anchorAxes"] == [
        {"CTRS": 20, "wdth": 76},
        {"CTRS": 20, "wdth": 76},
        {"CTRS": 20, "wdth": 76},
    ]
    assert expanded["wordmarkWidth"] <= expanded["stageWidth"] + 0.5


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tmc_hover_animation_reaches_and_reverses_its_rest_states(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Lock down the exact collapsed, expanded, and recollapsed TMC states.

    At rest the logo must show only the T, M, and C anchors: every hidden word
    segment has zero width and opacity, the separating margins are absent, and
    the completed spectrum overlay is transparent. Hovering must run the
    350 ms unfold animation, reveal all three words to their natural widths,
    bring their opacity to one, and add spacing only after T and M. Moving the
    pointer away must run the inverse fold animation and restore the original
    zero-width state without leaving a partially expanded hit area.

    The test finishes Web Animations directly instead of sleeping for 350 ms,
    so CPU speed and display refresh rate cannot make it flaky. Both themes are
    covered because the textual base color changes with the theme even though
    the hover mechanics are shared. Removing either hover rule, breaking the
    transition endpoint, changing the animation direction, or preventing the
    logo from folding again makes a specific phase of this regression fail.
    """

    page = driver(
        stored_theme=theme,
        viewport=(1280, 720),
        load_interactive_visuals=False,
    )
    wordmark = page.locator(".brand__wordmark")

    initial = wordmark.evaluate(
        """
        (element) => {
          for (const animation of element.getAnimations({subtree: true})) {
            animation.finish();
          }

          const reveals = [...element.querySelectorAll(".brand__word-reveal")];
          const parts = [...element.querySelectorAll(".brand__word-part")];
          return {
            widths: reveals.map(
              (reveal) => reveal.getBoundingClientRect().width,
            ),
            opacities: reveals.map(
              (reveal) => Number.parseFloat(getComputedStyle(reveal).opacity),
            ),
            margins: parts.map(
              (part) => Number.parseFloat(getComputedStyle(part).marginRight),
            ),
            spectrumOpacities: reveals.map(
              (reveal) => Number.parseFloat(
                getComputedStyle(reveal, "::after").opacity,
              ),
            ),
            animationNames: reveals.map(
              (reveal) => getComputedStyle(reveal, "::after").animationName,
            ),
          };
        }
        """
    )
    assert all(width <= 0.01 for width in initial["widths"])
    assert initial["opacities"] == [0, 0, 0]
    assert initial["margins"] == [0, 0, 0]
    assert initial["spectrumOpacities"] == [0, 0, 0]
    assert initial["animationNames"] == ["brand-spectrum-fold"] * 3

    wordmark.hover()
    expanded = wordmark.evaluate(
        """
        (element) => {
          const animations = element.getAnimations({subtree: true});
          const spectrumNames = animations
            .map((animation) => animation.animationName)
            .filter(Boolean);

          for (const animation of animations) {
            animation.finish();
          }

          const reveals = [...element.querySelectorAll(".brand__word-reveal")];
          const parts = [...element.querySelectorAll(".brand__word-part")];
          return {
            widths: reveals.map(
              (reveal) => reveal.getBoundingClientRect().width,
            ),
            naturalWidths: reveals.map((reveal) => reveal.scrollWidth),
            opacities: reveals.map(
              (reveal) => Number.parseFloat(getComputedStyle(reveal).opacity),
            ),
            margins: parts.map(
              (part) => Number.parseFloat(getComputedStyle(part).marginRight),
            ),
            spectrumOpacities: reveals.map(
              (reveal) => Number.parseFloat(
                getComputedStyle(reveal, "::after").opacity,
              ),
            ),
            spectrumNames,
          };
        }
        """
    )
    assert expanded["spectrumNames"] == ["brand-spectrum-unfold"] * 3
    for width, natural_width in zip(
        expanded["widths"],
        expanded["naturalWidths"],
    ):
        assert abs(width - natural_width) <= 1
    assert expanded["opacities"] == [1, 1, 1]
    assert expanded["margins"][0] > 0
    assert expanded["margins"][1] > 0
    assert expanded["margins"][2] == 0
    assert expanded["spectrumOpacities"] == [0, 0, 0]

    page.mouse.move(
        page.viewport_size["width"] - 1,
        page.viewport_size["height"] - 1,
    )
    collapsed = wordmark.evaluate(
        """
        (element) => {
          const animations = element.getAnimations({subtree: true});
          const spectrumNames = animations
            .map((animation) => animation.animationName)
            .filter(Boolean);

          for (const animation of animations) {
            animation.finish();
          }

          const reveals = [...element.querySelectorAll(".brand__word-reveal")];
          const parts = [...element.querySelectorAll(".brand__word-part")];
          return {
            widths: reveals.map(
              (reveal) => reveal.getBoundingClientRect().width,
            ),
            opacities: reveals.map(
              (reveal) => Number.parseFloat(getComputedStyle(reveal).opacity),
            ),
            margins: parts.map(
              (part) => Number.parseFloat(getComputedStyle(part).marginRight),
            ),
            spectrumOpacities: reveals.map(
              (reveal) => Number.parseFloat(
                getComputedStyle(reveal, "::after").opacity,
              ),
            ),
            spectrumNames,
          };
        }
        """
    )
    assert collapsed["spectrumNames"] == ["brand-spectrum-fold"] * 3
    assert all(width <= 0.01 for width in collapsed["widths"])
    assert collapsed["opacities"] == [0, 0, 0]
    assert collapsed["margins"] == [0, 0, 0]
    assert collapsed["spectrumOpacities"] == [0, 0, 0]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tmc_hover_animation_carries_a_moving_pink_spectrum_midflight(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Verify the visible pink shimmer while TMC is actively unfolding.

    Endpoint-only tests cannot detect a silently lost flourish: the words can
    still expand correctly even when the pseudo-element is transparent, its
    spectrum is replaced by a flat color, or the gradient stops moving. This
    regression pauses every 350 ms wordmark animation at 25%, 50%, and 75%.
    At each deterministic sample the hidden text must be only partly revealed,
    the spectrum overlay must be visibly opaque, and its background position
    must travel from right to left rather than remaining fixed.

    The style contract also requires the current 98-degree, 230%-wide gradient
    to contain the three dominant pink stops used by the supplied animation.
    The overlay itself remains transparent text filled through background
    clipping, and the named animation retains its linear 350 ms timing. Running
    the same checks in both themes proves that the pink shimmer remains present
    over either foreground color. Removing the overlay, flattening its colors,
    stopping its travel, or changing the reveal into an instant jump makes this
    test fail without depending on real elapsed time.
    """

    page = driver(
        stored_theme=theme,
        viewport=(1280, 720),
        load_interactive_visuals=False,
    )
    wordmark = page.locator(".brand__wordmark")
    wordmark.evaluate(
        """
        (element) => {
          for (const animation of element.getAnimations({subtree: true})) {
            animation.finish();
          }
        }
        """
    )

    wordmark.hover()
    spectrum = wordmark.evaluate(
        """
        (element) => {
          const animations = element.getAnimations({subtree: true});
          const timedAnimations = animations.filter((animation) => {
            const duration = Number(animation.effect?.getTiming().duration);
            return Number.isFinite(duration) && Math.abs(duration - 350) <= 0.1;
          });
          const spectrumAnimations = timedAnimations.filter(
            (animation) =>
              animation.animationName === "brand-spectrum-unfold",
          );
          const reveal = element.querySelector(".brand__word-reveal");
          const samples = [];

          for (const progress of [0.25, 0.5, 0.75]) {
            for (const animation of timedAnimations) {
              const duration = Number(animation.effect.getTiming().duration);
              animation.pause();
              animation.currentTime = duration * progress;
            }

            const revealStyle = getComputedStyle(reveal);
            const spectrumStyle = getComputedStyle(reveal, "::after");
            samples.push({
              progress,
              width: reveal.getBoundingClientRect().width,
              naturalWidth: reveal.scrollWidth,
              revealOpacity: Number.parseFloat(revealStyle.opacity),
              spectrumOpacity: Number.parseFloat(spectrumStyle.opacity),
              backgroundPositionX: Number.parseFloat(
                spectrumStyle.backgroundPositionX,
              ),
            });
          }

          const spectrumStyle = getComputedStyle(reveal, "::after");
          const gradient = spectrumStyle.backgroundImage;
          const pinkStops = [
            "rgb(240, 0, 120)",
            "rgb(255, 101, 189)",
            "rgb(255, 45, 149)",
          ];

          return {
            samples,
            spectrumAnimationCount: spectrumAnimations.length,
            animationName: spectrumStyle.animationName,
            animationDuration: spectrumStyle.animationDuration,
            animationTimingFunction: spectrumStyle.animationTimingFunction,
            backgroundImage: gradient,
            backgroundSize: spectrumStyle.backgroundSize,
            overlayColor: spectrumStyle.color,
            pinkStopCount: pinkStops.filter(
              (color) => gradient.includes(color),
            ).length,
          };
        }
        """
    )

    assert spectrum["spectrumAnimationCount"] == 3
    assert spectrum["animationName"] == "brand-spectrum-unfold"
    assert spectrum["animationDuration"] == "0.35s"
    assert spectrum["animationTimingFunction"] == "linear"
    assert spectrum["backgroundImage"].startswith("linear-gradient(98deg")
    assert spectrum["backgroundSize"] == "230% 100%"
    assert spectrum["overlayColor"] == "rgba(0, 0, 0, 0)"
    assert spectrum["pinkStopCount"] == 3

    samples = spectrum["samples"]
    assert len(samples) == 3
    assert (
        samples[0]["backgroundPositionX"]
        > samples[1]["backgroundPositionX"]
        > samples[2]["backgroundPositionX"]
    )
    assert (
        samples[0]["width"]
        < samples[1]["width"]
        < samples[2]["width"]
    )
    for sample in samples:
        assert 0 < sample["width"] < sample["naturalWidth"]
        assert 0 < sample["revealOpacity"] < 1
        assert 0.2 < sample["spectrumOpacity"] < 0.7
        assert 0 < sample["backgroundPositionX"] < 100


@pytest.mark.parametrize(
    ("selector", "requires_expansion"),
    [
        (".brand", False),
        (".animated-eye-logo", False),
        (".brand__word-part--t .brand__word-anchor", False),
        (".brand__word-part--m .brand__word-anchor", False),
        (
            ".brand__word-part:not(.brand__word-part--t)"
            ":not(.brand__word-part--m) .brand__word-anchor",
            False,
        ),
        ('.brand__word-reveal[data-text="ompany"]', True),
    ],
    ids=[
        "unified-container",
        "eye",
        "letter-t",
        "letter-m",
        "letter-c",
        "expanded-wordmark",
    ],
)
def test_every_visible_brand_area_uses_the_link_pointer_cursor(
    driver: DriverFactory,
    selector: str,
    requires_expansion: bool,
) -> None:
    """Show the link-style pointer over every part of the unified logo control.

    The eye and animated TMC wordmark are now children of one clickable
    ``div``. The parent already declared ``cursor: pointer``, but the old
    wordmark styling explicitly restored ``cursor: default`` on its subtree.
    As a result the cursor correctly looked like a link over the eye and then
    changed back to an ordinary arrow as soon as it crossed TMC—even though
    both areas performed the same reload action.

    This parameterized regression checks the computed cursor on the shared
    container, the eye, each initially visible TMC anchor, and text that becomes
    clickable only after the hover expansion finishes. The expanded case waits
    for the actual animation endpoint before moving the pointer onto “ompany”,
    so the test covers the complete revealed hit area rather than a hidden DOM
    node. Reintroducing a child-level ``default`` or ``auto`` cursor anywhere in
    these paths makes the corresponding case fail while leaving unrelated page
    elements unconstrained.
    """

    page = driver(
        viewport=(1280, 720),
        load_interactive_visuals=False,
    )

    if requires_expansion:
        page.locator(".brand__wordmark").hover()
        page.wait_for_function(
            """
            () => [...document.querySelectorAll(".brand__word-reveal")].every(
              (element) =>
                element.getBoundingClientRect().width > 1 &&
                getComputedStyle(element).opacity === "1",
            )
            """
        )

    target = page.locator(selector)
    assert target.count() == 1
    target.hover()
    assert target.evaluate(
        "(element) => getComputedStyle(element).cursor"
    ) == "pointer"


@pytest.mark.parametrize("viewport_width", [580, 390, 320, 304, 280])
def test_tmc_wordmark_is_always_visible_in_the_narrow_header(
    driver: DriverFactory,
    viewport_width: int,
) -> None:
    """Keep the compact ``TMC`` visible down to the supported minimum width.

    The earlier narrow-header rule solved a space shortage by applying
    ``display: none`` to the complete wordmark stage below 580 px. That removed
    not only the optional long-name reveal but also the required resting
    ``TMC`` identity, so the header unexpectedly became an eye-only control on
    phones. A layout can look acceptable at 390 px and still regress at the
    320/280 px widths where the theme toggle and Menu consume most of the row.

    This regression covers the mobile breakpoint, a normal phone, the 304 px
    width from the reported screen recording, and the two narrowest supported
    widths. It requires the stage and all three anchor
    letters to have real visible geometry, verifies their exact ``T``, ``M``,
    and ``C`` content, and proves that the compact wordmark stays between the
    eye and the action group without being clipped outside the header. Restoring
    the old ``display: none``, hiding individual anchors, or allowing flex
    compression to push the letters beneath the controls makes the test fail.
    """

    page = driver(
        stored_theme="dark",
        viewport=(viewport_width, 844),
        load_interactive_visuals=False,
    )
    stage = page.locator(".brand__wordmark-stage")
    eye = page.locator(".animated-eye-logo")
    brand = page.locator(".brand")
    anchors = page.locator(".brand__word-anchor")

    expect(stage).to_be_visible()
    expect(eye).to_be_visible()
    expect(brand).to_have_attribute(
        "aria-label",
        "Reload The Mutating Company home page",
    )
    assert anchors.count() == 3
    assert anchors.all_text_contents() == ["T", "M", "C"]

    geometry = page.locator(".site-header").evaluate(
        """
        (header) => {
          const rect = (element) => {
            const box = element.getBoundingClientRect();
            return {
              left: box.left,
              right: box.right,
              width: box.width,
              height: box.height,
            };
          };
          const stage = header.querySelector(".brand__wordmark-stage");
          const anchors = [...header.querySelectorAll(".brand__word-anchor")];
          return {
            header: rect(header),
            eye: rect(header.querySelector(".animated-eye-logo")),
            stage: rect(stage),
            actions: rect(header.querySelector(".header-actions")),
            stageDisplay: getComputedStyle(stage).display,
            anchors: anchors.map((anchor) => ({
              ...rect(anchor),
              visibility: getComputedStyle(anchor).visibility,
              opacity: Number.parseFloat(getComputedStyle(anchor).opacity),
            })),
          };
        }
        """
    )

    assert geometry["stageDisplay"] != "none"
    assert geometry["eye"]["width"] == pytest.approx(48, abs=0.5)
    assert geometry["eye"]["height"] == pytest.approx(48, abs=0.5)
    assert geometry["stage"]["left"] >= geometry["header"]["left"] - 0.5
    assert geometry["stage"]["right"] <= geometry["actions"]["left"] + 0.5
    for anchor in geometry["anchors"]:
        assert anchor["width"] > 0
        assert anchor["height"] > 0
        assert anchor["visibility"] == "visible"
        assert anchor["opacity"] == pytest.approx(1, abs=0.01)


@pytest.mark.parametrize(
    ("viewport_width", "expected_expandable"),
    [
        (280, False),
        (320, False),
        (390, False),
        (460, True),
        (580, True),
        (860, True),
    ],
)
def test_tmc_expands_only_when_the_reserved_wordmark_fits(
    driver: DriverFactory,
    viewport_width: int,
    expected_expandable: bool,
) -> None:
    """Gate the long company-name animation by measured free header width.

    The expanded logo reserves ``11.6em`` for “The Mutating Company”. On a
    narrow phone that space competes with the fixed-size theme toggle and Menu
    button. Letting hover expand unconditionally either covers those controls,
    pushes them outside the rounded header, or exposes only a clipped fragment
    of the name. A viewport-only breakpoint is also insufficient because the
    usable width depends on the actual rendered action group, font size, eye
    margins, and gap.

    The component therefore measures the horizontal interval from the brand's
    left edge to the action group's left edge after fonts and responsive styles
    settle. This parameterized regression exercises three widths where the full
    reservation cannot fit and three where it can. It independently recomputes
    the same physical requirement from rendered geometry, checks the published
    ``data-can-expand`` decision, then deterministically finishes CSS
    animations after hover. Blocked cases must keep every reveal at zero width
    while preserving ``TMC``; permitted cases must reveal every word fully and
    still end before the action group. The assertions use animation endpoints
    and element boxes rather than elapsed time, so slow CI cannot change the
    outcome. Removing the guard, using a stale hard-coded viewport decision, or
    allowing a partially clipped expansion makes this test fail.
    """

    page = driver(
        stored_theme="light",
        viewport=(1280, 844),
        load_interactive_visuals=False,
    )
    stage = page.locator(".brand__wordmark-stage")
    wordmark = page.locator(".brand__wordmark")

    expect(stage).to_have_attribute("data-expansion-measured", "true")
    initial_revision = int(stage.get_attribute("data-expansion-revision"))
    page.set_viewport_size({"width": viewport_width, "height": 844})
    page.wait_for_function(
        """
        ([revision]) =>
          Number(
            document.querySelector(".brand__wordmark-stage")
              .dataset.expansionRevision,
          ) > revision
        """,
        arg=[initial_revision],
    )
    initial = stage.evaluate(
        """
        (stage) => {
          const brand = stage.closest(".brand");
          const header = stage.closest(".site-header");
          const actions = header.querySelector(".header-actions");
          const eye = brand.querySelector(".brand__mark--eye");
          const brandStyle = getComputedStyle(brand);
          const headerStyle = getComputedStyle(header);
          const eyeStyle = getComputedStyle(eye);
          const stageStyle = getComputedStyle(stage);
          const headerRect = header.getBoundingClientRect();
          const actionsRect = actions.getBoundingClientRect();
          const eyeRect = eye.getBoundingClientRect();
          const gap = Number.parseFloat(brandStyle.columnGap) || 0;
          const eyeMargins =
            (Number.parseFloat(eyeStyle.marginLeft) || 0) +
            (Number.parseFloat(eyeStyle.marginRight) || 0);
          const required =
            eyeRect.width +
            eyeMargins +
            gap +
            (Number.parseFloat(stageStyle.fontSize) || 0) * 11.6;
          const available =
            headerRect.width -
            (Number.parseFloat(headerStyle.paddingLeft) || 0) -
            (Number.parseFloat(headerStyle.paddingRight) || 0) -
            actionsRect.width;

          return {
            canExpand: stage.dataset.canExpand,
            required,
            available,
          };
        }
        """
    )
    measured_expandable = initial["available"] + 0.5 >= initial["required"]
    assert measured_expandable is expected_expandable
    assert (initial["canExpand"] == "true") is expected_expandable

    wordmark.hover()
    expanded = wordmark.evaluate(
        """
        (wordmark) => {
          for (const animation of wordmark.getAnimations({subtree: true})) {
            animation.finish();
          }

          const brand = wordmark.closest(".brand");
          const actions = wordmark.closest(".site-header")
            .querySelector(".header-actions");
          const reveals = [
            ...wordmark.querySelectorAll(".brand__word-reveal"),
          ];
          const anchors = [
            ...wordmark.querySelectorAll(".brand__word-anchor"),
          ];
          return {
            revealWidths: reveals.map(
              (reveal) => reveal.getBoundingClientRect().width,
            ),
            revealNaturalWidths: reveals.map((reveal) => reveal.scrollWidth),
            revealOpacities: reveals.map(
              (reveal) => Number.parseFloat(getComputedStyle(reveal).opacity),
            ),
            anchorWidths: anchors.map(
              (anchor) => anchor.getBoundingClientRect().width,
            ),
            brandRight: brand.getBoundingClientRect().right,
            actionsLeft: actions.getBoundingClientRect().left,
          };
        }
        """
    )

    assert all(width > 0 for width in expanded["anchorWidths"])
    if expected_expandable:
        for width, natural_width in zip(
            expanded["revealWidths"],
            expanded["revealNaturalWidths"],
        ):
            assert width == pytest.approx(natural_width, abs=1)
        assert expanded["revealOpacities"] == [1, 1, 1]
        assert expanded["brandRight"] <= expanded["actionsLeft"] + 0.5
    else:
        assert all(width <= 0.01 for width in expanded["revealWidths"])
        assert expanded["revealOpacities"] == [0, 0, 0]


@pytest.mark.parametrize(
    "viewport_width",
    [1440, 1024, 861, 580, 390, 280],
)
def test_header_eye_remains_circular_at_every_responsive_width(
    driver: DriverFactory,
    viewport_width: int,
) -> None:
    """Prevent the narrow-header repair from deforming the desktop eye.

    A previous compact-header fix correctly stopped flexbox from shrinking the
    logo, but moved the source bitmap's 2048:1901 aspect ratio into the shared
    desktop rule. The yellow eye then became a subtle horizontal ellipse, most
    visibly after scrolling when its flat white fill and black outline exposed
    the contour.

    This regression spans desktop, both sides of the responsive breakpoints,
    and the minimum supported phone width. It requires the outer brand slot,
    animated logo, and color surface to remain exact 48 px squares, while the
    outline keeps its intentional equal 2.5% overscan and the mask stretches
    to the square instead of letterboxing the source canvas. Reintroducing
    ``height: auto``, a source-ratio ``aspect-ratio``, ``mask-size: contain``,
    or a mobile-only shape override makes the relevant width fail
    deterministically.
    """

    page = driver(
        stored_theme="light",
        viewport=(viewport_width, 844),
        load_interactive_visuals=False,
    )
    geometry = page.locator(".brand__mark--eye").evaluate(
        """
        (mark) => {
          const rectangle = (element) => {
            const rect = element.getBoundingClientRect();
            return {width: rect.width, height: rect.height};
          };
          const eye = mark.querySelector(".animated-eye-logo");
          const surface = eye.querySelector(".animated-eye-logo__surface");
          const outline = eye.querySelector(".animated-eye-logo__outline");
          const surfaceStyle = getComputedStyle(surface);
          return {
            mark: rectangle(mark),
            eye: rectangle(eye),
            surface: rectangle(surface),
            outline: rectangle(outline),
            maskSize:
              surfaceStyle.maskSize ||
              surfaceStyle.webkitMaskSize,
          };
        }
        """
    )

    for part in ("mark", "eye", "surface", "outline"):
        assert geometry[part]["width"] == pytest.approx(
            geometry[part]["height"],
            abs=0.1,
        )
    for part in ("mark", "eye", "surface"):
        assert geometry[part]["width"] == pytest.approx(48, abs=0.5)
        assert geometry[part]["height"] == pytest.approx(48, abs=0.5)
    assert geometry["outline"]["width"] == pytest.approx(
        48 * 1.025,
        abs=0.1,
    )
    assert geometry["outline"]["height"] == pytest.approx(
        48 * 1.025,
        abs=0.1,
    )
    assert geometry["maskSize"] == "100% 100%"


@pytest.mark.parametrize("viewport_width", [390, 320, 280])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_very_narrow_header_preserves_eye_and_theme_toggle_geometry(
    driver: DriverFactory,
    viewport_width: int,
    theme: str,
) -> None:
    """Prevent flex compression from deforming compact header controls.

    Below the 580-pixel breakpoint the old rules independently shrank the eye
    from its normal artwork box and narrowed the theme track from 68 to 60
    pixels. The sun, moon, thumb, masks, and pupil still used coordinates
    derived from the original sizes, so sufficiently narrow headers exposed
    oval eye contours and off-center theme slots—especially after scrolling,
    when the eye's white outline made the distortion obvious.

    This regression covers light and dark themes at three phone widths down to
    280 pixels. Before and after a deep scroll it requires the eye to retain its
    circular 1:1 geometry; the track, thumb, and active icon to retain
    their fixed geometry; the active icon and thumb to share the same center;
    and the brand/actions groups to remain separated inside the viewport.
    Timing is irrelevant: all assertions use layout boxes and computed styles,
    so a future responsive rule that silently reintroduces flex shrinking fails
    deterministically.
    """

    page = driver(
        stored_theme=theme,
        viewport=(viewport_width, 844),
        load_interactive_visuals=False,
    )

    def read_geometry() -> dict[str, object]:
        return page.locator(".site-header").evaluate(
            """
            (header) => {
              const rectangle = (selector) => {
                const rect = header.querySelector(selector).getBoundingClientRect();
                return {
                  left: rect.left,
                  right: rect.right,
                  top: rect.top,
                  bottom: rect.bottom,
                  width: rect.width,
                  height: rect.height,
                  centerX: rect.left + rect.width / 2,
                  centerY: rect.top + rect.height / 2,
                };
              };
              const theme = document.documentElement.dataset.theme;
              const track = header.querySelector(".theme-toggle__track");
              const thumb = header.querySelector(".theme-toggle__thumb");
              const headerRect = header.getBoundingClientRect();
              return {
                viewportWidth: innerWidth,
                header: {
                  left: headerRect.left,
                  right: headerRect.right,
                  top: headerRect.top,
                  bottom: headerRect.bottom,
                  width: headerRect.width,
                  height: headerRect.height,
                  centerX: headerRect.left + headerRect.width / 2,
                  centerY: headerRect.top + headerRect.height / 2,
                },
                brand: rectangle(".brand"),
                actions: rectangle(".header-actions"),
                eye: rectangle(".animated-eye-logo"),
                track: rectangle(".theme-toggle__track"),
                thumb: rectangle(".theme-toggle__thumb"),
                activeIcon: rectangle(
                  theme === "dark"
                    ? ".theme-toggle__moon"
                    : ".theme-toggle__sun",
                ),
                trackRadius: getComputedStyle(track).borderRadius,
                thumbRadius: getComputedStyle(thumb).borderRadius,
                toggleFlexShrink: getComputedStyle(
                  header.querySelector(".theme-toggle"),
                ).flexShrink,
                eyeFlexShrink: getComputedStyle(
                  header.querySelector(".brand__mark--eye"),
                ).flexShrink,
              };
            }
            """
        )

    def assert_geometry(geometry: dict[str, object]) -> None:
        header = geometry["header"]
        brand = geometry["brand"]
        actions = geometry["actions"]
        eye = geometry["eye"]
        track = geometry["track"]
        thumb = geometry["thumb"]
        active_icon = geometry["activeIcon"]

        assert header["left"] >= -0.5
        assert header["right"] <= geometry["viewportWidth"] + 0.5
        assert brand["right"] <= actions["left"] + 0.5
        assert eye["width"] == pytest.approx(48, abs=0.5)
        assert eye["height"] == pytest.approx(48, abs=0.5)
        assert track["width"] == pytest.approx(68, abs=0.5)
        assert track["height"] == pytest.approx(42, abs=0.5)
        assert thumb["width"] == pytest.approx(30, abs=0.5)
        assert thumb["height"] == pytest.approx(30, abs=0.5)
        assert active_icon["width"] == pytest.approx(30, abs=0.5)
        assert active_icon["height"] == pytest.approx(30, abs=0.5)
        assert thumb["centerX"] == pytest.approx(
            active_icon["centerX"],
            abs=0.5,
        )
        assert thumb["centerY"] == pytest.approx(
            active_icon["centerY"],
            abs=0.5,
        )
        assert geometry["trackRadius"] == "13px"
        assert geometry["thumbRadius"] == "9px"
        assert geometry["toggleFlexShrink"] == "0"
        assert geometry["eyeFlexShrink"] == "0"

    assert_geometry(read_geometry())
    page.evaluate(
        """
        () => scrollTo({
          top: document.documentElement.scrollHeight * 0.65,
          behavior: "instant",
        })
        """
    )
    assert_geometry(read_geometry())


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
