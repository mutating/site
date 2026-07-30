"""End-to-end coverage for every internal fragment link on the landing page."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect


DriverFactory = Callable[..., Page]

ANCHOR_TARGET_IDS = {
    "sections",
    "complexity",
    "containment",
    "thesis",
    "what-we-build",
    "scale",
    "feedback",
    "the-deal",
    "open-source",
    "field-notes",
    "contact",
}

BRAND_RELOAD_CASES = [
    (None, "intermediate"),
    ("sections", "eye"),
    ("complexity", "tmc"),
    ("containment", "expanded"),
    ("thesis", "intermediate"),
    ("what-we-build", "eye"),
    ("scale", "tmc"),
    ("feedback", "expanded"),
    ("the-deal", "intermediate"),
    ("open-source", "eye"),
    ("field-notes", "tmc"),
    ("contact", "expanded"),
]


class AnchorGeometry(TypedDict):
    """Describe one fragment target relative to the document and viewport."""

    target_top: float
    target_document_top: float
    scroll_margin_top: float
    maximum_scroll: float
    scroll_y: float
    header_bottom: float


def _wait_for_anchor_alignment(
    page: Page,
    target_id: str,
    *,
    timeout: float = 5_000,
) -> None:
    """Wait until a fragment target reaches its exact unclipped scroll position."""

    try:
        page.wait_for_function(
            """
            (targetId) => {
              const target = document.getElementById(targetId);
              if (!target) return false;

              const rect = target.getBoundingClientRect();
              const documentTop = rect.top + window.scrollY;
              const marginTop =
                Number.parseFloat(getComputedStyle(target).scrollMarginTop) || 0;
              const maximumScroll = Math.max(
                0,
                document.documentElement.scrollHeight - window.innerHeight,
              );
              const expectedScroll = Math.min(
                Math.max(documentTop - marginTop, 0),
                maximumScroll,
              );
              const expectedTop = documentTop - expectedScroll;

              return Math.abs(rect.top - expectedTop) <= 1;
            }
            """,
            arg=target_id,
            timeout=timeout,
        )
    except PlaywrightTimeoutError as error:
        geometry = _read_anchor_geometry(page, target_id)
        raise AssertionError(
            f"#{target_id} did not align at {page.url}: {geometry}"
        ) from error


def _read_anchor_geometry(page: Page, target_id: str) -> AnchorGeometry:
    """Measure a fragment target and the sticky header in viewport coordinates."""

    return page.locator(f'[id="{target_id}"]').evaluate(
        """
        (target) => {
          const targetRect = target.getBoundingClientRect();
          const headerRect = document
            .querySelector(".site-header")
            .getBoundingClientRect();

          return {
            target_top: targetRect.top,
            target_document_top: targetRect.top + window.scrollY,
            scroll_margin_top:
              Number.parseFloat(
                getComputedStyle(target).scrollMarginTop,
              ) || 0,
            maximum_scroll: Math.max(
              0,
              document.documentElement.scrollHeight - window.innerHeight,
            ),
            scroll_y: window.scrollY,
            header_bottom: headerRect.bottom,
          };
        }
        """
    )


def _assert_anchor_is_aligned(
    page: Page,
    target_id: str,
    *,
    wait: bool = True,
) -> None:
    """Assert the browser used the target margin and did not hide it under chrome."""

    if wait:
        _wait_for_anchor_alignment(page, target_id)

    geometry = _read_anchor_geometry(page, target_id)
    expected_scroll = min(
        max(
            geometry["target_document_top"]
            - geometry["scroll_margin_top"],
            0,
        ),
        geometry["maximum_scroll"],
    )
    expected_top = geometry["target_document_top"] - expected_scroll

    assert abs(geometry["target_top"] - expected_top) <= 1
    assert abs(geometry["scroll_y"] - expected_scroll) <= 1

    assert geometry["target_top"] >= geometry["header_bottom"]


def _interrupt_anchor_alignment_at_top(
    page: Page,
    *,
    restore_scroll_behavior: bool = True,
) -> str:
    """Send real Home intent and wait for its own non-animated endpoint.

    The helper deliberately does not follow the keyboard gesture with a
    programmatic ``scrollTo(0)``. Chromium can occasionally finish one last
    native Home scroll step after that competing command, making a correct
    cancellation look as though the site pulled the page back toward its hash.
    Waiting for the real gesture to reach zero preserves the user-input contract
    while removing the test-only race between two independent scroll sources.
    """

    previous_scroll_behavior = page.evaluate(
        """
        () => {
          if (document.activeElement instanceof HTMLElement) {
            document.activeElement.blur();
          }
          const root = document.documentElement;
          const previousScrollBehavior = root.style.scrollBehavior;
          root.style.scrollBehavior = "auto";
          return previousScrollBehavior;
        }
        """
    )
    page.keyboard.press("Home")
    page.wait_for_function(
        "window.__tmcAnchorAlignmentInterrupted === true"
    )
    page.wait_for_function("window.scrollY === 0")
    if restore_scroll_behavior:
        page.evaluate(
            """
            (previousScrollBehavior) => {
              document.documentElement.style.scrollBehavior =
                previousScrollBehavior;
            }
            """,
            previous_scroll_behavior,
        )

    return previous_scroll_behavior


def test_every_internal_fragment_link_has_one_real_target(
    driver: DriverFactory,
) -> None:
    """Keep every internal link connected to one unambiguous element.

    A fragment click can still update the address bar even when its target was
    renamed, deleted, or accidentally duplicated. In that broken state the
    browser either stays where it is or chooses an unpredictable duplicate, so
    individual click tests can miss a stale link elsewhere in the header,
    footer, hero, cards, or section labels.

    This structural test inspects every rendered ``href`` beginning with ``#``.
    It requires a non-empty fragment, URI-decodes it with the same fallback used
    by the site, and proves that exactly one element owns the resulting ID. It
    also compares the discovered target set with the complete intentional list,
    so adding or removing an anchor requires an explicit test update instead of
    silently reducing coverage.
    """

    page = driver(load_interactive_visuals=False)
    fragment_links = page.locator('a[href^="#"]').evaluate_all(
        """
        (links) => {
          const allIds = Array.from(
            document.querySelectorAll("[id]"),
            (element) => element.id,
          );

          return links.map((link) => {
            const href = link.getAttribute("href");
            const encodedId = href.slice(1);
            let id = encodedId;

            try {
              id = decodeURIComponent(encodedId);
            } catch {}

            return {
              href,
              id,
              targetCount: allIds.filter(
                (candidate) => candidate === id,
              ).length,
            };
          });
        }
        """
    )

    assert fragment_links
    assert {item["id"] for item in fragment_links} == ANCHOR_TARGET_IDS

    for item in fragment_links:
        assert item["href"] != "#"
        assert item["id"]
        assert item["targetCount"] == 1, item


def test_top_fragment_is_completely_absent_from_rendered_elements(
    driver: DriverFactory,
) -> None:
    """Forbid the retired ``#top`` fragment everywhere in the rendered page.

    The former logo was an anchor with ``href="#top"`` and the root ``main``
    owned ``id="top"``. That made even an ordinary home-logo click create a
    fragment entry instead of performing the newly required hashless reload.
    Merely changing the click handler is insufficient: a stale ``href``, ID,
    data attribute, or nested element could still expose the retired fragment
    to the browser's default navigation.

    This structural regression scans every attribute on every rendered
    element, rejects both an exact ``#top`` value and any ``id="top"`` owner,
    verifies that the unified logo control is a ``div`` rather than an anchor,
    and confirms that it has no ``href`` at all. It therefore fails if any
    future refactor reintroduces the old fragment independently of the reload
    implementation.
    """

    page = driver(load_interactive_visuals=False)
    violations = page.evaluate(
        """
        () =>
          [...document.querySelectorAll("*")].flatMap((element) =>
            [...element.attributes]
              .filter(
                (attribute) =>
                  attribute.value === "#top" ||
                  (attribute.name === "id" && attribute.value === "top"),
              )
              .map((attribute) => ({
                tag: element.tagName,
                name: attribute.name,
                value: attribute.value,
              })),
          )
        """
    )
    brand = page.locator(".brand")

    assert violations == []
    assert brand.count() == 1
    assert brand.evaluate("(element) => element.tagName") == "DIV"
    expect(brand).to_have_attribute("role", "link")
    assert brand.get_attribute("href") is None
    assert page.url.endswith("/")


def _click_brand_variant(page: Page, variant: str) -> None:
    """Activate one precise visible sub-area of the unified logo control."""

    if variant == "eye":
        page.locator(".animated-eye-logo").click()
        return

    if variant == "tmc":
        page.locator(
            ".brand__word-part--t .brand__word-anchor"
        ).click()
        return

    wordmark = page.locator(".brand__wordmark")
    wordmark.hover()

    if variant == "expanded":
        page.wait_for_function(
            """
            () => [...document.querySelectorAll(".brand__word-reveal")].every(
              (element) =>
                element.getBoundingClientRect().width > 1 &&
                getComputedStyle(element).opacity === "1",
            )
            """
        )
        page.locator(
            '.brand__word-reveal[data-text="ompany"]'
        ).click()
        return

    if variant != "intermediate":
        raise AssertionError(f"Unknown brand click variant: {variant}")

    reveal = page.locator('.brand__word-reveal[data-text="utating"]')
    page.wait_for_function(
        """
        () => {
          const element = document.querySelector(
            '.brand__word-reveal[data-text="utating"]',
          );
          const opacity = Number.parseFloat(getComputedStyle(element).opacity);
          return element.getBoundingClientRect().width > 2 &&
            opacity > 0 &&
            opacity < 1;
        }
        """
    )
    intermediate_state = page.locator(".brand").evaluate(
        """
        (brand) => {
          for (const animation of brand.getAnimations({subtree: true})) {
            const duration = Number(animation.effect?.getTiming().duration);
            if (Number.isFinite(duration) && duration > 0) {
              animation.currentTime = duration / 2;
              animation.pause();
            }
          }

          const reveal = brand.querySelector(
            '.brand__word-reveal[data-text="utating"]',
          );
          const rect = reveal.getBoundingClientRect();
          return {
            width: rect.width,
            fullWidth: reveal.scrollWidth,
            opacity: Number.parseFloat(getComputedStyle(reveal).opacity),
          };
        }
        """
    )
    assert 0 < intermediate_state["width"] < intermediate_state["fullWidth"]
    assert 0 < intermediate_state["opacity"] < 1
    reveal.click()


@pytest.mark.parametrize(
    ("target_id", "click_variant"),
    BRAND_RELOAD_CASES,
    ids=[
        f"{target_id or 'arbitrary-scroll'}-{click_variant}"
        for target_id, click_variant in BRAND_RELOAD_CASES
    ],
)
def test_every_logo_sub_area_clears_fragments_and_reloads_home(
    driver: DriverFactory,
    base_url: str,
    target_id: str | None,
    click_variant: str,
) -> None:
    """Reload the hashless home page from every logo area and scroll context.

    All visible pieces of the header logo now form one control: the animated
    eye, the compact ``TMC`` letters, a fully revealed word segment, and a
    partially revealed segment while its 350 ms transition is in progress.
    The former nested behaviors were inconsistent—clicking the text created
    ``#top``, while clicking the eye reloaded the current fragment—so testing
    only the wrapper's center would leave the original defects undetected.

    The parameter table covers every remaining internal fragment target and an
    additional arbitrary non-anchor scroll position, rotating through all four
    required click variants. Before activation, the test instruments both the
    History API and ``hashchange`` while persisting observations through the
    upcoming document load. It then requires a real browser navigation and uses
    a new-document init script plus an in-memory sentinel to prove that the
    current JavaScript document was destroyed and loaded again. This is more
    robust than ``PerformanceNavigationTiming.type``: Chromium reports
    ``navigate`` rather than ``reload`` when ``history.replaceState`` removes a
    fragment immediately before ``location.reload()``.

    Finally, the recorded history proves that ``#top`` was never created even
    transiently. Reintroducing an anchor default, preserving the active hash
    during reload, handling only the eye, omitting the expanded hit area, or
    losing clicks midway through the wordmark transition makes at least one
    parameterized case fail.
    """

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )

    if target_id is None:
        page.evaluate(
            """
            () => window.scrollTo({
              top: document.documentElement.scrollHeight * 0.42,
              behavior: "instant",
            })
            """
        )
        assert page.evaluate("window.scrollY") > 0
        assert page.evaluate("window.location.hash") == ""
    else:
        page.goto(
            f"{base_url}/#{target_id}",
            wait_until="domcontentloaded",
        )
        page.locator('html[data-theme-ready="true"]').wait_for(
            state="attached",
        )
        _assert_anchor_is_aligned(page, target_id)
        assert page.url.endswith(f"#{target_id}")

    page.evaluate(
        """
        () => {
          const key = "tmc-logo-navigation-observed-hashes";
          const record = (url) => {
            const parsed = new URL(url ?? location.href, location.href);
            const values = JSON.parse(sessionStorage.getItem(key) || "[]");
            values.push(parsed.hash);
            sessionStorage.setItem(key, JSON.stringify(values));
          };

          sessionStorage.setItem(key, JSON.stringify([location.hash]));
          addEventListener("hashchange", () => record(location.href));

          const replaceState = history.replaceState.bind(history);
          history.replaceState = (state, unused, url) => {
            record(url);
            return replaceState(state, unused, url);
          };

          const pushState = history.pushState.bind(history);
          history.pushState = (state, unused, url) => {
            record(url);
            return pushState(state, unused, url);
          };
        }
        """
    )
    page.add_init_script(
        """
        {
          const key = "tmc-logo-document-load-count";
          const count = Number.parseInt(sessionStorage.getItem(key) || "0", 10);
          sessionStorage.setItem(key, String(count + 1));
        }
        """
    )
    page.evaluate(
        """
        () => {
          sessionStorage.setItem("tmc-logo-document-load-count", "0");
          window.__tmcBrandPreReloadDocument = true;
        }
        """
    )

    _click_brand_variant(page, click_variant)
    page.wait_for_function(
        """
        () => (
          sessionStorage.getItem("tmc-logo-document-load-count") === "1"
          && window.__tmcBrandPreReloadDocument === undefined
        )
        """,
        timeout=5_000,
    )

    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    expect(page).to_have_url(f"{base_url}/")
    assert page.evaluate("window.location.hash") == ""
    assert page.evaluate(
        """
        () => sessionStorage.getItem("tmc-logo-document-load-count")
        """
    ) == "1"
    assert page.evaluate(
        """
        () => window.__tmcBrandPreReloadDocument
        """
    ) is None

    observed_hashes = page.evaluate(
        """
        () =>
          JSON.parse(
            sessionStorage.getItem(
              "tmc-logo-navigation-observed-hashes",
            ) || "[]",
          )
        """
    )
    assert "#top" not in observed_hashes
    assert observed_hashes[-1] == ""


def test_every_anchor_target_aligns_on_a_direct_deep_link(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Verify direct URLs for the complete anchor inventory.

    Anchor URLs are often opened without any preceding click: from a bookmark,
    a pasted link, another website, or a restored browser tab. Every supported
    fragment is therefore loaded as a fresh document. The test waits for the
    page's bounded layout-settling logic and then verifies the exact scroll
    position after applying ``scroll-margin-top`` and the document's maximum
    scroll limit.

    Besides checking all numbered sections, this includes the invisible
    ``#sections`` target used by “Read our thesis”, the nested
    ``#field-notes`` article that previously disappeared beneath the sticky
    header and the bottom ``#contact`` section. Any target left clipped,
    overscrolled, or connected only after an in-page click fails here.
    """

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )

    for target_id in sorted(ANCHOR_TARGET_IDS):
        page.goto("about:blank")
        page.goto(
            f"{base_url}/#{target_id}",
            wait_until="domcontentloaded",
        )
        page.locator('html[data-theme-ready="true"]').wait_for(
            state="attached",
        )

        assert page.url.endswith(f"#{target_id}")
        _assert_anchor_is_aligned(page, target_id)


@pytest.mark.parametrize(
    ("viewport", "target_id"),
    [
        ((1440, 900), "contact"),
        ((1024, 768), "containment"),
        ((390, 844), "field-notes"),
    ],
)
def test_reload_realigns_the_hash_before_dom_content_loaded_returns(
    driver: DriverFactory,
    base_url: str,
    viewport: tuple[int, int],
    target_id: str,
) -> None:
    """Prevent the reload-only flash and lost-position anchor defect.

    The former implementation corrected a fragment only from a React effect.
    During a reload the browser could finish ``DOMContentLoaded`` at ``scrollY
    = 0`` and display the hero; hydration moved the page to the requested
    section noticeably later. That race was especially visible for late-page
    targets and was sensitive to viewport width and cache timing.

    This regression test first opens a valid deep link, then uses a real Home
    key gesture to return to the top while leaving the fragment in the URL. It
    deliberately waits for fonts, pending images, and the alignment frames that
    used to be queued after ``load``. The page must respect that user intent
    instead of snapping back to the old fragment. This preparation is important:
    a reload must begin from a genuinely different scroll position rather than
    accidentally inheriting an already-correct anchor position.

    The reload assertion itself remains stricter: it runs as soon as
    ``DOMContentLoaded`` returns, without waiting for React, fonts, animation
    frames, or a polling helper. Thus the same scenario independently protects
    both sides of the race: late initial alignment must not override a user who
    has moved away, while a new navigation must reset that cancellation and
    align the requested fragment before hydration.

    To make the first half deterministic, the test dispatches the same ``load``
    signal used by the React alignment layer immediately before and after the
    Home gesture. The first signal leaves two correction frames pending for the
    cancellation path to clear when scheduling permits; the second
    deterministically proves later callbacks honor the interrupted state.
    Removing the effective guard protection therefore snaps the page back and
    fails on observable scroll geometry, rather than merely failing an
    implementation-marker assertion. The two guard levels intentionally remain
    redundant, so removing only one may preserve correct behavior.

    Desktop, intermediate, and mobile cases cover a late section, a 3D section,
    and the formerly unoffset nested Blog card. Removing the pre-hydration
    anchor bootstrap recreates the reported defect and makes this test observe
    the target thousands of pixels below the viewport.
    """

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
    )
    page.add_init_script(
        """
        addEventListener("pageshow", () => {
          window.__tmcAnchorTestPageShowObserved = true;
        }, { once: true });
        """
    )
    page.goto("about:blank")
    page.goto(
        f"{base_url}/#{target_id}",
        wait_until="load",
    )
    _assert_anchor_is_aligned(page, target_id, wait=False)

    page.wait_for_function(
        "window.__tmcAnchorTestPageShowObserved === true"
    )
    page.evaluate(
        """
        async () => {
          await document.fonts.ready;
          await Promise.all(
            [...document.images]
              .filter((image) => !image.complete)
              .map((image) => new Promise((resolve) => {
                image.addEventListener("load", resolve, { once: true });
                image.addEventListener("error", resolve, { once: true });
              })),
          );
          await new Promise((resolve) => {
            requestAnimationFrame(() => {
              requestAnimationFrame(() => {
                requestAnimationFrame(resolve);
              });
            });
          });
          window.dispatchEvent(new Event("load"));
        }
        """
    )
    _interrupt_anchor_alignment_at_top(page)
    page.evaluate(
        """
        async () => {
          window.dispatchEvent(new Event("load"));
          await new Promise((resolve) => {
            requestAnimationFrame(() => {
              requestAnimationFrame(() => {
                requestAnimationFrame(resolve);
              });
            });
          });
        }
        """
    )
    # Chromium may retain a sub-line keyboard-scroll residue after restoring
    # the page's CSS scroll behavior. A real hash snap is thousands of pixels,
    # so this tight near-top bound keeps the behavioral distinction exact.
    assert page.evaluate("window.scrollY") <= 16
    assert page.url.endswith(f"#{target_id}")

    page.reload(wait_until="domcontentloaded")

    assert page.url.endswith(f"#{target_id}")
    _assert_anchor_is_aligned(page, target_id, wait=False)


def test_non_scroll_pointer_input_does_not_cancel_anchor_alignment(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep ordinary clicks distinct from genuine scroll intent.

    The late-alignment cancellation originally listened to every
    ``pointerdown`` and ``touchstart``. That included harmless clicks on the
    theme toggle, buttons, text, and other controls: a click that never moved
    the viewport could silently disable a still-needed font or image correction.

    This regression opens a deep link, clicks the theme control with a real
    Playwright pointer sequence, then activates that focused button with Space.
    It proves both the fragment geometry and shared alignment state stay active:
    neither pointer activation nor a keyboard action consumed by an interactive
    control is scroll intent. Dispatching a later load correction must remain
    allowed. Restoring broad pointerdown or unfiltered key cancellation makes the
    interrupted flag true and fails without relying on event timing.
    """

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.goto(f"{base_url}/#field-notes", wait_until="load")
    _assert_anchor_is_aligned(page, "field-notes")

    toggle = page.get_by_role(
        "button",
        name="Toggle color theme",
        exact=True,
    )
    toggle.click()
    toggle.press("Space")

    assert page.evaluate(
        "window.__tmcAnchorAlignmentInterrupted"
    ) is False
    page.evaluate("window.dispatchEvent(new Event('load'))")
    _settle_anchor_frames = """
      () => new Promise((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            requestAnimationFrame(resolve);
          });
        });
      })
    """
    page.evaluate(_settle_anchor_frames)
    _assert_anchor_is_aligned(page, "field-notes", wait=False)


def test_only_real_tentacle_scroll_messages_cancel_anchor_alignment(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Authenticate cross-frame scroll intent before cancelling an anchor.

    Wheel input over the tentacle iframe cannot bubble into the parent window,
    so the child forwards its vertical delta with ``postMessage``. Accepting the
    type string alone would let the parent page, an unrelated frame, or a
    zero-delta message disable late anchor correction without any user scroll.

    This test sends two forged messages—one from the parent with a real delta
    and one from the expected iframe with a zero delta—and requires both to be
    ignored. It then emits the same-origin, finite, non-zero message from the
    actual tentacle frame and requires interruption. Removing source, origin, or
    delta validation breaks a corresponding assertion; rejecting the legitimate
    child-to-parent protocol breaks the final one.
    """

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.goto(f"{base_url}/#field-notes", wait_until="load")
    _assert_anchor_is_aligned(page, "field-notes")
    page.wait_for_function(
        "window.__tmcAnchorAlignmentInterrupted === false"
    )

    result = page.evaluate(
        """
        () => {
          const frame = document.querySelector(".contact__tentacles");
          const dispatch = (source, deltaY) => {
            window.dispatchEvent(
              new MessageEvent("message", {
                data: {
                  type: "muta-tentacles-scroll-input",
                  deltaY,
                },
                origin: window.location.origin,
                source,
              }),
            );
            return window.__tmcAnchorAlignmentInterrupted;
          };

          return {
            forgedParent: dispatch(window, 120),
            zeroFromFrame: dispatch(frame.contentWindow, 0),
            validFromFrame: dispatch(frame.contentWindow, 120),
          };
        }
        """
    )

    assert result == {
        "forgedParent": False,
        "zeroFromFrame": False,
        "validFromFrame": True,
    }


def test_pageshow_resets_interruption_only_for_a_restored_navigation(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Distinguish the initial pageshow race from a BFCache restoration.

    A normal ``pageshow`` follows ``load`` on a fresh document. If a very fast
    user scroll lands between those events, resetting cancellation on that
    ordinary pageshow yanks the page back to its hash. A persisted pageshow is
    different: it represents a real back/forward-cache navigation and must begin
    a new alignment session for the restored URL.

    The test first drains the document's natural ``pageshow`` so its scheduling
    cannot be confused with either controlled branch below. It then interrupts a
    deep-link alignment with the Home key, dispatches a non-persisted
    PageTransitionEvent and verifies the event causes no movement from the
    normalized top position, then dispatches its persisted counterpart and
    requires both the interruption flag and exact anchor geometry to reset.
    After the real Home gesture records user intent, the test temporarily keeps
    CSS scrolling non-animated through both controlled branches so a leftover
    smooth-scroll frame cannot masquerade as pageshow movement or undo the
    persisted realignment after the wait observes it. A real erroneous hash snap
    still moves this late mobile target by several thousand pixels. Treating every
    pageshow identically breaks one of these two externally visible outcomes.
    """

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.add_init_script(
        """
        addEventListener("pageshow", () => {
          window.__tmcAnchorTestInitialPageShowObserved = true;
        }, { once: true });
        """
    )
    page.goto("about:blank")
    page.goto(f"{base_url}/#field-notes", wait_until="load")
    page.wait_for_function(
        "window.__tmcAnchorTestInitialPageShowObserved === true"
    )
    _assert_anchor_is_aligned(page, "field-notes")

    previous_scroll_behavior = _interrupt_anchor_alignment_at_top(
        page,
        restore_scroll_behavior=False,
    )
    try:
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
        settled_scroll_y = page.evaluate("window.scrollY")
        assert settled_scroll_y == 0

        page.evaluate(
            """
            () => window.dispatchEvent(
              new PageTransitionEvent("pageshow", { persisted: false }),
            )
            """
        )
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
        after_pageshow_scroll_y = page.evaluate("window.scrollY")
        assert abs(after_pageshow_scroll_y - settled_scroll_y) <= 1
        assert page.evaluate(
            "window.__tmcAnchorAlignmentInterrupted"
        ) is True

        page.evaluate(
            """
            () => window.dispatchEvent(
              new PageTransitionEvent("pageshow", { persisted: true }),
            )
            """
        )
        page.wait_for_function(
            "window.__tmcAnchorAlignmentInterrupted === false"
        )
        _assert_anchor_is_aligned(page, "field-notes")
    finally:
        page.evaluate(
            """
            (previous) => {
              document.documentElement.style.scrollBehavior = previous;
            }
            """,
            previous_scroll_behavior,
        )


def test_hashchange_starts_a_new_alignment_after_user_scroll(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Let a new fragment supersede cancellation from an earlier fragment.

    Scroll intent cancels only pending corrections for the currently active
    hash. It must not permanently disable internal navigation. This test moves
    away from ``#field-notes`` with a real Home key, then assigns
    ``#complexity`` and requires the hashchange path to clear interruption and
    align the new target exactly.

    The initial real ``pageshow`` is explicitly drained before Home. Without
    that boundary, Chromium may still be completing its first fragment
    navigation while the test starts the user gesture, producing a race between
    two browser-owned scroll operations rather than exercising the later
    hashchange contract. Starting from ``about:blank`` also guarantees the
    observed pageshow belongs to this deep-link navigation.

    Forgetting to reset the shared flag on hashchange leaves the user stranded
    at the top even though the address bar changes; resetting on unrelated
    pointer activity is covered separately.
    """

    page = driver(
        viewport=(390, 844),
        load_interactive_visuals=False,
    )
    page.add_init_script(
        """
        addEventListener("pageshow", () => {
          window.__tmcAnchorTestInitialPageShowObserved = true;
        }, { once: true });
        """
    )
    page.goto("about:blank")
    page.goto(f"{base_url}/#field-notes", wait_until="load")
    page.wait_for_function(
        "window.__tmcAnchorTestInitialPageShowObserved === true"
    )
    _assert_anchor_is_aligned(page, "field-notes")

    _interrupt_anchor_alignment_at_top(page)

    page.evaluate("window.location.hash = '#complexity'")
    expect(page).to_have_url(f"{base_url}/#complexity")
    page.wait_for_function(
        "window.__tmcAnchorAlignmentInterrupted === false"
    )
    _assert_anchor_is_aligned(page, "complexity")


def test_representative_anchor_clicks_update_the_url_and_scroll(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Exercise every distinct kind of clickable anchor on the page.

    A correct fragment target is insufficient if React event handling, sticky
    navigation, smooth scrolling, or a nested layout prevents the actual link
    from navigating. This test clicks representative links from the hero,
    numbered section label and sticky desktop navigation. After each real
    Playwright click it verifies both externally visible outcomes: the
    exact fragment is present in the address bar and the corresponding target
    is positioned with the intended header clearance.

    The sequence explicitly covers the invisible list-start target, a regular
    numbered section, the nested Blog card, Contact near the document bottom,
    and the open-source section. The separate logo-navigation regression covers
    the intentionally hashless full reload back to the home page.
    """

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    click_cases = [
        ('.hero__actions a[href="#sections"]', "sections"),
        ('#complexity a[href="#complexity"]', "complexity"),
        ('.main-nav a[href="#field-notes"]', "field-notes"),
        ('.main-nav a[href="/#contact"]', "contact"),
        ('.hero__actions a[href="#open-source"]', "open-source"),
    ]

    for selector, target_id in click_cases:
        link = page.locator(selector)
        assert link.count() == 1
        link.click()

        expect(page).to_have_url(f"{base_url}/#{target_id}")
        _assert_anchor_is_aligned(page, target_id)


@pytest.mark.parametrize(
    ("viewport", "selector", "open_mobile_menu"),
    [
        ((1440, 900), '.main-nav a[href="/#contact"]', False),
        ((800, 900), '.mobile-menu nav a[href="/#contact"]', True),
    ],
    ids=["desktop-header", "mobile-header"],
)
def test_header_contact_returns_to_home_before_resolving_the_fragment(
    driver: DriverFactory,
    base_url: str,
    viewport: tuple[int, int],
    selector: str,
    open_mobile_menu: bool,
) -> None:
    """Make the shared header's Contact link safe on future nested pages.

    A fragment-only ``href="#contact"`` works on today's one-page site but is
    resolved against whichever pathname currently owns the header. Reusing that
    header on a future Blog or Sources page would therefore search for a
    nonexistent local ``#contact`` target instead of returning to the landing
    page.

    This regression emulates that future state without needing a second route:
    it moves the current document to a nested same-origin pathname through the
    History API, then clicks the real desktop or mobile Contact link. The
    authored href must be ``/#contact``, its fully resolved URL must point to
    the origin root, clicking it must replace the nested pathname with ``/``,
    and the landing-page Contact section must align below the sticky header.
    A fragment-only link, relative nested path, desktop/mobile mismatch, or URL
    update without correct scrolling makes the test fail.
    """

    page = driver(
        viewport=viewport,
        load_interactive_visuals=False,
    )
    future_url = f"{base_url}/future/about?preview=1#feedback"
    page.evaluate(
        """
        (url) => {
          history.replaceState({futurePage: true}, "", url);
        }
        """,
        future_url,
    )
    expect(page).to_have_url(future_url)

    if open_mobile_menu:
        page.locator(".mobile-menu > summary").click()

    contact_link = page.locator(selector)
    assert contact_link.count() == 1
    link_contract = contact_link.evaluate(
        """
        (link) => {
          const resolved = new URL(link.href);
          return {
            authored: link.getAttribute("href"),
            pathname: resolved.pathname,
            hash: resolved.hash,
          };
        }
        """
    )
    assert link_contract == {
        "authored": "/#contact",
        "pathname": "/",
        "hash": "#contact",
    }

    contact_link.click()
    expect(page).to_have_url(f"{base_url}/#contact")
    _assert_anchor_is_aligned(page, "contact")


def test_back_and_forward_history_realign_each_fragment(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Keep browser history navigation synchronized with fragment positions.

    Clicking two anchors creates same-document history entries. Back and
    Forward do not reload React, so they depend on the ``hashchange`` path
    rather than the pre-hydration bootstrap used by a fresh URL. This test
    starts at Complexity, clicks the sticky Contact link, then traverses both
    history directions. At every stop it checks the URL and geometry together;
    a handler that updates only one of them, fires too early, or ignores a
    restored hash cannot pass.
    """

    page = driver(
        viewport=(1440, 900),
        load_interactive_visuals=False,
    )
    page.goto(
        f"{base_url}/#complexity",
        wait_until="domcontentloaded",
    )
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    _assert_anchor_is_aligned(page, "complexity")

    contact_link = page.locator('.main-nav a[href="/#contact"]')
    assert contact_link.count() == 1
    contact_link.click()
    expect(page).to_have_url(f"{base_url}/#contact")
    _assert_anchor_is_aligned(page, "contact")

    page.go_back()
    expect(page).to_have_url(f"{base_url}/#complexity")
    _assert_anchor_is_aligned(page, "complexity")

    page.go_forward()
    expect(page).to_have_url(f"{base_url}/#contact")
    _assert_anchor_is_aligned(page, "contact")


def test_unknown_and_malformed_fragments_fail_safely(
    driver: DriverFactory,
    base_url: str,
) -> None:
    """Ensure unsupported hash text never crashes or redirects the landing page.

    Fragment strings can be stale, mistyped, or contain invalid percent
    encoding. The anchor resolver deliberately keeps a malformed fragment raw
    when ``decodeURIComponent`` rejects it and does nothing when no element has
    that ID. This test opens both an ordinary missing target and malformed URI
    bytes, records uncaught page errors, and proves that the hero remains usable
    at the top while the original fragment stays visible for diagnosis.

    The behavior matters because an exception in the early head bootstrap would
    otherwise interrupt scripts before React hydration, potentially breaking
    theme setup and every subsequent interaction on the page.
    """

    page = driver(load_interactive_visuals=False)
    page_errors: list[Exception] = []
    page.on("pageerror", lambda error: page_errors.append(error))

    for fragment in ("does-not-exist", "%E0%A4%A"):
        page.goto(
            f"{base_url}/#{fragment}",
            wait_until="domcontentloaded",
        )
        page.locator('html[data-theme-ready="true"]').wait_for(
            state="attached",
        )

        expect(
            page.get_by_role(
                "heading",
                name="AI systems that doubt.",
                exact=True,
            )
        ).to_be_visible()
        assert page.url.endswith(f"#{fragment}")
        assert page.evaluate("window.scrollY") == 0

    assert page_errors == []


def test_section_index_changes_only_its_decoration_on_hover(
    driver: DriverFactory,
) -> None:
    """Preserve the intentionally quiet visual treatment of anchor labels.

    Numbered micro-labels must look exactly like ordinary muted metadata until
    the pointer is over them, at which point CSS adds an underline. This test
    captures the computed typography and color before hovering, performs a real
    hover, and verifies that only ``text-decoration-line`` changes. It protects
    both halves of the original requirement: discoverability on interaction and
    no permanent color, size, weight, spacing, or font change at rest.
    """

    page = driver(load_interactive_visuals=False)
    section_link = page.locator('#complexity a[href="#complexity"]')
    assert section_link.count() == 1

    before = section_link.evaluate(
        """
        (link) => {
          const style = getComputedStyle(link);
          return {
            color: style.color,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            letterSpacing: style.letterSpacing,
            textDecorationLine: style.textDecorationLine,
          };
        }
        """
    )
    section_link.hover()
    after = section_link.evaluate(
        """
        (link) => {
          const style = getComputedStyle(link);
          return {
            color: style.color,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            letterSpacing: style.letterSpacing,
            textDecorationLine: style.textDecorationLine,
          };
        }
        """
    )

    assert before["textDecorationLine"] == "none"
    assert after["textDecorationLine"] == "underline"

    for property_name in (
        "color",
        "fontFamily",
        "fontSize",
        "fontWeight",
        "letterSpacing",
    ):
        assert after[property_name] == before[property_name]
