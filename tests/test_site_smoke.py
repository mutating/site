"""Small smoke tests for the rendered site and its local assets."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
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


def test_all_github_navigation_links_use_sources_label_and_icon(
    driver: DriverFactory,
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
    """

    page = driver(color_scheme="light")
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
        spacing = link.evaluate(
            """
            (element) => {
              const style = getComputedStyle(element);
              return {
                gap: Number.parseFloat(style.columnGap),
                fontSize: Number.parseFloat(style.fontSize),
              };
            }
            """
        )
        assert spacing["gap"] == pytest.approx(
            spacing["fontSize"] * 0.675,
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
        "public/pantograph-variant-20-clean.svg",
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
    replacement is embedded as a same-origin SVG document whose pantograph API
    reports an actively changing progress value after the section enters the
    viewport. The assertion waits for a state change instead of expecting an
    exact frame timestamp, so normal differences in browser and CI speed do
    not make the test flaky.

    The illustration must also sit directly on the section background instead
    of retaining either the old card border/shadow or the supplied SVG's opaque
    paper rectangle. Its original black structural ink stays black in the light
    theme and becomes white in the dark theme, including after a manual theme
    switch made while the embedded document is already running. This guards the
    same-origin theme bridge: without it the SVG would keep the palette that was
    active at load time and drift out of sync with the rest of the page.
    """

    svg_source = (
        PROJECT_ROOT / "public/pantograph-variant-20-clean.svg"
    ).read_text()
    assert '<rect width="1536" height="1024" fill="url(#paper)"/>' not in (
        svg_source
    )
    assert 'stroke="var(--pantograph-ink)"' in svg_source
    assert 'fill="var(--pantograph-ink)"' in svg_source

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
    )
    visual = page.locator(".pantograph-visual")
    embedded_svg = visual.locator(".pantograph-visual__object")

    expect(page.locator(".network-visual")).to_have_count(0)
    expect(embedded_svg).to_have_attribute(
        "data",
        "/pantograph-variant-20-clean.svg",
    )
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
    visual.scroll_into_view_if_needed()
    page.wait_for_function(
        """
        () => {
          const embedded = document.querySelector(
            ".pantograph-visual__object",
          );
          const childDocument = embedded?.contentDocument;
          const childWindow = childDocument?.defaultView;
          const childRoot = childDocument?.documentElement;
          return Boolean(
            childWindow?.__pantograph
            && childRoot?.dataset.progress,
          );
        }
        """
    )
    initial_progress = embedded_svg.evaluate(
        """
        (embedded) => Number.parseFloat(
          embedded.contentDocument.documentElement.dataset.progress,
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
            embedded.contentDocument.documentElement.dataset.progress,
          );
          return Math.abs(currentProgress - initialProgress) > 0.003;
        }
        """,
        arg=initial_progress,
    )
    metrics = embedded_svg.evaluate(
        """
        (embedded) =>
          embedded.contentDocument.defaultView.__pantograph.metrics()
        """,
    )
    assert metrics["playing"] is True
    assert metrics["config"]["duration"] == 8000
    assert metrics["finite"] is True

    adaptive_ink = embedded_svg.evaluate(
        """
        (embedded) => {
          const childDocument = embedded.contentDocument;
          const stroke = childDocument.querySelector(
            '[stroke="var(--pantograph-ink)"]',
          );
          const fill = childDocument.querySelector(
            '[fill="var(--pantograph-ink)"]',
          );
          return {
            strokeCount: childDocument.querySelectorAll(
              '[stroke="var(--pantograph-ink)"]',
            ).length,
            fillCount: childDocument.querySelectorAll(
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
    page.wait_for_function(
        """
        () => {
          const embedded = document.querySelector(
            ".pantograph-visual__object",
          );
          return embedded?.contentDocument?.documentElement?.dataset.theme
            === "dark";
        }
        """
    )
    dark_ink = embedded_svg.evaluate(
        """
        (embedded) => {
          const childDocument = embedded.contentDocument;
          const stroke = childDocument.querySelector(
            '[stroke="var(--pantograph-ink)"]',
          );
          const fill = childDocument.querySelector(
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
    brand = page.locator(".brand")

    expect(stage).to_be_hidden()
    expect(eye).to_be_visible()
    expect(brand).to_have_attribute(
        "aria-label",
        "Reload The Mutating Company home page",
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
