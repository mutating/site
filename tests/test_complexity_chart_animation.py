"""Regression tests for the one-shot Complexity chart curve animation."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page, expect


DriverFactory = Callable[..., Page]


def _place_chart_at_visibility(
    page: Page,
    visible_fraction: float,
) -> None:
    """Position the chart panel with a deterministic vertical visibility ratio."""

    page.evaluate(
        """
        (visibleFraction) => {
          const panel = document.querySelector(".chart-panel");
          const rect = panel.getBoundingClientRect();
          const documentTop = rect.top + window.scrollY;
          const visibleHeight = rect.height * visibleFraction;
          window.scrollTo({
            top: documentTop - window.innerHeight + visibleHeight,
            behavior: "instant",
          });
        }
        """,
        visible_fraction,
    )


def _center_chart_panel(page: Page) -> None:
    """Place the complete chart panel in the middle of the viewport."""

    page.evaluate(
        """
        () => {
          const panel = document.querySelector(".chart-panel");
          const rect = panel.getBoundingClientRect();
          const documentTop = rect.top + window.scrollY;
          window.scrollTo({
            top:
              documentTop -
              Math.max(0, (window.innerHeight - rect.height) / 2),
            behavior: "instant",
          });
        }
        """
    )


def _assert_chart_axis_label_typography(page: Page) -> None:
    """Require both chart axis labels to share one resolved DOM text style.

    Keeping this assertion block separate from the bitmap checks lets the
    responsive/theme matrix exercise the typography before animation
    completion can hide a more useful failure. Both labels must remain real
    spans with identical family, size, weight, line-height, color, spacing,
    style, and capitalization; moving one back into canvas text or styling it
    independently therefore remains a failing regression.
    """

    labels = page.locator(".chart-panel__axis-label")
    expect(labels).to_have_count(2)
    expect(page.locator(".chart-panel__cost")).to_have_text("↑ CHANGE COST")
    expect(page.locator(".chart-panel__scale")).to_have_text(
        "SYSTEM SCALE →",
    )

    resolved = labels.evaluate_all(
        """
        (elements) => elements.map((element) => {
          const style = getComputedStyle(element);
          return {
            tagName: element.tagName,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
            color: style.color,
            letterSpacing: style.letterSpacing,
            fontStyle: style.fontStyle,
            textTransform: style.textTransform,
          };
        })
        """
    )

    assert resolved[0]["tagName"] == "SPAN"
    assert resolved[1]["tagName"] == "SPAN"
    assert resolved[0] == resolved[1]


def _settle_view_observation(page: Page) -> None:
    """Yield three painted frames so IntersectionObserver can report geometry."""

    page.evaluate(
        """
        () => new Promise((resolve) => {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              requestAnimationFrame(resolve);
            });
          });
        })
        """
    )


def _wait_for_initial_chart_layout(page: Page) -> None:
    """Wait until font and ResizeObserver sizing finishes before reveal tests."""

    page.evaluate("document.fonts.ready")
    page.wait_for_function(
        """
        () => {
          const chart = document.querySelector(".complexity-chart");
          const staticLayer = chart?.querySelector(
            ".complexity-chart__static",
          );
          const curvesLayer = chart?.querySelector(
            ".complexity-chart__curves",
          );
          if (
            !chart ||
            !staticLayer ||
            !curvesLayer ||
            !chart.dataset.labelLayoutWidth
          ) {
            return false;
          }

          const dpr = Math.min(window.devicePixelRatio || 1, 2);
          const expectedWidth = Math.max(
            1,
            Math.floor(chart.getBoundingClientRect().width * dpr),
          );
          const expectedHeight = Math.floor(330 * dpr);
          return (
            Math.abs(
              Number(chart.dataset.labelLayoutWidth) -
              chart.getBoundingClientRect().width
            ) < 0.1 &&
            staticLayer.width === expectedWidth &&
            curvesLayer.width === expectedWidth &&
            staticLayer.height === expectedHeight &&
            curvesLayer.height === expectedHeight
          );
        }
        """
    )
    page.evaluate(
        """
        () => new Promise((resolve) => {
          const readSignature = () => {
            const chart = document.querySelector(".complexity-chart");
            const staticLayer = chart.querySelector(
              ".complexity-chart__static",
            );
            const curvesLayer = chart.querySelector(
              ".complexity-chart__curves",
            );
            return [
              chart.getBoundingClientRect().width.toFixed(3),
              chart.dataset.labelLayoutWidth,
              staticLayer.width,
              staticLayer.height,
              curvesLayer.width,
              curvesLayer.height,
            ].join("|");
          };

          let previousSignature = null;
          let stableFrames = 0;
          const checkNextFrame = () => {
            requestAnimationFrame(() => {
              const signature = readSignature();
              stableFrames =
                signature === previousSignature ? stableFrames + 1 : 0;
              previousSignature = signature;
              if (stableFrames >= 2) {
                resolve();
              } else {
                checkNextFrame();
              }
            });
          };
          checkNextFrame();
        })
        """
    )


def _finish_curve_animation(page: Page) -> None:
    """Move curves and their DOM annotations to the exact reveal endpoint."""

    _wait_for_curve_animation(page)
    page.locator(".complexity-chart").evaluate(
        """
        (chart) => {
          const animatedLayers = [
            chart.querySelector(".complexity-chart__curves"),
            chart.querySelector(".complexity-chart__annotations"),
          ];
          for (const layer of animatedLayers) {
            const [animation] = layer.getAnimations();
            if (!animation) {
              throw new Error("A chart reveal animation did not start");
            }
            animation.finish();
          }
        }
        """
    )


def _wait_for_curve_animation(page: Page) -> None:
    """Wait for CSS to instantiate the reveal after the one-shot marker."""

    expect(page.locator(".complexity-chart")).to_have_attribute(
        "data-curves-revealed",
        "true",
    )
    page.wait_for_function(
        """
        () => {
          const chart = document.querySelector(".complexity-chart");
          return [
            chart.querySelector(".complexity-chart__curves"),
            chart.querySelector(".complexity-chart__annotations"),
          ].every((layer) => layer.getAnimations().length === 1);
        }
        """
    )


def _install_controlled_intersection_observer(page: Page) -> None:
    """Reload with an IntersectionObserver whose deliveries tests can control."""

    page.add_init_script(
        """
        (() => {
          const instances = [];

          class ControlledIntersectionObserver {
            constructor(callback, options = {}) {
              this.callback = callback;
              this.options = options;
              this.targets = [];
              this.disconnectCount = 0;
              instances.push(this);
            }

            observe(target) {
              this.targets.push(target);
            }

            unobserve(target) {
              this.targets = this.targets.filter(
                (candidate) => candidate !== target,
              );
            }

            disconnect() {
              this.disconnectCount += 1;
            }

            takeRecords() {
              return [];
            }
          }

          window.__complexityControlledObservers = instances;
          window.__complexityControlledChartObservers = () => {
            const panel = document.querySelector(
              "#complexity .chart-panel",
            );
            return instances.filter((observer) =>
              observer.targets.includes(panel),
            );
          };
          window.__complexityControlledActiveChartObservers = () =>
            window.__complexityControlledChartObservers().filter(
              (observer) => observer.disconnectCount === 0,
            );
          window.IntersectionObserver = ControlledIntersectionObserver;
        })();
        """
    )
    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")


def test_curves_wait_for_the_whole_panel_and_use_the_css_reveal(
    driver: DriverFactory,
) -> None:
    """Animate only after the complete chart panel enters the viewport.

    The requested effect must not begin merely because the Complexity section
    or the top half of its graph is visible. The static grid and axis labels
    should already be present, while the curve layer stays clipped at its
    zero-width starting edge. Once at least 98.5% of the rounded chart panel is
    visible, the one-shot observer may add its persistent marker and CSS—not a
    JavaScript frame loop—must own the 1600 ms reveal.

    The test deliberately stops at 80% visibility first, then centers the
    complete panel. It inspects the Web Animations representation generated by
    CSS and freezes it at its start, midpoint, and endpoint. This guards the
    named keyframes, duration, easing, fill mode, and left-to-right clipping
    progression without depending on wall-clock speed or the host machine's
    frame rate.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    chart = page.locator(".complexity-chart")
    curves = page.locator(".complexity-chart__curves")

    expect(chart).to_have_attribute("data-curves-revealed", "false")
    initial = chart.evaluate(
        """
        (element) => {
          const staticLayer = element.querySelector(
            ".complexity-chart__static",
          );
          const curveLayer = element.querySelector(
            ".complexity-chart__curves",
          );
          return {
            staticAlpha: staticLayer
              .getContext("2d")
              .getImageData(0, 0, staticLayer.width, staticLayer.height)
              .data.some((channel, index) => index % 4 === 3 && channel > 0),
            curveClip: getComputedStyle(curveLayer).clipPath,
            curveAnimations: curveLayer.getAnimations().length,
          };
        }
        """
    )
    assert initial["staticAlpha"]
    assert "100%" in initial["curveClip"]
    assert initial["curveAnimations"] == 0

    _place_chart_at_visibility(page, 0.8)
    _settle_view_observation(page)
    expect(chart).to_have_attribute("data-curves-revealed", "false")

    _center_chart_panel(page)
    _wait_for_curve_animation(page)

    samples = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          const timing = animation.effect.getTiming();
          animation.pause();

          const readAt = (time) => {
            animation.currentTime = time;
            return getComputedStyle(element).clipPath;
          };

          return {
            animationName: animation.animationName,
            duration: timing.duration,
            easing: getComputedStyle(element).animationTimingFunction,
            fill: timing.fill,
            start: readAt(0),
            midpoint: readAt(Number(timing.duration) / 2),
            end: readAt(Number(timing.duration)),
          };
        }
        """
    )

    assert samples["animationName"] == "complexity-curves-reveal"
    assert samples["duration"] == 1600
    assert samples["easing"] == "cubic-bezier(0.22, 1, 0.36, 1)"
    assert samples["fill"] == "both"
    assert "100%" in samples["start"]
    assert samples["midpoint"] not in {samples["start"], samples["end"]}
    assert samples["end"] == "inset(0px 0% 0px 0px)"


@pytest.mark.parametrize(
    ("theme", "viewport", "device_scale_factor"),
    [
        ("light", (1440, 900), 1),
        ("dark", (1440, 900), 3),
        ("light", (390, 844), 3),
        ("dark", (390, 844), 1),
    ],
)
def test_finished_curves_fill_the_chart_in_every_theme_and_layout(
    driver: DriverFactory,
    theme: str,
    viewport: tuple[int, int],
    device_scale_factor: float,
) -> None:
    """Keep the completed drawing intact across themes and responsive widths.

    Splitting the former single canvas into a static layer and an animated
    curve layer must not lose either curve, crop their labels, reduce retina
    sharpness, or create a different-size overlay on phones. For both themes
    and both sides of the responsive breakpoint, this test reveals the graph,
    jumps to the exact CSS endpoint, and requires the two bitmaps and their CSS
    boxes to remain perfectly congruent.

    It also scans alpha values in the left, middle, and right thirds of the
    curve bitmap. Non-empty pixels in all three bands prove that the final
    unchecked-entropy and verification paths span the complete plot instead of
    stopping at a stale clip boundary. Because the animation is finished via
    its API, the assertion is independent of rendering speed.
    """

    page = driver(
        stored_theme=theme,
        viewport=viewport,
        device_scale_factor=device_scale_factor,
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    _settle_view_observation(page)
    expect(page.locator(".complexity-chart")).to_have_attribute(
        "data-curves-revealed",
        "true",
    )
    _assert_chart_axis_label_typography(page)
    _finish_curve_animation(page)

    state = page.locator(".complexity-chart").evaluate(
        """
        (chart) => {
          const staticLayer = chart.querySelector(
            ".complexity-chart__static",
          );
          const curves = chart.querySelector(".complexity-chart__curves");
          const curveContext = curves.getContext("2d");
          const pixels = curveContext.getImageData(
            0,
            0,
            curves.width,
            curves.height,
          ).data;
          const occupiedBands = [false, false, false];

          for (let pixel = 0; pixel < pixels.length / 4; pixel += 1) {
            if (pixels[pixel * 4 + 3] === 0) continue;
            const x = pixel % curves.width;
            occupiedBands[Math.min(2, Math.floor(x / (curves.width / 3)))] =
              true;
          }

          const readRect = (element) => {
            const rect = element.getBoundingClientRect();
            return [rect.x, rect.y, rect.width, rect.height];
          };

          return {
            devicePixelRatio: window.devicePixelRatio,
            clipPath: getComputedStyle(curves).clipPath,
            staticBitmap: [staticLayer.width, staticLayer.height],
            curveBitmap: [curves.width, curves.height],
            staticRect: readRect(staticLayer),
            curveRect: readRect(curves),
            occupiedBands,
          };
        }
        """
    )

    assert state["clipPath"] == "inset(0px 0% 0px 0px)"
    assert state["staticBitmap"] == state["curveBitmap"]
    assert state["staticRect"] == pytest.approx(state["curveRect"], abs=0.01)
    assert state["occupiedBands"] == [True, True, True]
    expected_scale = min(device_scale_factor, 2)
    assert state["devicePixelRatio"] == device_scale_factor
    assert state["curveBitmap"][0] == int(
        state["curveRect"][2] * expected_scale,
    )
    assert state["curveBitmap"][1] == int(330 * expected_scale)


@pytest.mark.parametrize(
    ("theme", "viewport", "device_scale_factor"),
    [
        ("light", (1440, 900), 1),
        ("dark", (1440, 900), 2),
        ("light", (390, 844), 2),
        ("dark", (390, 844), 1),
    ],
)
def test_finished_canvas_layers_match_the_curve_and_grid_reference_pixel_for_pixel(
    driver: DriverFactory,
    theme: str,
    viewport: tuple[int, int],
    device_scale_factor: float,
) -> None:
    """Keep the expanded canvas artwork identical after moving text into DOM.

    Alpha in three broad bands cannot distinguish the intended two curves from
    a wrong formula, thickness, color, or arbitrary pink scribble. The curve
    labels now intentionally live in selectable DOM elements, so this pixel
    comparison is scoped to the two canvases: the grid and both plotted paths.

    For both themes, both responsive layouts, and DPR 1/2, this test draws an
    offscreen references with the exact production layer split: the independently
    expanded grid on one canvas and both curves on the other, composited in DOM
    order. The dotted working
    zone must span the full chart-content width—aligned with the header's
    ``COMPLEXITY`` edge—and be shifted down by exactly one third of the distance
    between adjacent guides. The two curve paths must now begin at that same
    left edge, while retaining their established breathing room on the right.

    The test composites the static and curve layers and compares every RGBA
    channel. A two-channel tolerance is reserved only for transparent
    premultiplication at antialiased edges. Changing either grid endpoint or
    offset, the exponential constant, sampling, curve padding, stroke
    width/caps, theme colors, or layer order therefore fails this regression,
    while DOM typography and collision safety are protected by their dedicated
    tests below.
    """

    page = driver(
        stored_theme=theme,
        viewport=viewport,
        device_scale_factor=device_scale_factor,
        load_interactive_visuals=False,
    )
    page.evaluate("document.fonts.ready")
    _center_chart_panel(page)
    _finish_curve_animation(page)

    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    opposite_theme = "dark" if theme == "light" else "light"
    toggle.click()
    expect(page.locator("html")).to_have_attribute(
        "data-theme",
        opposite_theme,
    )
    toggle.click()
    expect(page.locator("html")).to_have_attribute("data-theme", theme)
    page.evaluate(
        """
        () => new Promise((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(resolve));
        })
        """
    )

    comparison = page.locator(".complexity-chart").evaluate(
        """
        (chart) => {
          const staticLayer = chart.querySelector(
            ".complexity-chart__static",
          );
          const curvesLayer = chart.querySelector(
            ".complexity-chart__curves",
          );
          const dpr = Math.min(window.devicePixelRatio || 1, 2);
          const width = chart.getBoundingClientRect().width;
          const height = 330;
          const pad = { top: 30, right: 24, bottom: 48, left: 0 };
          const plotWidth = width - pad.left - pad.right;
          const plotHeight = height - pad.top - pad.bottom;
          const gridTop = pad.top + plotHeight / 12;
          const styles = getComputedStyle(document.documentElement);
          const grid =
            styles.getPropertyValue("--line").trim() || "#ddd6dc";
          const hot = styles.getPropertyValue("--hot").trim() || "#ff3e9d";
          const hotSoft =
            styles.getPropertyValue("--hot-soft").trim() || "#b15b87";

          const reference = document.createElement("canvas");
          reference.width = staticLayer.width;
          reference.height = staticLayer.height;
          const referenceContext = reference.getContext("2d");
          referenceContext.scale(dpr, dpr);
          referenceContext.lineWidth = 1;
          referenceContext.strokeStyle = grid;
          referenceContext.setLineDash([3, 6]);

          for (let index = 0; index <= 4; index += 1) {
            const y = gridTop + (plotHeight / 4) * index;
            referenceContext.beginPath();
            referenceContext.moveTo(0, y);
            referenceContext.lineTo(width, y);
            referenceContext.stroke();
          }
          referenceContext.setLineDash([]);

          const curvesReference = document.createElement("canvas");
          curvesReference.width = curvesLayer.width;
          curvesReference.height = curvesLayer.height;
          const curvesReferenceContext = curvesReference.getContext("2d");
          curvesReferenceContext.scale(dpr, dpr);

          const plot = (fn, color, lineWidth) => {
            curvesReferenceContext.beginPath();
            for (let index = 0; index <= 160; index += 1) {
              const progress = index / 160;
              const x = pad.left + progress * plotWidth;
              const value = Math.min(1, fn(progress));
              const y = pad.top + plotHeight - value * plotHeight;
              if (index === 0) {
                curvesReferenceContext.moveTo(x, y);
              } else {
                curvesReferenceContext.lineTo(x, y);
              }
            }
            curvesReferenceContext.lineWidth = lineWidth;
            curvesReferenceContext.strokeStyle = color;
            curvesReferenceContext.lineCap = "round";
            curvesReferenceContext.lineJoin = "round";
            curvesReferenceContext.stroke();
          };

          plot(
            (x) =>
              (Math.exp(4.35 * x) - 1) / (Math.exp(4.35) - 1),
            hot,
            4,
          );
          plot((x) => 0.06 + 0.28 * x + 0.16 * x * x, hotSoft, 3);

          const expectedComposite = document.createElement("canvas");
          expectedComposite.width = staticLayer.width;
          expectedComposite.height = staticLayer.height;
          const expectedCompositeContext =
            expectedComposite.getContext("2d");
          expectedCompositeContext.drawImage(reference, 0, 0);
          expectedCompositeContext.drawImage(curvesReference, 0, 0);

          const composite = document.createElement("canvas");
          composite.width = staticLayer.width;
          composite.height = staticLayer.height;
          const compositeContext = composite.getContext("2d");
          compositeContext.drawImage(staticLayer, 0, 0);
          compositeContext.drawImage(curvesLayer, 0, 0);

          const expected = expectedCompositeContext.getImageData(
            0,
            0,
            expectedComposite.width,
            expectedComposite.height,
          ).data;
          const actual = compositeContext.getImageData(
            0,
            0,
            composite.width,
            composite.height,
          ).data;
          let maximumDifference = 0;
          let channelsOverTwo = 0;

          for (let index = 0; index < expected.length; index += 1) {
            const difference = Math.abs(expected[index] - actual[index]);
            maximumDifference = Math.max(maximumDifference, difference);
            if (difference > 2) channelsOverTwo += 1;
          }

          return {
            maximumDifference,
            channelsOverTwo,
            channelCount: expected.length,
          };
        }
        """
    )

    assert comparison["maximumDifference"] <= 2
    assert comparison["channelsOverTwo"] == 0


def test_curve_reveal_is_one_shot_across_scrolling_theme_and_resize(
    driver: DriverFactory,
) -> None:
    """Preserve the completed graph after every later viewport interaction.

    A view-linked CSS timeline would rewind the curves when the user scrolls
    upward, while recreating an observer during theme or resize redraws could
    restart the animation. Both outcomes violate the requirement that the
    first drawing be remembered for the rest of the page visit.

    This regression completes the reveal, records its CSS animation identity
    and terminal time, scrolls to the hero and back twice, changes the theme,
    resizes from desktop to a narrow layout and back, and then rechecks the
    same curve layer. The persistent data marker, single finished animation,
    final clip, and terminal current time must survive every operation. A
    re-observe-on-scroll implementation or a class toggle would fail here.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    chart = page.locator(".complexity-chart")
    curves = page.locator(".complexity-chart__curves")

    _center_chart_panel(page)
    expect(chart).to_have_attribute("data-curves-revealed", "true")
    _finish_curve_animation(page)

    baseline = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          return {
            count: element.getAnimations().length,
            name: animation.animationName,
            currentTime: animation.currentTime,
            duration: animation.effect.getTiming().duration,
          };
        }
        """
    )

    for _ in range(2):
        page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
        _center_chart_panel(page)

    page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    ).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    page.set_viewport_size({"width": 390, "height": 844})
    _center_chart_panel(page)
    page.set_viewport_size({"width": 1440, "height": 900})
    _center_chart_panel(page)

    final = curves.evaluate(
        """
        (element) => {
          const animations = element.getAnimations();
          const [animation] = animations;
          return {
            count: animations.length,
            name: animation.animationName,
            currentTime: animation.currentTime,
            duration: animation.effect.getTiming().duration,
            playState: animation.playState,
            clipPath: getComputedStyle(element).clipPath,
          };
        }
        """
    )

    expect(chart).to_have_attribute("data-curves-revealed", "true")
    assert final["count"] == baseline["count"] == 1
    assert final["name"] == baseline["name"] == "complexity-curves-reveal"
    assert final["duration"] == baseline["duration"] == 1600
    assert final["currentTime"] == pytest.approx(1600, abs=0.01)
    assert final["playState"] == "finished"
    assert final["clipPath"] == "inset(0px 0% 0px 0px)"


def test_chart_animation_changes_no_section_geometry_or_neighbor_content(
    driver: DriverFactory,
) -> None:
    """Limit the new behavior strictly to the graph's painted curve pixels.

    The animation request explicitly excludes typography, copy, the panel
    frame, neighboring sections, and page layout. This test records
    document-space geometry for the complete Complexity section and every
    direct child of its copy/panel columns while the panel is only partially
    visible. It also records the next Containment section and the exact text of
    both sections.

    After the chart becomes fully visible and its CSS reveal reaches the final
    frame, all rectangles and text must be identical. The only permitted DOM
    state change is ``data-curves-revealed`` on the chart wrapper. Moving the
    animation to the panel, changing padding, animating transforms, or
    accidentally editing surrounding content makes this regression fail.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    _place_chart_at_visibility(page, 0.8)

    selectors = (
        "#complexity",
        "#complexity .complexity__copy",
        "#complexity .section-index",
        "#complexity h2",
        "#complexity .chart-panel",
        "#complexity .chart-panel__header",
        "#complexity .complexity-chart",
        "#complexity .chart-panel__caption",
        "#containment",
    )
    before = page.evaluate(
        """
        (selectors) => ({
          geometry: Object.fromEntries(selectors.map((selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [
              selector,
              [
                rect.x,
                rect.y + window.scrollY,
                rect.width,
                rect.height,
              ],
            ];
          })),
          complexityText: document.querySelector("#complexity").textContent,
          containmentText: document.querySelector("#containment").textContent,
        })
        """,
        selectors,
    )

    _center_chart_panel(page)
    expect(page.locator(".complexity-chart")).to_have_attribute(
        "data-curves-revealed",
        "true",
    )
    _finish_curve_animation(page)

    after = page.evaluate(
        """
        (selectors) => ({
          geometry: Object.fromEntries(selectors.map((selector) => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [
              selector,
              [
                rect.x,
                rect.y + window.scrollY,
                rect.width,
                rect.height,
              ],
            ];
          })),
          complexityText: document.querySelector("#complexity").textContent,
          containmentText: document.querySelector("#containment").textContent,
        })
        """,
        selectors,
    )

    assert after["complexityText"] == before["complexityText"]
    assert after["containmentText"] == before["containmentText"]
    assert after["geometry"].keys() == before["geometry"].keys()
    for selector in selectors:
        assert after["geometry"][selector] == pytest.approx(
            before["geometry"][selector],
            abs=0.5,
        )


def test_redundant_copy_column_legend_stays_removed(
    driver: DriverFactory,
) -> None:
    """Prevent the duplicate legend beneath the Complexity-section prose.

    The plotted lines already carry the labels ``UNCHECKED ENTROPY`` and
    ``WITH VERIFICATION`` inside the chart itself. A second key below the two
    explanatory paragraphs repeated that information, added a stray separator,
    and made the left column look visually heavier than the graph column.

    This regression verifies that the obsolete ``.chart-key`` structure and its
    two duplicate labels are absent from the prose column, while the accessible
    graph wrapper remains present. It therefore catches either accidentally
    restoring the old legend markup or removing the actual chart along with it.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    copy = page.locator("#complexity .complexity__copy")

    expect(copy.locator(".chart-key")).to_have_count(0)
    assert "Unchecked entropy" not in copy.inner_text()
    assert "Verification applied" not in copy.inner_text()
    expect(page.locator("#complexity .complexity-chart")).to_have_attribute(
        "role",
        "img",
    )


def test_change_cost_label_and_curves_align_with_the_dotted_working_area(
    driver: DriverFactory,
) -> None:
    """Align the curves and upper axis label with the expanded dotted grid.

    The chart's dotted working area spans the full canvas width, but a previous
    iteration kept the curves and ``↑ CHANGE COST`` on the old 42 px inset.
    That left a conspicuous empty strip at the start of the graph even though
    the guides had already moved left. The label also sat too close to the
    solid header divider instead of being centered in the band between that
    divider and the first dotted guide.

    This regression exercises both desktop and phone widths. The phone pass
    reloads the document after changing the viewport, so canvas allocation,
    media queries, observers, and chart initialization all start at the narrow
    width just as they did when these were separate browser cases; only the
    unchanged browser context is reused. It requires the complete selectable
    label to share the dotted zone's exact left edge and to have its vertical
    center equidistant from the header divider and first dotted guide. It also
    samples the rendered curve canvas and requires a non-transparent curve
    pixel at the same left edge. Restoring the legacy 42 px plot padding,
    applying a breakpoint-specific offset, or moving only the text while
    leaving the curves behind makes the test fail.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    geometries: list[tuple[tuple[int, int], dict[str, object]]] = []

    for index, viewport in enumerate(((1440, 900), (390, 844))):
        if index:
            page.set_viewport_size(
                {"width": viewport[0], "height": viewport[1]}
            )
            page.reload(wait_until="domcontentloaded")
            page.locator(
                'html[data-theme-ready="true"]'
            ).wait_for(state="attached")

        _center_chart_panel(page)
        _finish_curve_animation(page)

        geometry = page.locator(".complexity-chart").evaluate(
            """
            (chart) => {
              const chartRect = chart.getBoundingClientRect();
              const label = chart.querySelector(".chart-panel__cost");
              const labelRect = label.getBoundingClientRect();
              const canvas = chart.querySelector(".complexity-chart__curves");
              const context = canvas.getContext("2d");
              const dpr = canvas.width / chartRect.width;
              let firstCurveColumn = null;

              for (
                let x = 0;
                x < canvas.width && firstCurveColumn === null;
                x += 1
              ) {
                const pixels = context.getImageData(
                  x,
                  0,
                  1,
                  canvas.height,
                ).data;
                for (let index = 3; index < pixels.length; index += 4) {
                  if (pixels[index] !== 0) {
                    firstCurveColumn = x / dpr;
                    break;
                  }
                }
              }

              const plotHeight = 330 - 30 - 48;
              const firstGuideY = 30 + plotHeight / 12;
              const labelCenterY =
                labelRect.top - chartRect.top + labelRect.height / 2;
              return {
                text: label.textContent.trim(),
                leftOffset: labelRect.left - chartRect.left,
                labelCenterY,
                firstGuideY,
                distanceFromDivider: labelCenterY,
                distanceFromFirstGuide: firstGuideY - labelCenterY,
                firstCurveColumn,
              };
            }
            """
        )
        geometries.append((viewport, geometry))

    for viewport, geometry in geometries:
        diagnostic = f"chart alignment at {viewport}: {geometry}"
        assert geometry["text"] == "↑ CHANGE COST", diagnostic
        assert geometry["leftOffset"] == pytest.approx(
            0,
            abs=0.5,
        ), diagnostic
        assert geometry["labelCenterY"] == pytest.approx(
            geometry["firstGuideY"] / 2,
            abs=0.5,
        ), diagnostic
        assert geometry["distanceFromDivider"] == pytest.approx(
            geometry["distanceFromFirstGuide"],
            abs=0.5,
        ), diagnostic
        assert geometry["firstCurveColumn"] is not None, diagnostic
        assert geometry["firstCurveColumn"] <= 1, diagnostic


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844)])
def test_chart_footer_caption_is_visible_only_while_the_panel_is_hovered(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Reveal the marketing disclaimer only for the chart's hover state.

    The resting chart footer must show just ``SYSTEM SCALE →``. Its right edge
    must match the full-width dotted working area, and its vertical center must
    sit halfway between the final dotted guide and the panel's inner bottom.
    Hovering anywhere over the
    chart card must raise the scale label and reveal
    ``Illustrative model—not a promise from marketing.`` by moving it downward
    from beneath the scale label. At the end of that transition, the scale and
    disclaimer must occupy the established pre-change positions: the scale
    remains inside the bottom of the canvas and the disclaimer begins directly
    below it without changing the panel's geometry.

    This regression exercises desktop and phone layouts, completes CSS
    transitions programmatically instead of sleeping for animation timing, and
    then moves the pointer away again. It verifies the complete round trip:
    hidden/lowered at rest, visible/raised while hovered, and the exact initial
    state restored after hover ends. A permanently visible disclaimer, a
    one-way animation, a JavaScript-controlled sticky state, or a footer that
    shifts neighboring layout therefore makes the test fail deterministically.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)

    panel = page.locator(".chart-panel")
    footer = page.locator(".chart-panel__footer")
    scale = page.locator(".chart-panel__scale")
    caption = page.locator(".chart-panel__caption")

    expect(scale).to_have_text("SYSTEM SCALE →")
    expect(caption).to_have_text(
        "Illustrative model—not a promise from marketing.",
    )

    def state() -> dict[str, object]:
        return footer.evaluate(
            """
            (footer) => {
              const chart = footer.previousElementSibling;
              const scale = footer.querySelector(".chart-panel__scale");
              const caption = footer.querySelector(".chart-panel__caption");
              const scaleStyle = getComputedStyle(scale);
              const captionStyle = getComputedStyle(caption);
              const transformY = (style) =>
                style.transform === "none"
                  ? 0
                  : new DOMMatrixReadOnly(style.transform).m42;
              const rectangle = (element) => {
                const rect = element.getBoundingClientRect();
                return {
                  top: rect.top,
                  right: rect.right,
                  bottom: rect.bottom,
                  height: rect.height,
                };
              };
              const panel = footer.closest(".chart-panel");
              const panelStyle = getComputedStyle(panel);
              const panelRect = panel.getBoundingClientRect();

              return {
                chart: rectangle(chart),
                footer: rectangle(footer),
                scale: rectangle(scale),
                caption: rectangle(caption),
                scaleTransform: scaleStyle.transform,
                captionTransform: captionStyle.transform,
                scaleTransformY: transformY(scaleStyle),
                captionTransformY: transformY(captionStyle),
                captionOpacity: captionStyle.opacity,
                captionVisibility: captionStyle.visibility,
                captionClipPath: captionStyle.clipPath,
                panelHeight: panelRect.height,
                panelInnerBottom:
                  panelRect.bottom - parseFloat(panelStyle.paddingBottom),
              };
            }
            """
        )

    resting = state()
    assert resting["captionOpacity"] == "0"
    assert resting["captionVisibility"] == "hidden"
    assert resting["captionClipPath"] != "inset(0px)"
    canvas_scale = resting["chart"]["height"] / 330
    last_guide_y = resting["chart"]["top"] + (
        30 + ((330 - 30 - 48) / 12) + (330 - 30 - 48)
    ) * canvas_scale
    scale_center_y = (
        resting["scale"]["top"] + resting["scale"]["bottom"]
    ) / 2
    lower_band_center_y = (
        last_guide_y + resting["panelInnerBottom"]
    ) / 2
    assert resting["scale"]["right"] == pytest.approx(
        resting["chart"]["right"],
        abs=0.5,
    )
    assert scale_center_y == pytest.approx(lower_band_center_y, abs=0.75)

    panel.hover()
    footer.evaluate(
        """
        (element) => {
          for (const animation of element.getAnimations({subtree: true})) {
            animation.finish();
          }
        }
        """
    )
    hovered = state()

    assert hovered["captionOpacity"] == "1"
    assert hovered["captionVisibility"] == "visible"
    assert hovered["captionClipPath"] == "inset(0px)"
    assert hovered["scaleTransformY"] == pytest.approx(0, abs=0.01)
    assert hovered["captionTransformY"] == pytest.approx(0, abs=0.01)
    assert resting["scale"]["top"] - hovered["scale"]["top"] == pytest.approx(
        resting["scaleTransformY"],
        abs=0.75,
    )
    assert hovered["caption"]["top"] - resting["caption"]["top"] == pytest.approx(
        12,
        abs=0.75,
    )
    assert hovered["caption"]["top"] == pytest.approx(
        hovered["chart"]["bottom"],
        abs=0.75,
    )
    assert hovered["panelHeight"] == pytest.approx(
        resting["panelHeight"],
        abs=0.01,
    )

    page.mouse.move(1, 1)
    footer.evaluate(
        """
        (element) => {
          for (const animation of element.getAnimations({subtree: true})) {
            animation.finish();
          }
        }
        """
    )
    restored = state()

    assert restored["captionOpacity"] == "0"
    assert restored["captionVisibility"] == "hidden"
    assert restored["scale"]["top"] == pytest.approx(
        resting["scale"]["top"],
        abs=0.01,
    )
    assert restored["caption"]["top"] == pytest.approx(
        resting["caption"]["top"],
        abs=0.01,
    )
    assert restored["panelHeight"] == pytest.approx(
        resting["panelHeight"],
        abs=0.01,
    )


def test_verification_label_stays_clear_of_both_curves_across_all_layout_ranges(
    driver: DriverFactory,
) -> None:
    """Prevent ``WITH VERIFICATION`` from touching either curve at any width.

    Earlier collision handling considered only the burgundy verification path.
    At several intermediate and narrow widths, the fixed-width label began to
    the left of the curves' crossing, so the brighter entropy path could still
    pass through its first letters even though the burgundy gap was correct.
    A breakpoint cannot cover every possible panel width because both the
    crossing coordinate and the label's relative width vary continuously.

    The production layout now measures the real DOM label, moves it right until
    its left edge clears the mathematical crossing whenever the available width
    permits, and otherwise uses every available horizontal pixel. Its vertical
    coordinate is then derived from whichever curve is lower at that edge.
    Because both curves rise monotonically to the right, this single edge is the
    strict collision boundary for the entire word.

    This test resizes one live page through an eight-pixel sweep from 320 to
    1600 px, then checks large desktop endpoints through 2560 px. At every width
    it waits for ResizeObserver to publish the matching layout, independently
    recomputes both equations and their intersection, accounts for the mobile
    canvas's vertical scaling, and requires at least six CSS pixels between
    each stroke and the label box. It also proves that the label actually shifts
    right on constrained layouts, retains its established 28 px inset when no
    collision exists, and never leaves the chart. Restoring the old one-curve
    calculation, using a stale label measurement, applying a coarse breakpoint,
    or allowing either path through the text therefore fails without screenshot
    timing or font-antialiasing assumptions.
    """

    page = driver(
        stored_theme="light",
        viewport=(320, 900),
        load_interactive_visuals=False,
    )
    page.evaluate("document.fonts.ready")
    viewport_widths = [*range(320, 1601, 8), 1920, 2560]
    shifted_widths: list[int] = []
    unchanged_widths: list[int] = []

    for viewport_width in viewport_widths:
        page.set_viewport_size({"width": viewport_width, "height": 900})
        page.wait_for_function(
            """
            () => {
              const chart = document.querySelector(".complexity-chart");
              if (!chart?.dataset.labelLayoutWidth) return false;
              return Math.abs(
                Number(chart.dataset.labelLayoutWidth) -
                chart.getBoundingClientRect().width
              ) < 0.1;
            }
            """
        )
        geometry = page.locator(".complexity-chart").evaluate(
            """
            (chart) => {
              const label = chart.querySelector(
                ".complexity-chart__curve-label--verification",
              );
              const chartRect = chart.getBoundingClientRect();
              const labelRect = label.getBoundingClientRect();
              const pad = {top: 30, right: 24, bottom: 48, left: 0};
              const plotWidth = chartRect.width - pad.left - pad.right;
              const plotHeight = 330 - pad.top - pad.bottom;
              const labelLeft = labelRect.left - chartRect.left;
              const entropyValueAt = (progress) =>
                (Math.exp(4.35 * progress) - 1) /
                (Math.exp(4.35) - 1);
              const verificationValueAt = (progress) =>
                0.06 + 0.28 * progress + 0.16 * progress * progress;
              const progress = Math.max(
                0,
                Math.min(1, (labelLeft - pad.left) / plotWidth),
              );
              let crossingLow = 0;
              let crossingHigh = 1;
              for (let iteration = 0; iteration < 48; iteration += 1) {
                const middle = (crossingLow + crossingHigh) / 2;
                if (
                  entropyValueAt(middle) <
                  verificationValueAt(middle)
                ) {
                  crossingLow = middle;
                } else {
                  crossingHigh = middle;
                }
              }
              const crossingProgress = (crossingLow + crossingHigh) / 2;
              const crossingX =
                chartRect.left + pad.left + crossingProgress * plotWidth;
              const verticalScale = chartRect.height / 330;
              const entropyY =
                chartRect.top +
                (
                  pad.top +
                  plotHeight -
                  entropyValueAt(progress) * plotHeight
                ) * verticalScale;
              const verificationY =
                chartRect.top +
                (
                  pad.top +
                  plotHeight -
                  verificationValueAt(progress) * plotHeight
                ) * verticalScale;

              return {
                chartWidth: chartRect.width,
                labelLeft: labelRect.left,
                labelRight: labelRect.right,
                labelWidth: labelRect.width,
                chartLeft: chartRect.left,
                chartRight: chartRect.right,
                entropyGap: labelRect.top - entropyY,
                verificationGap: labelRect.top - verificationY,
                crossingX,
                rightInset: chartRect.right - labelRect.right,
              };
            }
            """
        )

        assert geometry["entropyGap"] >= 6, (
            f"verification label touched the entropy curve at viewport "
            f"{viewport_width}px / chart {geometry['chartWidth']:.2f}px: "
            f"gap was {geometry['entropyGap']:.2f}px"
        )
        assert geometry["verificationGap"] >= 6, (
            f"verification label touched its own curve at viewport "
            f"{viewport_width}px / chart {geometry['chartWidth']:.2f}px: "
            f"gap was {geometry['verificationGap']:.2f}px"
        )
        assert geometry["labelLeft"] >= geometry["chartLeft"]
        assert geometry["labelRight"] <= geometry["chartRight"]
        if geometry["rightInset"] < 27.5:
            shifted_widths.append(viewport_width)
        else:
            unchanged_widths.append(viewport_width)

        maximum_label_left = (
            geometry["chartRight"] - 4 - geometry["labelWidth"]
        )
        if maximum_label_left >= geometry["crossingX"] + 6:
            assert geometry["labelLeft"] >= geometry["crossingX"] + 5.5

    assert shifted_widths
    assert unchanged_widths


def test_every_chart_text_is_real_user_selectable_dom_content(
    driver: DriverFactory,
) -> None:
    """Make every visible word in the graph selectable with a pointer.

    The two curve names were formerly painted with ``canvas.fillText``. They
    looked like labels but were only pixels, so a user could select the header,
    axes, and hover caption while ``UNCHECKED ENTROPY`` and
    ``WITH VERIFICATION`` silently refused selection. A partial fix would still
    leave the hidden-on-rest caption or one axis blocked by a canvas layer or by
    ``pointer-events: none``.

    This test enumerates every textual item in the chart panel: both header
    values, both curve names, both axes, and the hover-only explanatory caption.
    After revealing the caption it requires one real DOM node per item, explicit
    ``user-select: text``, pointer hit-testing, and a complete Range selection
    equal to the visible string. It then instruments ``fillText`` and forces
    both a theme redraw and a resize redraw; zero calls prove that no future
    label can be moved back into either canvas while the DOM assertions happen
    to remain green. Restoring canvas text, disabling pointer events on the
    footer, or covering a label with a drawing layer makes this regression fail.
    """

    page = driver(
        stored_theme="light",
        viewport=(1280, 900),
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    _finish_curve_animation(page)
    panel = page.locator("#complexity .chart-panel")
    panel.hover()
    expect(page.locator(".chart-panel__caption")).to_be_visible()
    page.wait_for_function(
        """
        () => {
          const caption = document.querySelector(".chart-panel__caption");
          const rect = caption?.getBoundingClientRect();
          if (!caption || !rect) return false;
          const hit = document.elementFromPoint(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2,
          );
          return hit === caption || caption.contains(hit);
        }
        """
    )

    labels = {
        ".chart-panel__header span:first-child": "COMPLEXITY / CHANGE COST",
        ".chart-panel__header span:last-child": "MODEL 04.35",
        ".chart-panel__cost": "↑ CHANGE COST",
        ".complexity-chart__curve-label--entropy": "UNCHECKED ENTROPY",
        ".complexity-chart__curve-label--verification": "WITH VERIFICATION",
        ".chart-panel__scale": "SYSTEM SCALE →",
        ".chart-panel__caption": (
            "Illustrative model—not a promise from marketing."
        ),
    }

    selections = panel.evaluate(
        """
        (panel, labels) => Object.fromEntries(
          Object.entries(labels).map(([selector]) => {
            const matches = [...panel.querySelectorAll(selector)];
            if (matches.length !== 1) {
              return [selector, {count: matches.length}];
            }

            const [element] = matches;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const hit = document.elementFromPoint(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2,
            );
            const range = document.createRange();
            range.selectNodeContents(element);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            const selectedText = selection.toString();
            selection.removeAllRanges();

            return [selector, {
              count: 1,
              normalizedText: element.textContent.replace(/\\s+/g, " ").trim(),
              selectedText,
              userSelect: style.userSelect,
              pointerEvents: style.pointerEvents,
              hitTarget: hit === element || element.contains(hit),
            }];
          }),
        )
        """,
        labels,
    )

    for selector, expected_text in labels.items():
        selection = selections[selector]
        diagnostic = f"selectable chart label {selector}: {selection}"
        assert selection["count"] == 1, diagnostic
        assert selection["normalizedText"] == expected_text, diagnostic
        assert selection["selectedText"].casefold() == expected_text.casefold(), (
            diagnostic
        )
        assert selection["userSelect"] == "text", diagnostic
        assert selection["pointerEvents"] != "none", diagnostic
        assert selection["hitTarget"] is True, diagnostic

    page.evaluate(
        """
        () => {
          window.__chartFillTextCalls = 0;
          const original = CanvasRenderingContext2D.prototype.fillText;
          CanvasRenderingContext2D.prototype.fillText = function (...args) {
            window.__chartFillTextCalls += 1;
            return original.apply(this, args);
          };
        }
        """
    )
    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    toggle.click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.set_viewport_size({"width": 760, "height": 900})
    page.wait_for_function(
        """
        () => {
          const chart = document.querySelector(".complexity-chart");
          return chart?.dataset.labelLayoutWidth &&
            Math.abs(
              Number(chart.dataset.labelLayoutWidth) -
              chart.getBoundingClientRect().width
            ) < 0.1;
        }
        """
    )
    assert page.evaluate("window.__chartFillTextCalls") == 0


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_reduced_motion_finishes_the_complete_accessible_drawing(
    driver: DriverFactory,
    theme: str,
) -> None:
    """Respect reduced motion without leaving either curve permanently hidden.

    The base curve layer starts with a 100% right inset, so a careless
    ``animation: none`` accessibility override would erase the graph for people
    who request less motion. The site's global reduced-motion rule intentionally
    keeps one effectively instant CSS iteration instead.

    This test creates the browser context with reduced motion before React or
    CSS initializes, reveals the panel in both themes, and inspects the actual
    CSS Animation. Its duration must collapse from 1600 ms to 0.01 ms while the
    iteration count remains one. Finishing that animation must expose the full
    curve canvas and preserve the wrapper's accessible image description.
    Removing the reduced-motion rule or disabling the animation outright makes
    this test fail deterministically.
    """

    page = driver(
        stored_theme=theme,
        viewport=(1280, 720),
        reduced_motion="reduce",
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    chart = page.get_by_role(
        "img",
        name=(
            "Unchecked software complexity rises exponentially with system "
            "scale, while deterministic verification keeps the curve much "
            "flatter."
        ),
        exact=True,
    )
    expect(chart).to_have_attribute("data-curves-revealed", "true")

    state = page.locator(".complexity-chart__curves").evaluate(
        """
        (curves) => {
          const [animation] = curves.getAnimations();
          const timing = animation.effect.getTiming();
          animation.finish();
          return {
            reduce: matchMedia("(prefers-reduced-motion: reduce)").matches,
            duration: timing.duration,
            iterations: timing.iterations,
            clipPath: getComputedStyle(curves).clipPath,
          };
        }
        """
    )

    assert state == {
        "reduce": True,
        "duration": 0.01,
        "iterations": 1,
        "clipPath": "inset(0px 0% 0px 0px)",
    }


@pytest.mark.parametrize("viewport", [(1440, 900), (390, 844)])
def test_chart_accessibility_contract_is_stable_through_every_frame(
    driver: DriverFactory,
    viewport: tuple[int, int],
) -> None:
    """Expose one semantic image while both implementation canvases stay silent.

    The visual was split into two canvases solely to animate its curves. That
    implementation detail must never produce duplicate unlabeled images for
    assistive technology or make the description disappear while the clip is
    between endpoints.

    At desktop and phone widths the test requires exactly one accessible image
    with the established explanatory label. Both child canvases must remain
    ``aria-hidden`` and unfocusable. It then pauses the CSS animation at 0%,
    50%, and 100% and repeats the same accessibility assertions at each frame,
    proving that animation state affects only paint and never semantics.
    """

    page = driver(
        stored_theme="light",
        viewport=viewport,
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    _wait_for_curve_animation(page)

    accessible_name = (
        "Unchecked software complexity rises exponentially with system scale, "
        "while deterministic verification keeps the curve much flatter."
    )
    image = page.get_by_role("img", name=accessible_name, exact=True)
    expect(image).to_have_count(1)
    canvases = page.locator(".complexity-chart canvas")
    expect(canvases).to_have_count(2)

    for current_time in (0, 800, 1600):
        page.locator(".complexity-chart__curves").evaluate(
            """
            (curves, time) => {
              const [animation] = curves.getAnimations();
              animation.pause();
              animation.currentTime = time;
            }
            """,
            current_time,
        )
        expect(image).to_have_count(1)
        state = canvases.evaluate_all(
            """
            (elements) => elements.map((element) => ({
              ariaHidden: element.getAttribute("aria-hidden"),
              role: element.getAttribute("role"),
              tabIndex: element.tabIndex,
            }))
            """
        )
        assert state == [
            {"ariaHidden": "true", "role": None, "tabIndex": -1},
            {"ariaHidden": "true", "role": None, "tabIndex": -1},
        ]


def test_short_landscape_viewport_uses_the_maximum_attainable_visibility(
    driver: DriverFactory,
) -> None:
    """Reveal the chart when a short viewport cannot contain the full panel.

    On a low landscape display the rounded panel is taller than the viewport,
    making a fixed 98.5% IntersectionObserver threshold mathematically
    unreachable. The first implementation therefore risked leaving both curves
    hidden forever on small laptop windows and landscape phones.

    This regression uses an 844×320 viewport, verifies that the panel truly is
    taller than the available screen, and positions it at the maximum possible
    visible ratio. The adaptive observer threshold must treat that physical
    maximum as the equivalent of a fully presented panel and start exactly one
    CSS reveal. The assertion waits on the semantic marker rather than elapsed
    animation time.
    """

    page = driver(
        stored_theme="dark",
        viewport=(844, 320),
        load_interactive_visuals=False,
    )
    panel_height = page.locator(".chart-panel").evaluate(
        "(panel) => panel.getBoundingClientRect().height"
    )
    assert panel_height > 320

    _center_chart_panel(page)
    chart = page.locator(".complexity-chart")
    expect(chart).to_have_attribute("data-curves-revealed", "true")
    expect(page.locator(".complexity-chart__curves")).to_have_js_property(
        "tagName",
        "CANVAS",
    )
    animation_count = page.locator(".complexity-chart__curves").evaluate(
        "(curves) => curves.getAnimations().length"
    )
    assert animation_count == 1


def test_interrupted_reveal_does_not_rewind_or_restart_when_reentered(
    driver: DriverFactory,
) -> None:
    """Remember a partially drawn curve if the user scrolls away mid-animation.

    A one-shot implementation can appear correct after a completed reveal while
    still restarting when the graph leaves the viewport before 1600 ms elapse.
    This test pauses the real CSS animation at exactly 37%, stores the Animation
    object itself, and records its clip. It then scrolls to the hero and returns
    the complete panel without letting wall-clock time advance.

    Reentry must retain the same DOM node, the same Animation identity, the same
    current time and the same clip. Only an explicit final ``finish()`` may move
    it to 100%. Reconnecting the observer, toggling the marker, or replacing the
    canvas on scroll makes this regression fail.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    curves = page.locator(".complexity-chart__curves")
    _wait_for_curve_animation(page)
    paused = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          animation.pause();
          animation.currentTime = 1600 * 0.37;
          window.__complexityPausedAnimation = animation;
          window.__complexityCurveCanvas = element;
          return {
            currentTime: animation.currentTime,
            clipPath: getComputedStyle(element).clipPath,
          };
        }
        """
    )

    page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
    _settle_view_observation(page)
    _center_chart_panel(page)
    _settle_view_observation(page)

    resumed = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          return {
            sameAnimation: animation === window.__complexityPausedAnimation,
            sameCanvas: element === window.__complexityCurveCanvas,
            count: element.getAnimations().length,
            currentTime: animation.currentTime,
            clipPath: getComputedStyle(element).clipPath,
          };
        }
        """
    )

    assert resumed["sameAnimation"]
    assert resumed["sameCanvas"]
    assert resumed["count"] == 1
    assert resumed["currentTime"] == pytest.approx(paused["currentTime"], abs=0.01)
    assert resumed["clipPath"] == paused["clipPath"]


def test_theme_and_resize_redraw_do_not_reset_midflight_progress(
    driver: DriverFactory,
) -> None:
    """Keep animation progress while canvas pixels redraw under it.

    Theme changes recolor both canvas layers and responsive resizes replace both
    backing stores. Either redraw could accidentally remount the curve canvas,
    recreate its CSS animation, or snap an in-progress reveal to an endpoint.

    The test pauses at exactly 40%, stores the curve node and Animation object,
    changes light to dark, then crosses the mobile breakpoint. It waits for
    observable bitmap changes instead of sleeping. The bitmap dimensions and
    encoded pixels must update, while node identity, animation identity,
    current time and normalized clip stay fixed. This catches stale colors,
    one-layer redraws, resize races and mid-flight restarts.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    _center_chart_panel(page)
    curves = page.locator(".complexity-chart__curves")
    _wait_for_curve_animation(page)
    before = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          animation.pause();
          animation.currentTime = 640;
          window.__complexityMidAnimation = animation;
          window.__complexityMidCanvas = element;
          window.__complexityLightPixels = element.toDataURL();
          return {
            width: element.width,
            currentTime: animation.currentTime,
            clipPath: getComputedStyle(element).clipPath,
          };
        }
        """
    )

    page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    ).click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.wait_for_function(
        """
        () => document.querySelector(".complexity-chart__curves").toDataURL()
          !== window.__complexityLightPixels
        """
    )

    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function(
        """
        () => {
          const chart = document.querySelector(".complexity-chart");
          const curves = document.querySelector(".complexity-chart__curves");
          return curves.width === Math.floor(
            chart.getBoundingClientRect().width *
            Math.min(window.devicePixelRatio || 1, 2)
          );
        }
        """
    )

    after = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          return {
            sameAnimation: animation === window.__complexityMidAnimation,
            sameCanvas: element === window.__complexityMidCanvas,
            pixelsChanged:
              element.toDataURL() !== window.__complexityLightPixels,
            width: element.width,
            currentTime: animation.currentTime,
            clipPath: getComputedStyle(element).clipPath,
          };
        }
        """
    )

    assert after["sameAnimation"]
    assert after["sameCanvas"]
    assert after["pixelsChanged"]
    assert after["width"] != before["width"]
    assert after["currentTime"] == pytest.approx(before["currentTime"], abs=0.01)
    assert after["clipPath"] == before["clipPath"]


def test_reveal_uses_css_without_inline_frames_or_dom_replacement(
    driver: DriverFactory,
) -> None:
    """Prove that intermediate drawing frames come from CSS alone.

    The requirement prefers a CSS animation, so the IntersectionObserver may
    set one persistent marker but must not run a JavaScript frame loop, rewrite
    inline ``clip-path`` values, append temporary masks, or replace either
    canvas. As soon as the chart mounts, this test records both layer nodes and
    observes the complete chart subtree. It lets font and ResizeObserver setup
    finish, accepting only intrinsic canvas sizing plus the chart's calculated
    label-layout attributes; child replacement or any other initialization
    mutation still fails.

    The initialization records are then cleared before the test starts reveal
    and seeks the CSS Animation through four exact progress points. Computed
    clipping must change at every point even though the curve canvas has no
    inline animation styles and the original child nodes remain in place. The
    sole reveal mutation permitted is the wrapper marker changing from false
    to true. A requestAnimationFrame renderer that manipulates reveal styles or
    a DOM-mask implementation fails this regression without timing assumptions.
    """

    page = driver(
        stored_theme="dark",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    chart = page.locator(".complexity-chart")
    chart.evaluate(
        """
        (element) => {
          window.__complexityOriginalChildren = [...element.children];
          window.__complexityMutations = [];
          window.__complexityMutationObserver = new MutationObserver(
            (records) => {
              window.__complexityMutations.push(...records.map((record) => ({
                type: record.type,
                attributeName: record.attributeName,
                targetClass: record.target.className,
                added: record.addedNodes.length,
                removed: record.removedNodes.length,
              })));
            },
          );
          window.__complexityMutationObserver.observe(element, {
            attributes: true,
            childList: true,
            subtree: true,
          });
        }
        """
    )
    _wait_for_initial_chart_layout(page)
    expect(chart).to_have_attribute("data-curves-revealed", "false")
    initialization = chart.evaluate(
        """
        (element) => {
          window.__complexityMutationObserver.takeRecords().forEach((record) => {
            window.__complexityMutations.push({
              type: record.type,
              attributeName: record.attributeName,
              targetClass: record.target.className,
              added: record.addedNodes.length,
              removed: record.removedNodes.length,
            });
          });
          const result = {
            sameChildren:
              element.children.length ===
                window.__complexityOriginalChildren.length &&
              [...element.children].every(
                (child, index) =>
                  child === window.__complexityOriginalChildren[index],
              ),
            mutations: window.__complexityMutations,
          };
          window.__complexityMutations = [];
          return result;
        }
        """
    )
    assert initialization["sameChildren"]
    assert all(
        mutation["type"] == "attributes"
        and (
            (
                mutation["targetClass"]
                in {
                    "complexity-chart__layer complexity-chart__static",
                    "complexity-chart__layer complexity-chart__curves",
                }
                and mutation["attributeName"] in {"width", "height"}
            )
            or (
                mutation["targetClass"] == "complexity-chart"
                and mutation["attributeName"]
                in {"style", "data-label-layout-width"}
            )
        )
        for mutation in initialization["mutations"]
    )

    _center_chart_panel(page)
    curves = page.locator(".complexity-chart__curves")
    _wait_for_curve_animation(page)
    frames = curves.evaluate(
        """
        (element) => {
          const [animation] = element.getAnimations();
          animation.pause();
          return [0, 400, 800, 1600].map((time) => {
            animation.currentTime = time;
            return getComputedStyle(element).clipPath;
          });
        }
        """
    )
    state = chart.evaluate(
        """
        (element) => {
          window.__complexityMutationObserver.takeRecords().forEach((record) => {
            window.__complexityMutations.push({
              type: record.type,
              attributeName: record.attributeName,
              targetClass: record.target.className,
              added: record.addedNodes.length,
              removed: record.removedNodes.length,
            });
          });
          return {
            sameChildren:
              element.children.length ===
                window.__complexityOriginalChildren.length &&
              [...element.children].every(
                (child, index) =>
                  child === window.__complexityOriginalChildren[index],
              ),
            inlineClip:
              element.querySelector(".complexity-chart__curves").style.clipPath,
            inlineAnimation:
              element.querySelector(".complexity-chart__curves").style.animation,
            mutations: window.__complexityMutations,
          };
        }
        """
    )

    assert len(set(frames)) == 4
    assert state["sameChildren"]
    assert state["inlineClip"] == ""
    assert state["inlineAnimation"] == ""
    assert state["mutations"] == [
        {
            "type": "attributes",
            "attributeName": "data-curves-revealed",
            "targetClass": "complexity-chart",
            "added": 0,
            "removed": 0,
        }
    ]


def test_batched_intersection_delivery_remembers_a_fast_complete_pass(
    driver: DriverFactory,
) -> None:
    """Catch a fully visible frame even inside one fast-scroll observer batch.

    Browsers may coalesce several threshold crossings into one
    IntersectionObserver delivery. During a rapid swipe that batch can contain
    “below threshold”, “fully visible”, and “already leaving” entries in that
    order. Reading only ``entries[0]`` loses the qualifying middle frame and
    postpones the requested first reveal until the user scrolls back.

    A controlled observer supplies all three entries in one callback, with no
    scheduler or wall-clock dependency. The test resolves that observer by its
    ``.chart-panel`` target rather than construction order because the tokamak and
    tentacle visibility observers may mount before or after it. The implementation
    must inspect the complete batch, set its persistent marker once, disconnect
    that observer, and instantiate exactly one CSS animation. Reverting the
    callback to ``([entry]) => ...`` reproduces the fast-scroll defect and makes
    this test fail.
    """

    page = driver(
        stored_theme="light",
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    _install_controlled_intersection_observer(page)
    chart = page.locator(".complexity-chart")
    expect(chart).to_have_attribute("data-curves-revealed", "false")
    page.wait_for_function(
        """
        () =>
          window.__complexityControlledActiveChartObservers().length === 1
        """
    )

    result = page.evaluate(
        """
        () => {
          const [observer] =
            window.__complexityControlledActiveChartObservers();
          const threshold = Number(observer.options.threshold);
          const target = observer.targets[0];
          const disconnectBefore = observer.disconnectCount;
          observer.callback([
            {
              target,
              isIntersecting: true,
              intersectionRatio: Math.max(0, threshold - 0.1),
            },
            {
              target,
              isIntersecting: true,
              intersectionRatio: Math.min(1, threshold + 0.001),
            },
            {
              target,
              isIntersecting: false,
              intersectionRatio: 0,
            },
          ]);
          return {
            threshold,
            targetClass: target.className,
            disconnectBefore,
            disconnectAfter: observer.disconnectCount,
          };
        }
        """
    )

    assert result["targetClass"] == "chart-panel"
    assert result["threshold"] == pytest.approx(0.985, abs=0.001)
    assert result["disconnectAfter"] == result["disconnectBefore"] + 1
    _wait_for_curve_animation(page)


def test_stale_observer_from_old_viewport_cannot_start_the_reveal(
    driver: DriverFactory,
) -> None:
    """Ignore a disconnected observer callback calculated for old geometry.

    Resizing a short landscape window to a tall viewport changes the maximum
    attainable intersection and therefore replaces the adaptive observer. The
    platform is still allowed to deliver a callback that was already queued by
    the disconnected old observer. Without a generation guard, that stale
    callback can reveal the chart at a ratio that is valid for the old short
    window but below the new 98.5% requirement.

    This deterministic test captures the active short-window chart observer by
    its ``.chart-panel`` target, resizes until a new chart observer with a higher
    threshold exists, and then manually delivers an entry whose ratio is computed
    strictly between the old and new thresholds through the stale callback.
    Resolving by target prevents unrelated tokamak or tentacle observers from
    making mount order part of the contract. The marker must remain false. Only a
    qualifying entry from the newest observer may start the one CSS animation.
    Removing the generation check reproduces the race and makes the first marker
    assertion fail.
    """

    page = driver(
        stored_theme="dark",
        viewport=(844, 320),
        load_interactive_visuals=False,
    )
    _install_controlled_intersection_observer(page)
    page.wait_for_function(
        """
        () =>
          window.__complexityControlledActiveChartObservers().length === 1
        """
    )
    before_resize = page.evaluate(
        """
        () => {
          const observers = window.__complexityControlledChartObservers();
          [window.__complexityStaleObserver] =
            window.__complexityControlledActiveChartObservers();
          return {
            count: observers.length,
            threshold: Number(
              window.__complexityStaleObserver.options.threshold,
            ),
          };
        }
        """
    )
    assert before_resize["threshold"] < 0.985

    page.set_viewport_size({"width": 844, "height": 900})
    page.wait_for_function(
        """
        (oldState) =>
          window.__complexityControlledChartObservers().length >
            oldState.count &&
          window.__complexityControlledActiveChartObservers().length === 1 &&
          Number(
            window.__complexityControlledActiveChartObservers()[0]
              .options.threshold
          ) > oldState.threshold
        """,
        arg=before_resize,
    )
    after_resize = page.evaluate(
        """
        () => {
          const [observer] =
            window.__complexityControlledActiveChartObservers();
          return {
            staleDisconnectCount:
              window.__complexityStaleObserver.disconnectCount,
            threshold: Number(observer.options.threshold),
          };
        }
        """
    )
    assert after_resize["staleDisconnectCount"] >= 1
    stale_ratio = (
        before_resize["threshold"] + after_resize["threshold"]
    ) / 2
    assert before_resize["threshold"] < stale_ratio
    assert stale_ratio < after_resize["threshold"]

    page.evaluate(
        """
        (staleRatio) => {
          const observer = window.__complexityStaleObserver;
          observer.callback([{
            target: observer.targets[0],
            isIntersecting: true,
            intersectionRatio: staleRatio,
          }]);
        }
        """,
        stale_ratio,
    )
    chart = page.locator(".complexity-chart")
    expect(chart).to_have_attribute("data-curves-revealed", "false")

    page.evaluate(
        """
        () => {
          const [observer] =
            window.__complexityControlledActiveChartObservers();
          observer.callback([{
            target: observer.targets[0],
            isIntersecting: true,
            intersectionRatio: 0.99,
          }]);
        }
        """
    )
    _wait_for_curve_animation(page)
