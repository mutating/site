"""Visitor-facing contracts for the Mutating Compute landing page."""

from __future__ import annotations

import math

import pytest
from playwright.sync_api import Browser, expect

from conftest import DriverFactory


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_compute_text_and_pool_nodes_do_not_overlap_at_responsive_boundaries(
    driver: DriverFactory, language: str, theme: str,
) -> None:
    page = driver(
        initial_path=f"{'/ru' if language == 'ru' else ''}/products/compute/",
        stored_language=language, stored_theme=theme, reduced_motion="reduce",
    )
    for width in (280, 320, 390, 560, 561, 620, 640, 641, 768, 800, 801,
                  1024, 1100, 1101, 1440, 1920, 2560, 3440):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        problems = page.evaluate("""() => {
          const root = document.querySelector('.compute'), problems = [];
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) {
            const node = walker.currentNode, parent = node.parentElement;
            if (!node.textContent.trim() || !parent.getClientRects().length ||
                getComputedStyle(parent).visibility === 'hidden' ||
                parent.closest('svg,[aria-hidden="true"]') ||
                (parent.closest('details:not([open])') && !parent.closest('summary'))) continue;
            const range = document.createRange();
            range.selectNodeContents(node);
            for (const rect of range.getClientRects()) {
              if (rect.left < -1 || rect.right > innerWidth + 1)
                problems.push(`Text outside viewport: ${node.textContent.trim()}`);
            }
          }
          const nodes = [...root.querySelectorAll('.compute-ci-source,.compute-ci-router,.compute-ci-host')];
          if (nodes.length !== 5) problems.push('The CI diagram is incomplete');
          for (let i = 0; i < nodes.length; i++) {
            for (let j = i + 1; j < nodes.length; j++) {
              const a = nodes[i].getBoundingClientRect(), b = nodes[j].getBoundingClientRect();
              if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
                  Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2)
                problems.push(`Pool nodes overlap: ${i}, ${j}`);
            }
          }
          const wires = root.querySelector('[data-ci-wires]');
          if (getComputedStyle(wires).display === 'none')
            problems.push('The CI diagram has lost its connections');
          const flow = root.querySelector('[data-ci-flow]').getBoundingClientRect();
          const busy = root.querySelector('[data-ci-host][data-busy]').getBoundingClientRect();
          for (const route of wires.querySelectorAll('path:not(.compute-ci-wires__busy)')) {
            const length = route.getTotalLength();
            if (!length) problems.push('An empty CI connection');
            for (let step = 0; step <= 32; step++) {
              const point = route.getPointAtLength(length * step / 32);
              const x = point.x + flow.left, y = point.y + flow.top;
              if (x > busy.left + 2 && x < busy.right - 2 &&
                  y > busy.top + 2 && y < busy.bottom - 2)
                problems.push('A CI connection crosses the busy laptop');
            }
          }
          const heroLabels = [...root.querySelectorAll(
            '.compute-grid-source,.compute-grid-device')];
          for (const route of root.querySelectorAll('[data-hero-route]')) {
            const length = route.getTotalLength();
            const start = route.getPointAtLength(0), end = route.getPointAtLength(length);
            let lastY = start.y;
            for (let step = 1; step <= 16; step++) {
              const point = route.getPointAtLength(length * step / 16);
              if (point.x < Math.min(start.x, end.x) - 1 ||
                  point.x > Math.max(start.x, end.x) + 1 || point.y < lastY - 1)
                problems.push('CI route folds back on itself');
              lastY = point.y;
            }
          }
          for (let i = 0; i < heroLabels.length; i++) {
            const box = heroLabels[i].getBoundingClientRect();
            if (box.left < -1 || box.right > innerWidth + 1)
              problems.push(`Hero diagram outside viewport: ${i}`);
            if (heroLabels[i].matches('.compute-grid-device')) {
              if (box.height > 30) problems.push('Device label obscures too much of the globe');
              for (const pin of root.querySelectorAll('[data-hero-pin]')) {
                if (Number(getComputedStyle(pin).opacity) < .5) continue;
                const point = pin.getBoundingClientRect();
                if (Math.min(box.right, point.right) - Math.max(box.left, point.left) > 2 &&
                    Math.min(box.bottom, point.bottom) - Math.max(box.top, point.top) > 2)
                  problems.push(`Device label covers visible node ${pin.dataset.heroPin}`);
              }
            }
            for (let j = i + 1; j < heroLabels.length; j++) {
              const a = heroLabels[i].getBoundingClientRect(), b = heroLabels[j].getBoundingClientRect();
              if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 &&
                  Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2)
                problems.push(`Hero labels overlap: ${i}, ${j}`);
            }
          }
          return problems;
        }""")
        assert not problems, f"{language}/{theme}/{width}px: {problems}"


def test_compute_globe_labels_follow_nodes_and_dim_on_the_far_side(driver: DriverFactory) -> None:
    page = driver(initial_path="/products/compute/", viewport=(1440, 1000), reduced_motion="reduce")
    page.evaluate("() => document.fonts.ready")
    grip = page.locator('[data-globe-grip]')
    expect(grip).to_be_visible()
    pin = page.locator('[data-hero-pin="A"]')
    card = page.locator('[data-hero-device="A"]')
    initial_pin = pin.bounding_box()
    initial_card = card.bounding_box()
    assert initial_pin and initial_card
    initial_color = card.evaluate("el => getComputedStyle(el).color")
    rear_pins = page.locator('[data-hero-pin][data-hero-rear]')
    expect(rear_pins).to_have_count(2)
    for rear_pin in rear_pins.all():
        expect(rear_pin).to_have_css("opacity", "0.26")

    grip.focus()
    for _ in range(12):
        page.keyboard.press("ArrowRight")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    page.wait_for_function("getComputedStyle(document.querySelector('[data-hero-pin=A]')).opacity < .4")
    far_pin = pin.bounding_box()
    far_card = card.bounding_box()
    assert far_pin and far_card
    assert abs(far_pin["x"] - initial_pin["x"]) > 50
    assert abs(far_card["x"] - initial_card["x"]) > 20
    assert abs((far_card["x"] - initial_card["x"]) - (far_pin["x"] - initial_pin["x"])) < 20
    assert card.evaluate("el => getComputedStyle(el).color") != initial_color
    assert float(card.evaluate("el => getComputedStyle(el).opacity")) < .3
    for rear_pin in rear_pins.all():
        expect(rear_pin).to_have_css("opacity", "1")
    assert card.evaluate("el => { const t = new DOMMatrix(getComputedStyle(el).transform); return t.is2D && t.a === 1 && t.b === 0 && t.c === 0 && t.d === 1; }")
    expect(page.locator('[data-hero-pulse]')).to_have_attribute("visibility", "hidden")


@pytest.mark.parametrize("width", [280, 1440])
def test_compute_globe_labels_do_not_jump_between_sides(driver: DriverFactory, width: int) -> None:
    page = driver(initial_path="/products/compute/", viewport=(width, 1000), reduced_motion="reduce")
    page.evaluate("() => document.fonts.ready")
    grip = page.locator('[data-globe-grip]')
    grip.focus()
    positions = """() => [...document.querySelectorAll('[data-hero-pin]')].map(pin => {
      const card = document.querySelector(`[data-hero-device="${pin.dataset.heroPin}"]`);
      const point = pin.getBoundingClientRect(), label = card.getBoundingClientRect();
      return {
        key: pin.dataset.heroPin,
        pin: [point.x + point.width / 2, point.y + point.height / 2],
        label: [label.x + label.width / 2, label.y + label.height / 2],
        rect: [label.x, label.y, label.width, label.height],
        readable: Number(getComputedStyle(card).opacity) > .65,
      };
    })"""
    previous = page.evaluate(positions)
    for _ in range(24):
        page.keyboard.press("ArrowRight")
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
        current = page.evaluate(positions)
        for before, after in zip(previous, current):
            pin_movement = math.dist(before["pin"], after["pin"])
            label_movement = math.dist(before["label"], after["label"])
            assert label_movement <= pin_movement * 1.25 + .5
        readable = [node for node in current if node["readable"]]
        for index, a in enumerate(readable):
            for b in readable[index + 1:]:
                ax, ay, aw, ah = a["rect"]
                bx, by, bw, bh = b["rect"]
                overlaps = (min(ax + aw, bx + bw) - max(ax, bx) > 4 and
                            min(ay + ah, by + bh) - max(ay, by) > 4)
                assert not overlaps, f"Globe labels overlap: {a['key']}, {b['key']}"
        previous = current


def test_compute_globe_spins_after_drag_and_pulses_only_to_available_devices(driver: DriverFactory) -> None:
    page = driver(initial_path="/products/compute/", viewport=(1440, 1000))
    grip = page.locator('[data-globe-grip]')
    expect(grip).to_be_visible()
    mesh = page.locator('[data-globe-front]')
    box = grip.bounding_box()
    assert box
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx - 65, cy)
    page.mouse.down()
    before = mesh.get_attribute("d")
    page.mouse.move(cx + 70, cy + 35, steps=10)
    expect(mesh).not_to_have_attribute("d", before or "")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    dragged = mesh.get_attribute("d")
    page.mouse.up()
    expect(mesh).not_to_have_attribute("d", dragged or "")
    destinations = page.evaluate("""() => new Promise(resolve => {
      const pulse = document.querySelector('[data-hero-pulse]'), targets = new Set();
      const read = () => {
        if (pulse.dataset.target) targets.add(pulse.dataset.target);
        if (targets.size >= 6) finish();
      };
      const observer = new MutationObserver(read);
      const finish = () => { clearTimeout(timer); observer.disconnect(); resolve([...targets].sort()); };
      const timer = setTimeout(finish, 18000);
      observer.observe(pulse, {attributes: true, attributeFilter: ['data-target']});
      read();
    })""")
    assert destinations == ["A", "B", "D", "E", "F", "G"]
    expect(page.locator('[data-hero-device]')).to_have_count(7)
    expect(page.locator('[data-hero-device="D"]')).to_contain_text("Windows")
    expect(page.locator('[data-hero-device="E"]')).to_contain_text("TV")
    expect(page.locator('[data-hero-job]')).to_have_text(["Tests / 01", "Tests / 02"])
    shared_sources = page.evaluate("""() => {
      const start = key => {
        const point = document.querySelector(`[data-hero-route="${key}"]`).getPointAtLength(0);
        return [point.x, point.y];
      };
      return [start('A'), start('D'), start('F'), start('B'), start('E'), start('G')];
    }""")
    assert shared_sources[0] == shared_sources[1] == shared_sources[2]
    assert shared_sources[3] == shared_sources[4] == shared_sources[5]
    expect(page.locator('[data-hero-route="C"]')).to_have_count(0)


def test_compute_globe_flick_keeps_moving_and_loses_momentum(driver: DriverFactory) -> None:
    page = driver(initial_path="/products/compute/", viewport=(1440, 1000))
    grip = page.locator('[data-globe-grip]')
    expect(grip).to_be_visible()
    box = grip.bounding_box()
    assert box
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx - 70, cy)
    page.mouse.down()
    page.mouse.move(cx + 70, cy + 25, steps=10)
    page.mouse.up()
    distances = page.evaluate("""async () => {
      const pins = [...document.querySelectorAll('[data-hero-pin]')];
      const positions = () => pins.map(pin => {
        const box = pin.getBoundingClientRect();
        return [box.x, box.y];
      });
      const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
      const movement = async () => {
        const before = positions();
        await wait(160);
        const after = positions();
        return Math.sqrt(after.reduce((sum, p, i) =>
          sum + (p[0] - before[i][0]) ** 2 + (p[1] - before[i][1]) ** 2, 0) / pins.length);
      };
      const initial = await movement();
      await wait(4200);
      return [initial, await movement()];
    }""")
    assert distances[0] > 5
    assert distances[0] > distances[1] * 6
    assert distances[1] < 2

    page.emulate_media(reduced_motion="reduce")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    mesh = page.locator('[data-globe-front]').get_attribute("d")
    page.wait_for_timeout(150)
    expect(page.locator('[data-globe-front]')).to_have_attribute("d", mesh or "")


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("language", ["en", "ru"])
def test_compute_organization_quote_fits_the_card_at_compact_widths(
    driver: DriverFactory, language: str, theme: str,
) -> None:
    page = driver(
        initial_path=f"{'/ru' if language == 'ru' else ''}/products/compute/",
        stored_language=language, stored_theme=theme, reduced_motion="reduce",
    )
    page.locator('[data-audience="organization"]').click()
    page.locator('[data-period="yearly"]').click()
    quote = page.locator('.compute-plan__custom')
    for width in (280, 561, 641, 801):
        page.set_viewport_size({"width": width, "height": 900})
        fits = quote.evaluate("""quote => {
          const card = quote.closest('.compute-plan');
          const box = card.getBoundingClientRect(), style = getComputedStyle(card);
          const range = document.createRange();
          range.selectNodeContents(quote);
          return [...range.getClientRects()].every(rect =>
            rect.left >= box.left + parseFloat(style.paddingLeft) - 2 &&
            rect.right <= box.right - parseFloat(style.paddingRight) + 2);
        }""")
        assert fits, f"{language}/{theme}/{width}px: organization quote overflows"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_compute_text_has_readable_contrast_in_both_themes(
    driver: DriverFactory, theme: str,
) -> None:
    page = driver(initial_path="/products/compute/", stored_theme=theme)
    failures = page.evaluate("""() => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d', {willReadFrequently: true});
      const parse = color => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const pixel = context.getImageData(0, 0, 1, 1).data;
        return [pixel[0], pixel[1], pixel[2], pixel[3] / 255];
      };
      const composite = (front, back) => front.slice(0, 3)
        .map((value, i) => value * front[3] + back[i] * (1 - front[3])).concat(1);
      const luminance = color => {
        const rgb = color.slice(0, 3).map(channel => {
          channel /= 255;
          return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
        });
        return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2];
      };
      const failures = [], walker = document.createTreeWalker(
        document.querySelector('.compute'), NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode, parent = node.parentElement;
        const style = getComputedStyle(parent);
        if (!node.textContent.trim() || !parent.getClientRects().length ||
            style.visibility === 'hidden' || parent.closest('svg,[aria-hidden="true"],[aria-disabled="true"]') ||
            (parent.closest('details:not([open])') && !parent.closest('summary'))) continue;
        let element = parent, background = [255, 255, 255, 1];
        const ancestors = [];
        while (element) { ancestors.push(element); element = element.parentElement; }
        for (const ancestor of ancestors.reverse())
          background = composite(parse(getComputedStyle(ancestor).backgroundColor), background);
        const foreground = composite(parse(style.color), background);
        const a = luminance(foreground), b = luminance(background);
        const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
        const large = parseFloat(style.fontSize) >= 24 ||
          (parseFloat(style.fontSize) >= 18.66 && parseFloat(style.fontWeight) >= 700);
        if (ratio < (large ? 3 : 4.5))
          failures.push({text: node.textContent.trim(), ratio});
      }
      return failures;
    }""")
    assert not failures, f"{theme}: {failures}"


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("theme,width", [("light", 1440), ("dark", 390), ("light", 320)])
def test_compute_preserves_the_shell_and_fits_the_screen(
    driver: DriverFactory, language: str, theme: str, width: int,
) -> None:
    path = f"{'/ru' if language == 'ru' else ''}/products/compute/"
    page = driver(
        initial_path=path, stored_language=language,
        stored_theme=theme, viewport=(width, 900),
    )
    expect(page.locator("html")).to_have_attribute("lang", language)
    expect(page.locator(".tmc-header")).to_be_visible()
    expect(page.locator(".site-footer")).to_have_count(1)
    expect(page.locator("h1")).to_contain_text(
        "суперкомпьютер" if language == "ru" else "supercomputer"
    )
    expect(page.locator('[data-demo-controls]')).to_be_visible()
    expect(page.locator('.compute-download__button')).to_have_count(5)
    expect(page.locator('.compute-download__button:enabled')).to_have_count(1)
    expect(page.locator('.compute-download__button:disabled')).to_have_count(4)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    description = page.locator('meta[name="description"]').get_attribute("content")
    assert "Mutating Compute" in description
    assert ("распределённых вычислений" if language == "ru" else "distributed computing") in description
    expect(page.locator('link[rel="alternate"][hreflang="ru-RU"]')).to_have_attribute(
        "href", f"{page.url.split(path)[0]}/ru/products/compute/"
    )


def test_compute_prices_include_two_free_devices_and_use_full_year_totals(
    driver: DriverFactory,
) -> None:
    page = driver(initial_path="/products/compute/")
    personal = page.locator('[data-plans="personal"]')
    organization = page.locator('[data-plans="organization"]')
    expect(page.locator('#pricing-title')).to_contain_text("Device subscriptions.")
    expect(personal.locator('[data-price]')).to_have_text(["$0", "$1", "$4", "$7.50"])
    expect(personal.locator("h3")).to_have_text(["2 free devices", "Additional devices", "7 devices", "12 devices"])
    expect(personal.locator("h4")).to_have_text([
        "2 personal devices", "Each device after your first 2",
        "2 free + 5 paid devices", "2 free + 10 paid devices",
    ])
    expect(organization).to_be_hidden()

    page.get_by_role("button", name="Yearly", exact=True).click()
    expect(personal.locator('[data-price]')).to_have_text(["$0", "$10", "$39", "$75"])
    expect(personal.locator('[data-price-period]')).to_have_text([
        "Always free", "/ year", "/ year", "/ year",
    ])
    expect(personal.locator('[data-annual-hint]')).to_have_text([
        "", "Save $2 per year", "Save $9 per year", "Save $15 per year",
    ])
    expect(page.locator('[data-billing-status]')).to_contain_text("full year")

    page.get_by_role("button", name="Organizations", exact=True).click()
    expect(personal).to_be_hidden()
    expect(page.locator('#pricing-title')).to_contain_text("Organization pricing.")
    expect(page.locator('[data-pricing-copy="description"]')).to_contain_text("Every connected organization device is billed")
    expect(page.locator('[data-pricing-copy="extra"]')).to_contain_text("$1/month or $10/year per organization device")
    expect(organization.locator('[data-price]')).to_have_text("$10")
    expect(organization.locator('[data-annual-hint]')).to_have_text("Save $2 per year")
    expect(organization.get_by_role("link", name="contact@mutating.tech")).to_have_attribute(
        "href", "mailto:contact@mutating.tech"
    )
    page.get_by_role("button", name="Monthly", exact=True).click()
    expect(organization.locator('[data-price]')).to_have_text("$1")
    page.get_by_role("button", name="Personal", exact=True).click()
    expect(page.locator('#pricing-title')).to_contain_text("Device subscriptions.")
    expect(page.locator('[data-pricing-copy="extra"]')).to_contain_text("The 2 free devices do not apply to organizations")
    expect(personal.locator('[data-price]')).to_have_text(["$0", "$1", "$4", "$7.50"])
    expect(personal.locator('[data-annual-hint]')).to_have_text([
        "", "or $10 / year", "or $39 / year", "or $75 / year",
    ])


@pytest.mark.parametrize("language", ["en", "ru"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_compute_audience_switch_keeps_tabs_below_the_sticky_header(
    driver: DriverFactory, language: str, theme: str,
) -> None:
    page = driver(
        initial_path=f"{'/ru' if language == 'ru' else ''}/products/compute/",
        stored_language=language, stored_theme=theme,
        viewport=(320, 844), reduced_motion="reduce",
    )
    page.evaluate("() => document.fonts.ready")
    controls = page.locator('[data-pricing-controls]')
    for audience in ("organization", "personal", "organization", "personal"):
        controls.evaluate("""controls => {
          const tabs = controls.getBoundingClientRect();
          const header = document.querySelector('.tmc-header-shell').getBoundingClientRect();
          window.scrollTo({top: scrollY + tabs.top - header.bottom - 6, behavior: 'instant'});
        }""")
        previous_top = controls.bounding_box()["y"]
        page.locator(f'[data-audience="{audience}"]').click()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        geometry = controls.evaluate("""controls => ({
          top: controls.getBoundingClientRect().top,
          headerBottom: document.querySelector('.tmc-header-shell').getBoundingClientRect().bottom,
        })""")
        assert abs(geometry["top"] - previous_top) <= 1
        assert geometry["top"] >= geometry["headerBottom"]


def test_compute_adapts_the_laptop_budget_to_activity(driver: DriverFactory) -> None:
    page = driver(initial_path="/products/compute/")
    expect(page.locator('[data-power-output]')).to_have_text("20%")
    expect(page.locator('[data-foreground-output]')).to_have_text("80%")
    page.locator('[data-active-limit]').fill("35")
    expect(page.locator('[data-power-output]')).to_have_text("35%")
    page.locator('[data-idle-limit]').fill("65")
    expect(page.locator('[data-power-output]')).to_have_text("35%")
    page.get_by_role("button", name="I’m using this computer", exact=True).click()
    expect(page.locator('[data-power-output]')).to_have_text("65%")
    expect(page.locator('[data-foreground-output]')).to_have_text("35%")
    page.locator('[data-idle-limit]').fill("0")
    expect(page.locator('[data-power-output]')).to_have_text("0%")
    expect(page.locator('[data-foreground-output]')).to_have_text("100%")


def test_compute_cost_example_updates_without_changing_subscription_prices(
    driver: DriverFactory,
) -> None:
    page = driver(initial_path="/products/compute/")
    expect(page.locator('[data-runner-cost]')).to_have_text(["$50", "$60", "$220"])
    page.locator('[data-minutes]').fill("50000")
    expect(page.locator('[data-minutes-output]')).to_have_text("50,000")
    expect(page.locator('[data-runner-cost]')).to_have_text(["$250", "$300", "$1,100"])
    expect(page.locator('[data-plans="personal"] [data-price]')).to_have_text([
        "$0", "$1", "$4", "$7.50",
    ])


def test_compute_ci_example_loops_automatically_and_skips_the_busy_computer(
    driver: DriverFactory,
) -> None:
    page = driver(initial_path="/products/compute/")
    scene = page.locator('[data-ci-scene]')
    expect(scene).not_to_have_attribute("data-stage", "done")
    expect(scene.get_by_role("button")).to_have_count(0)
    page.clock.install(time=0)
    page.clock.pause_at(10_000)
    page.emulate_media(reduced_motion="reduce")
    page.emulate_media(reduced_motion="no-preference")
    expect(scene).to_have_attribute("data-stage", "select")
    for advance, stage in ((1100, "create"), (1300, "run"), (2000, "clean"), (1200, "done")):
        page.clock.run_for(advance)
        expect(scene).to_have_attribute("data-stage", stage)
    expect(page.locator('[data-ci-host][data-busy="true"] .compute-ci-vm')).to_have_count(0)
    expect(page.locator('[data-ci-vm-phase]')).to_have_text(["VM removed", "VM removed"])
    expect(scene.locator('[data-ci-status-stage][aria-hidden="false"]')).to_contain_text(
        "the laptop ran no jobs"
    )
    page.clock.run_for(2200)
    expect(scene).to_have_attribute("data-stage", "select")
    page.clock.run_for(7800)
    expect(scene).to_have_attribute("data-stage", "select")


def test_compute_ci_example_respects_reduced_motion_without_a_pause_control(
    driver: DriverFactory,
) -> None:
    page = driver(initial_path="/products/compute/", reduced_motion="reduce")
    scene = page.locator('[data-ci-scene]')
    expect(scene).to_have_attribute("data-stage", "done")
    expect(page.locator('[data-animation-toggle]')).to_have_count(0)
    expect(scene.get_by_role("button")).to_have_count(0)
    page.clock.install(time=0)
    page.clock.pause_at(10_000)
    page.clock.run_for(20_000)
    expect(scene).to_have_attribute("data-stage", "done")
    page.emulate_media(reduced_motion="no-preference")
    expect(scene).to_have_attribute("data-stage", "select")
    page.clock.run_for(1100)
    expect(scene).to_have_attribute("data-stage", "create")
    page.emulate_media(reduced_motion="reduce")
    expect(scene).to_have_attribute("data-stage", "done")
    page.clock.run_for(20_000)
    expect(scene).to_have_attribute("data-stage", "done")


def test_compute_benchmark_explains_both_measurements(driver: DriverFactory) -> None:
    page = driver(initial_path="/products/compute/")
    expect(page.locator('[data-benchmark-output]')).to_have_text(["1.6 × pi", "4.2 × pi", "7.8 × pi"])
    page.get_by_role("button", name="One thread", exact=True).click()
    expect(page.locator('[data-benchmark-output]')).to_have_text(["1.1 × pi", "1.7 × pi", "2.3 × pi"])
    expect(page.locator('.compute-benchmark figcaption')).to_contain_text("results are illustrative")
    page.get_by_role("button", name="All cores", exact=True).click()
    expect(page.locator('[data-benchmark-output]')).to_have_text(["1.6 × pi", "4.2 × pi", "7.8 × pi"])


def test_compute_only_macos_agent_button_is_enabled_without_starting_downloads(
    driver: DriverFactory,
) -> None:
    page = driver(initial_path="/products/compute/")
    downloads = []
    page.on("download", lambda download: downloads.append(download))
    initial_url = page.url
    macos = page.get_by_role("button", name="Download for macOS", exact=True)
    expect(macos).to_be_enabled()
    macos.click()
    expect(page).to_have_url(initial_url)
    for platform in ("Windows", "Linux", "Android", "Android TV"):
        button = page.get_by_role("button", name=f"Download for {platform}", exact=True)
        expect(button).to_be_disabled()
    assert not downloads
    expect(page.locator('.compute a[href="https://platform.mutating.tech/"]')).to_have_count(1)


def test_compute_content_and_all_prices_work_without_javascript(
    browser: Browser, base_url: str,
) -> None:
    context = browser.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(f"{base_url}/products/compute/")
        expect(page.locator("h1")).to_contain_text("supercomputer")
        expect(page.locator('[data-plans="personal"]')).to_be_visible()
        expect(page.locator('[data-plans="organization"]')).to_be_visible()
        expect(page.locator('[data-plans="personal"] [data-annual-hint]')).to_have_text([
            "", "or $10 / year", "or $39 / year", "or $75 / year",
        ])
        expect(page.locator('[data-pricing-controls]')).to_be_hidden()
        expect(page.locator('.compute-faq__item')).to_have_count(5)
        question = page.locator('.compute-faq__item').filter(has_text="Whose machines run my jobs?")
        expect(question).to_contain_text("Your personal devices")
        expect(question.locator('.compute-faq__answer')).to_be_hidden()
        question.locator('summary').focus()
        page.keyboard.press("Enter")
        expect(question.locator('.compute-faq__answer')).to_be_visible()
        expect(page.locator('.compute-faq__item[open]')).to_have_count(1)
    finally:
        context.close()
