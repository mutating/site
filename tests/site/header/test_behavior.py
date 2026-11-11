"""Header behavior added before migration; visual states live alongside it."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import Page, expect

DriverFactory = Callable[..., Page]


def test_content_iframe_press_closes_header_menus(driver: DriverFactory) -> None:
    page = driver(
        viewport=(390, 780), initial_path="/ru/", stored_language="ru",
        load_interactive_visuals=True, bounded_tentacle_rendering=True,
        manual_tentacle_rendering=True, reduced_motion="reduce",
    )
    page.locator("#contact").scroll_into_view_if_needed()
    frame = page.locator("iframe.contact__tentacles")
    expect(frame).to_have_attribute("src", re.compile(r"/muta-tentacles\.html\?"))
    expect(page.frame_locator("iframe.contact__tentacles").locator("canvas")).to_be_visible()
    menu = page.locator(".tmc-mobile-menu")
    languages = menu.locator(".tmc-language-switcher--mobile")
    menu.locator(":scope > summary").click()
    languages.locator("summary").click()
    point = frame.evaluate("""frame => {
      const box = frame.getBoundingClientRect();
      const x = Math.min(innerWidth - 30, box.right - 30);
      const y = Math.min(innerHeight - 30, box.bottom - 30);
      return {x, y, exposed: document.elementFromPoint(x, y) === frame};
    }""")
    assert point["exposed"], "The click must reach the actual iframe, outside the menus"
    page.mouse.click(point["x"], point["y"])
    expect(frame).to_be_focused()
    expect(menu).not_to_have_attribute("open", "")
    expect(languages).not_to_have_attribute("open", "")


@pytest.mark.parametrize("width", [280, 320, 380, 390, 900])
def test_post_fragment_heading_clears_the_actual_header(
    driver: DriverFactory, width: int,
) -> None:
    page = driver(
        viewport=(width, 800),
        initial_path="/blog/markdown-feature-fixture/#structured-signals",
    )
    page.wait_for_function("""() => {
      const heading = document.getElementById('structured-signals');
      return heading.getBoundingClientRect().top >=
        document.querySelector('.tmc-header-shell').getBoundingClientRect().bottom;
    }""", timeout=5_000)
    expect(page.locator("#structured-signals")).to_be_in_viewport()
    page.set_viewport_size({"width": 320 if width > 380 else 900, "height": 800})
    page.wait_for_function("""() => document.getElementById('structured-signals')
      .getBoundingClientRect().top >= document.querySelector('.tmc-header-shell')
      .getBoundingClientRect().bottom""", timeout=5_000)


def _observer_failure_script(failure: str) -> str:
    observer = (
        "undefined" if failure == "missing" else
        "class { constructor() { throw new Error('ResizeObserver unavailable'); } }"
    )
    return (
        "window.headerInitializationErrors = []; "
        "addEventListener('error', event => window.headerInitializationErrors.push(event.message)); "
        f"window.ResizeObserver = {observer};"
    )


def _listener_failure_script(failure: str) -> str:
    if failure == "missing":
        return "window.AbortController = undefined;"
    if failure == "constructor":
        return "window.AbortController = class { constructor() { throw new Error('AbortController unavailable'); } };"
    return """(() => {
      const add = EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener = function(type, listener, options) {
        if (options && typeof options === 'object' && 'signal' in options) {
          throw new TypeError('Event listener AbortSignal is unavailable');
        }
        return add.call(this, type, listener, options);
      };
    })();"""


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("width", [1440, 1040, 360])
@pytest.mark.parametrize(
    "destination", ["sources", "news", "blog", "contact", "platform"]
)
def test_header_destinations_are_reachable(
    driver: DriverFactory,
    language: str,
    width: int,
    destination: str,
) -> None:
    """Follow every header destination in each layout and both languages."""
    prefix = "/ru" if language == "ru" else ""
    page = driver(
        stored_language=language, viewport=(width, 900), initial_path=prefix + "/"
    )
    if width <= 1120 and destination != "platform":
        menu = page.locator(".tmc-mobile-menu")
        menu.locator(":scope > summary").click()
        navigation = menu.locator("nav")
    else:
        navigation = page.locator(".tmc-nav")
    href = {
        "sources": "https://github.com/mutating",
        "news": prefix + "/news/",
        "blog": prefix + "/blog/",
        "contact": prefix + "/#contact",
        "platform": "https://platform.mutating.tech/",
    }[destination]
    if destination in {"sources", "platform"}:
        page.route(
            href,
            lambda route: route.fulfill(
                body="<!doctype html><title>Destination</title>",
                content_type="text/html",
            ),
        )
    link = (
        page.locator(".tmc-platform-link")
        if destination == "platform"
        else navigation.locator(f'a[href="{href}"]')
    )
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(
        href if href.startswith("https:") else re.compile(re.escape(href) + "$")
    )


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("activation", ["click", "Enter", " "])
def test_home_brand_activates_once_clears_fragment_and_preserves_query(
    driver: DriverFactory,
    language: str,
    activation: str,
) -> None:
    """Blink once before reloading the localized home with its query intact."""
    prefix = "/ru" if language == "ru" else ""
    page = driver(
        stored_language=language, initial_path=prefix + "/?header-probe=1#feedback"
    )
    page.evaluate("""() => {
      sessionStorage.removeItem('header-activations');
      addEventListener('tmc-brand-home-activate', () => {
        sessionStorage.setItem('header-activations', String(Number(sessionStorage.getItem('header-activations') || 0) + 1));
        sessionStorage.setItem('header-activation-url', location.pathname + location.search + location.hash);
        // Re-enter before the 260ms reload, even on an overloaded runner.
        document.querySelector('.tmc-brand').click();
      });
    }""")
    brand = page.locator(".tmc-brand")
    with page.expect_navigation(wait_until="domcontentloaded"):
        if activation == "click":
            brand.click()
        else:
            brand.press(activation)
    expect(page).to_have_url(re.compile(re.escape(prefix + "/?header-probe=1") + "$"))
    assert page.evaluate("sessionStorage.getItem('header-activations')") == "1"
    assert (
        page.evaluate("sessionStorage.getItem('header-activation-url')")
        == prefix + "/?header-probe=1"
    )


def test_eye_recenters_when_pointer_leaves_window_or_window_loses_focus(
    driver: DriverFactory,
) -> None:
    """Stop gazing at a stale pointer after either browser exit signal."""
    page = driver(viewport=(1440, 900))
    pupil = page.locator(".tmc-eye__pupil")
    for event in ["blur", "pointerout"]:
        page.mouse.move(1438, 898)
        page.wait_for_function("""() => {
          const pupil = document.querySelector('.tmc-eye__pupil');
          return Math.abs(parseFloat(pupil.style.getPropertyValue('--gaze-x'))) > 1;
        }""")
        page.evaluate(
            """event => {
          if (event === 'blur') dispatchEvent(new Event('blur'));
          else document.dispatchEvent(new PointerEvent('pointerout', {relatedTarget: null}));
        }""",
            event,
        )
        page.wait_for_function("""() => {
          const pupil = document.querySelector('.tmc-eye__pupil');
          return Math.abs(parseFloat(pupil.style.getPropertyValue('--gaze-x'))) < 0.1
            && Math.abs(parseFloat(pupil.style.getPropertyValue('--gaze-y'))) < 0.1;
        }""")
        expect(pupil).to_be_visible()


def test_eye_opens_blinks_on_timer_and_theme_and_stays_closed_for_home(
    driver: DriverFactory,
) -> None:
    """Exercise the real lid timers and theme/home event wiring deterministically."""
    page = driver(stored_theme="light")
    epoch = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
    page.clock.install(time=epoch - timedelta(days=1))
    page.clock.pause_at(epoch)
    page.reload(wait_until="domcontentloaded")
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    page.evaluate("() => document.fonts.ready")
    lid = page.locator(".tmc-eye__clip")
    page.clock.run_for(1024)
    assert lid.evaluate("el => el.style.clipPath") == "none"
    page.clock.run_for(29056)
    assert lid.evaluate("el => el.style.clipPath") != "none"
    page.clock.run_for(2000)
    assert lid.evaluate("el => el.style.clipPath") == "none"
    page.locator(".tmc-theme-toggle").evaluate("el => el.click()")
    page.clock.run_for(80)
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert lid.evaluate("el => el.style.clipPath") != "none"
    page.clock.run_for(2000)
    assert lid.evaluate("el => el.style.clipPath") == "none"
    page.evaluate("dispatchEvent(new Event('tmc-brand-home-activate'))")
    page.clock.run_for(2000)
    held = lid.evaluate("el => el.style.clipPath")
    assert held != "none"
    page.clock.run_for(30000)
    assert lid.evaluate("el => el.style.clipPath") == held


@pytest.mark.parametrize("width", [1040, 360, 280])
@pytest.mark.parametrize("language", ["en", "ru"])
def test_touch_menu_language_and_theme_controls(
    driver: DriverFactory,
    width: int,
    language: str,
) -> None:
    """Use real taps without mouse hover on phone and tablet controls."""
    page = driver(
        stored_language=language,
        stored_theme="light",
        viewport=(width, 900),
        has_touch=True,
    )
    menu = page.locator(".tmc-mobile-menu")
    menu.locator(":scope > summary").tap()
    expect(menu).to_have_attribute("open", "")
    switcher = menu.locator(".tmc-language-switcher--mobile")
    switcher.locator("summary").tap()
    expect(switcher.locator("a")).to_be_visible()
    switcher.locator("a").tap()
    other = "ru" if language == "en" else "en"
    expect(page.locator("html")).to_have_attribute("lang", other)
    assert page.evaluate("localStorage.getItem('tmc-language')") == other
    expect(page.locator(".tmc-mobile-menu")).not_to_have_attribute("open", "")
    page.locator(".tmc-theme-toggle").tap()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize("width", [1040, 360])
def test_menu_dismisses_with_escape_and_when_returning_to_desktop(
    driver: DriverFactory, width: int,
) -> None:
    """Dismiss the innermost disclosure and reset the mobile menu on desktop."""
    page = driver(viewport=(width, 900))
    menu = page.locator(".tmc-mobile-menu")
    summary = menu.locator(":scope > summary")
    summary.click()
    summary.press("Escape")
    expect(menu).not_to_have_attribute("open", "")
    expect(summary).to_be_focused()
    summary.click()
    language = menu.locator(".tmc-language-switcher")
    language.locator("summary").click()
    language.locator("summary").press("Escape")
    expect(language).not_to_have_attribute("open", "")
    expect(language.locator("summary")).to_be_focused()
    expect(menu).to_have_attribute("open", "")
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(menu).not_to_have_attribute("open", "")
    page.set_viewport_size({"width": width, "height": 900})
    expect(menu).not_to_have_attribute("open", "")
    expect(menu.locator("nav")).not_to_be_visible()


@pytest.mark.parametrize("width", [1440, 1040, 360])
def test_language_new_tab_preserves_current_document_state(
    driver: DriverFactory, width: int,
) -> None:
    """A real modifier click must preserve the original page's language list."""
    page = driver(viewport=(width, 900))
    mobile = width <= 1120
    menu = page.locator(".tmc-mobile-menu")
    if mobile:
        menu.locator(":scope > summary").click()
    language = page.locator(
        ".tmc-language-switcher--mobile" if mobile
        else ".tmc-language-switcher--desktop"
    )
    language.locator("summary").focus()
    language.locator("summary").press("Enter")
    with page.context.expect_page() as popup:
        language.locator("a").click(modifiers=["ControlOrMeta"])
    expect(popup.value).to_have_url(re.compile(r"/ru/"))
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(language).to_have_attribute("open", "")
    if mobile:
        expect(menu).not_to_have_attribute("open", "")
    popup.value.close()


def test_reload_brand_is_an_action_without_native_link_gestures(
    driver: DriverFactory,
) -> None:
    """Middle-click and dragging must not open tabs or drag a URL."""
    page = driver()
    popups: list[Page] = []
    page.context.on("page", lambda popup: popups.append(popup))
    brand = page.locator(".tmc-brand")
    page.evaluate("""() => {
      window.headerGestures = [];
      document.querySelector('.tmc-brand').addEventListener('dragstart', () => window.headerGestures.push('drag'));
      addEventListener('tmc-brand-home-activate', () => window.headerGestures.push('activate'));
    }""")
    brand.click(button="middle")
    eye = page.locator(".tmc-eye").bounding_box()
    assert eye is not None
    page.mouse.move(eye["x"] + eye["width"] / 2, eye["y"] + eye["height"] / 2)
    page.mouse.down()
    page.mouse.move(400, 300, steps=10)
    page.mouse.up()
    assert popups == []
    assert page.evaluate("window.headerGestures") == []
    expect(brand).to_have_attribute("role", "link")
    expect(brand).to_have_attribute("tabindex", "0")
    assert brand.get_attribute("href") is None


def test_theme_does_not_follow_other_tabs_until_system_change_or_reload(
    driver: DriverFactory,
) -> None:
    """Keep the old per-document theme policy during another tab's toggle."""
    page = driver(stored_theme="light", color_scheme="light")
    other = page.context.new_page()
    other.goto(page.url, wait_until="domcontentloaded")
    other.locator(".tmc-theme-toggle").click()
    expect(other.locator("html")).to_have_attribute("data-theme", "dark")
    # Drain two paint turns so an asynchronous storage event has been delivered.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    other.close()


@pytest.mark.parametrize("storage_available", [True, False])
def test_system_theme_rereads_storage_after_manual_choice(
    driver: DriverFactory, storage_available: bool,
) -> None:
    """Cleared or unavailable storage lets later OS changes control the theme."""
    page = driver(color_scheme="light", storage_available=storage_available)
    page.locator(".tmc-theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    if storage_available:
        page.evaluate("localStorage.removeItem('tmc-theme')")
    page.emulate_media(color_scheme="dark")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")


@pytest.mark.parametrize("change", ["storage", "system"])
def test_delayed_header_script_preserves_the_theme_already_painted_by_head(
    driver: DriverFactory, change: str,
) -> None:
    """Do not reinterpret a preference changed while the deferred bundle loads."""
    page = driver(color_scheme="dark", stored_theme="dark" if change == "storage" else None)
    pending = []
    page.route("**/js/site.*.js", lambda route: pending.append(route))
    page.reload(wait_until="commit")
    page.wait_for_function("document.readyState === 'interactive'")
    assert len(pending) == 1
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    if change == "storage":
        other = page.context.new_page()
        other.goto(page.url, wait_until="domcontentloaded")
        other.evaluate("localStorage.setItem('tmc-theme', 'light')")
        other.close()
    else:
        page.emulate_media(color_scheme="light")
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    pending.pop().continue_()
    page.locator('html[data-theme-ready="true"]').wait_for(state="attached")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize("width", [1440, 1040, 360])
def test_language_escape_stops_before_outer_disclosures(
    driver: DriverFactory, width: int,
) -> None:
    """One Escape dismisses only the language list and restores its focus."""
    page = driver(viewport=(width, 900))
    mobile = width <= 1120
    if mobile:
        page.locator(".tmc-mobile-menu > summary").click()
    language = page.locator(f".tmc-language-switcher--{'mobile' if mobile else 'desktop'}")
    language.locator("summary").press("Enter")
    language.locator("a").focus()
    page.evaluate("""() => {
      window.headerEscapes = [];
      document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        const list = event.target.closest('.tmc-language-switcher');
        window.headerEscapes.push({
          open: list.open,
          summaryFocused: document.activeElement === list.querySelector('summary'),
          defaultPrevented: event.defaultPrevented,
        });
      });
    }""")
    page.keyboard.press("Escape")
    assert page.evaluate("window.headerEscapes") == []
    expect(language).not_to_have_attribute("open", "")
    expect(language.locator("summary")).to_be_focused()
    if mobile:
        expect(page.locator(".tmc-mobile-menu")).to_have_attribute("open", "")


@pytest.mark.parametrize("failure", ["missing", "constructor"])
@pytest.mark.parametrize("width", [1440, 1040, 360])
def test_theme_and_language_controls_survive_brand_initialization_failure(
    driver: DriverFactory, failure: str, width: int,
) -> None:
    """A failed brand observer must not disable controls initialized before it."""
    page = driver(
        viewport=(width, 900), stored_theme="light", color_scheme="light",
        wait_for_theme_ready=False,
        init_scripts=(_observer_failure_script(failure),),
    )
    assert page.evaluate("window.headerInitializationErrors.length") > 0
    page.locator(".tmc-theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"
    mobile = width <= 1120
    if mobile:
        page.locator(".tmc-mobile-menu > summary").click()
    language = page.locator(f".tmc-language-switcher--{'mobile' if mobile else 'desktop'}")
    language.locator("summary").press("Enter")
    expect(language).to_have_attribute("open", "")
    language.locator("summary").press("Escape")
    expect(language).not_to_have_attribute("open", "")
    expect(language.locator("summary")).to_be_focused()


@pytest.mark.parametrize("failure", ["missing", "constructor"])
def test_wordmark_pointer_handlers_survive_unavailable_observer(
    driver: DriverFactory, failure: str,
) -> None:
    """Keep the original wordmark listeners installed before observer creation."""
    page = driver(
        viewport=(1440, 900), wait_for_theme_ready=False,
        init_scripts=(_observer_failure_script(failure),),
    )
    assert page.evaluate("window.headerInitializationErrors.length") > 0
    wordmark = page.locator(".tmc-brand__wordmark")
    bounds = wordmark.bounding_box()
    assert bounds is not None
    for exit_signal in ("pointer", "blur"):
        page.mouse.move(bounds["x"] + 2, bounds["y"] + bounds["height"] / 2)
        expect(wordmark).to_have_attribute("data-hover-expanded", "true")
        if exit_signal == "pointer":
            page.mouse.move(1438, 898)
        else:
            page.evaluate("dispatchEvent(new Event('blur'))")
        expect(wordmark).not_to_have_attribute("data-hover-expanded", "true")


@pytest.mark.parametrize("failure", ["missing", "constructor", "event-signal"])
@pytest.mark.parametrize("width", [1440, 1040, 360])
def test_original_header_controls_do_not_require_abortable_event_listeners(
    driver: DriverFactory, failure: str, width: int,
) -> None:
    """Keep the header's original capabilities without a new AbortSignal dependency."""
    page = driver(
        viewport=(width, 900), stored_theme="light", color_scheme="light",
        wait_for_theme_ready=False, init_scripts=(_listener_failure_script(failure),),
    )
    page.locator(".tmc-theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert page.evaluate("localStorage.getItem('tmc-theme')") == "dark"
    mobile = width <= 1120
    if mobile:
        page.locator(".tmc-mobile-menu > summary").click()
    language = page.locator(f".tmc-language-switcher--{'mobile' if mobile else 'desktop'}")
    language.locator("summary").press("Enter")
    expect(language).to_have_attribute("open", "")
    language.locator("summary").press("Escape")
    expect(language).not_to_have_attribute("open", "")
    expect(language.locator("summary")).to_be_focused()
    expect(page.locator(".tmc-brand__wordmark-stage")).to_have_attribute("data-expansion-measured", "true")


@pytest.mark.parametrize("failure", ["missing", "constructor"])
def test_brand_failure_preserves_earlier_site_media_and_ticker_initialization(
    driver: DriverFactory, failure: str,
) -> None:
    """The brand must fail at the same stage of site initialization as before."""
    page = driver(
        viewport=(1440, 900), stored_theme="dark", wait_for_theme_ready=False,
        init_scripts=(_observer_failure_script(failure),),
    )
    rotor = page.locator(".pillar-card__rotor-image")
    assert rotor.evaluate("el => el.dataset.deferredSrc || el.getAttribute('src')") == rotor.get_attribute("data-theme-src-dark")
    page.locator(".tmc-theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.wait_for_function("""() => {
      const image = document.querySelector('.pillar-card__rotor-image');
      return (image.dataset.deferredSrc || image.getAttribute('src')) === image.dataset.themeSrcLight;
    }""")
    # The rotor continuously rotates, so Playwright's geometry-stability wait
    # cannot settle. Native scrolling still exercises the real lazy loader.
    rotor.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
    expect(rotor).to_have_attribute("src", rotor.get_attribute("data-theme-src-light"))
    page.wait_for_function("""() => {
      const image = document.querySelector('.pillar-card__rotor-image');
      return image.complete && image.naturalWidth > 0;
    }""")
    page.wait_for_function("document.querySelector('.ticker__track').getAnimations().length === 2")


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("width", [1440, 1040, 360])
def test_theme_control_preserves_original_screen_reader_semantics(
    driver: DriverFactory, language: str, width: int,
) -> None:
    """The site's action button must not become a pressed-state toggle button."""
    page = driver(stored_language=language, stored_theme="light", viewport=(width, 900))
    toggle = page.locator(".tmc-theme-toggle")
    for index, theme in enumerate(("light", "dark", "light")):
        expect(page.locator("html")).to_have_attribute("data-theme", theme)
        assert toggle.get_attribute("aria-pressed") is None
        assert "[pressed" not in toggle.aria_snapshot()
        if index < 2:
            toggle.click()


@pytest.mark.parametrize(
    ("language", "prefix", "label"),
    [("en", "", "Products"), ("ru", "/ru", "Продукты")],
)
def test_products_menu_is_a_localized_disclosure(
    driver: DriverFactory, language: str, prefix: str, label: str,
) -> None:
    page = driver(
        viewport=(1440, 900), initial_path=prefix + "/", stored_language=language,
    )
    menu = page.locator(".tmc-nav .tmc-nav-menu")
    expect(menu).to_have_count(1)
    summary = menu.locator(":scope > summary")
    expect(summary).to_contain_text(label)
    summary.click()
    expect(menu).to_have_attribute("open", "")
    link = menu.locator(".tmc-nav-menu__item")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", prefix + "/products/compute/")
    page.keyboard.press("Escape")
    expect(menu).not_to_have_attribute("open", "")


@pytest.mark.parametrize(
    ("language", "prefix", "label"),
    [("en", "", "Products"), ("ru", "/ru", "Продукты")],
)
def test_products_menu_is_available_in_the_mobile_menu(
    driver: DriverFactory, language: str, prefix: str, label: str,
) -> None:
    page = driver(
        viewport=(390, 780), initial_path=prefix + "/", stored_language=language,
    )
    page.locator(".tmc-mobile-menu > summary").click()
    menu = page.locator(".tmc-mobile-menu__links .tmc-nav-menu")
    expect(menu).to_have_count(1)
    label_element = menu.locator(".tmc-nav-menu__label")
    expect(label_element).to_contain_text(label)
    expect(menu.locator(".tmc-nav-menu__chevron")).to_have_count(0)
    sibling_color = page.locator(".tmc-mobile-menu__links > a").first.evaluate(
        "element => getComputedStyle(element).color"
    )
    assert label_element.evaluate("element => getComputedStyle(element).color") == sibling_color
    link = menu.locator(".tmc-nav-menu__item")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", prefix + "/products/compute/")
