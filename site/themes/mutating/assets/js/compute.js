import { initComputeGlobe } from "./compute-globe.js";

(() => {
  const root = document.querySelector('[data-compute]');
  if (!root) return;
  const locale = document.documentElement.lang === 'ru' ? 'ru-RU' : 'en-US';
  const number = new Intl.NumberFormat(locale);
  const decimal = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
  const dollars = new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 2,
  });
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)');

  // Measure the current header before the browser scrolls to a section.
  // Its height can change when a compact viewport wraps the header controls.
  root.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', () => {
      const header = document.querySelector('.tmc-header-shell');
      if (!header) return;
      const style = getComputedStyle(header);
      const top = Number.parseFloat(style.top) || 0;
      const clearance = style.position === 'sticky'
        ? Math.ceil(header.getBoundingClientRect().height + top + 8) : 0;
      document.documentElement.style.setProperty('--tmc-header-clearance', `${clearance}px`);
    });
  });

  // The CI lifecycle repeats while the page is visible.
  const scene = root.querySelector('[data-ci-scene]');
  const stages = ['select', 'create', 'run', 'clean', 'done'];
  const durations = [1100, 1300, 2000, 1200, 2200];
  let sequenceTimer;
  function showStage(stage) {
    scene.dataset.stage = stage;
    scene.querySelectorAll('[data-ci-status-stage]').forEach((status) => {
      status.setAttribute('aria-hidden', String(status.dataset.ciStatusStage !== stage));
    });
    const index = stages.indexOf(stage);
    scene.querySelectorAll('[data-ci-phase]').forEach((phase, i) => {
      phase.dataset.current = String(i === index);
      phase.dataset.complete = String(i < index);
    });
    const vmState = {
      select: scene.dataset.vmSelect,
      create: scene.dataset.vmCreate, run: scene.dataset.vmRun,
      clean: scene.dataset.vmClean, done: scene.dataset.vmDone,
    }[stage];
    scene.querySelectorAll('[data-ci-vm-phase]').forEach((label) => {
      label.textContent = vmState;
    });
  }
  function runSequence(index) {
    showStage(stages[index]);
    sequenceTimer = setTimeout(() => runSequence((index + 1) % stages.length), durations[index]);
  }
  function updateSequence() {
    clearTimeout(sequenceTimer);
    if (motion.matches) {
      showStage('done');
    } else if (!document.hidden) {
      runSequence(0);
    }
  }
  motion.addEventListener('change', updateSequence);
  document.addEventListener('visibilitychange', updateSequence);
  updateSequence();

  // Route paths follow the actual layout; mobile has an explicit vertical flow.
  const flow = scene.querySelector('[data-ci-flow]');
  const wires = flow.querySelector('[data-ci-wires]');
  function drawRoutes() {
    if (!wires.getClientRects().length) return;
    const frame = flow.getBoundingClientRect();
    const source = flow.querySelector('[data-ci-source]').getBoundingClientRect();
    const router = flow.querySelector('.compute-ci-router__mark').getBoundingClientRect();
    const routerGroup = flow.querySelector('[data-ci-router]').getBoundingClientRect();
    const hosts = [...flow.querySelectorAll('[data-ci-host]')].map((host) => host.getBoundingClientRect());
    wires.setAttribute('viewBox', `0 0 ${frame.width} ${frame.height}`);
    const curve = (x1, y1, x2, y2) => {
      const bend = (x1 + x2) / 2;
      return `M${x1},${y1} C${bend},${y1} ${bend},${y2} ${x2},${y2}`;
    };
    const input = flow.querySelector('[data-ci-input]');
    const paths = flow.querySelectorAll('[data-ci-route]');
    if (source.bottom < router.top) {
      const cx = router.left + router.width / 2 - frame.left;
      input.setAttribute('d', `M${cx},${source.bottom - frame.top} L${cx},${router.top - frame.top}`);
      const start = routerGroup.bottom - frame.top;
      const top = hosts[0].top - frame.top;
      const bend = (start + top) / 2;
      const row = Math.abs(hosts[0].top - hosts[1].top) < 1;
      // Stacked cards share a lane beside them, leaving the busy laptop untouched.
      const lane = hosts[0].left - frame.left - 10;
      hosts.forEach((host, i) => {
        const x = host.left - frame.left;
        const y = host.top + host.height / 2 - frame.top;
        paths[i].setAttribute('d', row
          ? `M${cx},${start} C${cx},${bend} ${x + host.width / 2},${bend} ${x + host.width / 2},${top}`
          : `M${cx},${start} C${cx},${bend} ${lane},${bend} ${lane},${top} L${lane},${y - 8} Q${lane},${y} ${x},${y}`);
      });
      return;
    }
    const ry = router.top + router.height / 2 - frame.top;
    input.setAttribute('d', curve(
      source.right - frame.left, source.top + source.height / 2 - frame.top,
      router.left - frame.left, ry,
    ));
    hosts.forEach((bounds, i) => {
      paths[i].setAttribute('d', curve(
        router.right - frame.left, ry, bounds.left - frame.left,
        bounds.top + bounds.height / 2 - frame.top,
      ));
    });
  }
  if ('ResizeObserver' in window) new ResizeObserver(drawRoutes).observe(flow);
  else window.addEventListener('resize', drawRoutes);
  document.fonts?.ready.then(drawRoutes);
  drawRoutes();

  initComputeGlobe(root, motion);

  root.querySelectorAll('.compute-faq__summary').forEach((summary) => {
    summary.addEventListener('click', () => {
      const previousTop = summary.getBoundingClientRect().top;
      // Native accordion toggling can close a long answer above this row.
      requestAnimationFrame(() => {
        const offset = summary.getBoundingClientRect().top - previousTop;
        if (previousTop >= 0 && previousTop < window.innerHeight && Math.abs(offset) > 1) {
          window.scrollBy({ top: offset, behavior: 'instant' });
        }
      });
    });
  });

  function updateRangeProgress(input) {
    const min = Number(input.min);
    const fraction = (Number(input.value) - min) / (Number(input.max) - min);
    input.style.setProperty('--range-progress', `${fraction * 100}%`);
  }

  const resource = root.querySelector('[data-resource-demo]');
  const policy = resource.querySelector('[data-policy]');
  const idle = resource.querySelector('[data-idle-limit]');
  const active = resource.querySelector('[data-active-limit]');
  const activity = resource.querySelector('[data-activity-toggle]');
  const state = resource.querySelector('[data-resource-state]');
  let isActive = true;
  function updateBudget() {
    updateRangeProgress(idle);
    updateRangeProgress(active);
    const limit = Number(isActive ? active.value : idle.value);
    policy.style.setProperty('--background-limit', `${limit}%`);
    policy.dataset.active = String(isActive);
    resource.querySelector('[data-idle-output]').value = `${idle.value}%`;
    resource.querySelector('[data-active-output]').value = `${active.value}%`;
    resource.querySelector('[data-power-output]').value = `${limit}%`;
    resource.querySelector('[data-foreground-output]').value = `${100 - limit}%`;
    state.textContent = isActive ? state.dataset.active : state.dataset.idle;
    activity.setAttribute('aria-pressed', String(isActive));
  }
  activity.addEventListener('click', () => { isActive = !isActive; updateBudget(); });
  idle.addEventListener('input', updateBudget);
  active.addEventListener('input', updateBudget);
  resource.querySelector('[data-demo-controls]').hidden = false;
  updateBudget();

  const benchmark = root.querySelector('[data-benchmark]');
  benchmark.querySelectorAll('[data-benchmark-mode]').forEach((button) => {
    button.addEventListener('click', () => {
      const mode = button.dataset.benchmarkMode;
      benchmark.querySelectorAll('[data-benchmark-mode]').forEach((choice) => {
        choice.setAttribute('aria-pressed', String(choice === button));
      });
      benchmark.querySelectorAll('[data-benchmark-row]').forEach((row) => {
        const value = Number(row.dataset[mode]);
        row.querySelector('[data-benchmark-output]').value = `${decimal.format(value)} × pi`;
        row.querySelector('[data-benchmark-bar]').style.width = `${value * 12.5}%`;
      });
    });
  });
  benchmark.querySelector('[data-benchmark-controls]').hidden = false;
  benchmark.querySelector('[data-benchmark-mode="multi"]').click();

  const minutes = root.querySelector('[data-minutes]');
  function updateCosts() {
    updateRangeProgress(minutes);
    const value = Number(minutes.value);
    root.querySelector('[data-minutes-output]').value = number.format(value);
    root.querySelectorAll('[data-runner-cost]').forEach((output) => {
      output.textContent = dollars.format(value * Number(output.dataset.rate)).replace(/\.00$/, '');
    });
  }
  minutes.disabled = false;
  minutes.addEventListener('input', updateCosts);
  updateCosts();

  const pricing = root.querySelector('[data-pricing]');
  const annualHints = [...pricing.querySelectorAll('[data-annual-hint]')].map((element) => {
    const price = element.previousElementSibling.querySelector('[data-price]');
    const savings = Number(price.dataset.monthly) * 12 - Number(price.dataset.yearly);
    return {
      element,
      monthly: element.textContent,
      yearly: savings > 0
        ? pricing.dataset.yearlySavings.replace('%s', dollars.format(savings).replace(/\.00$/, ''))
        : '',
    };
  });
  const copyElements = [...pricing.querySelectorAll('[data-pricing-copy]')];
  const audienceCopy = {
    personal: Object.fromEntries(copyElements.map((element) => [
      element.dataset.pricingCopy, element.textContent,
    ])),
    organization: JSON.parse(pricing.dataset.organizationCopy),
  };
  let audience = 'personal';
  let period = 'monthly';
  function updatePricing() {
    copyElements.forEach((element) => {
      element.textContent = audienceCopy[audience][element.dataset.pricingCopy];
    });
    pricing.querySelectorAll('[data-audience]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.audience === audience));
    });
    pricing.querySelectorAll('[data-period]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.period === period));
    });
    pricing.querySelectorAll('[data-plans]').forEach((plans) => {
      plans.hidden = plans.dataset.plans !== audience;
    });
    pricing.querySelectorAll('[data-price]').forEach((price) => {
      price.textContent = `$${price.dataset[period]}`;
      if (price.dataset.monthly !== '0') {
        price.nextElementSibling.textContent = period === 'yearly' ? pricing.dataset.year : pricing.dataset.month;
      }
    });
    annualHints.forEach(({ element, monthly, yearly }) => {
      element.textContent = period === 'yearly' ? yearly : monthly;
    });
    pricing.querySelector('[data-billing-status]').textContent = period === 'yearly'
      ? pricing.dataset.yearlyStatus : pricing.dataset.monthlyStatus;
  }
  pricing.querySelectorAll('[data-audience]').forEach((button) => {
    button.addEventListener('click', () => {
      if (audience === button.dataset.audience) return;
      const controls = pricing.querySelector('[data-pricing-controls]');
      const previousTop = controls.getBoundingClientRect().top;
      const keepPosition = previousTop >= 0 && previousTop < window.innerHeight;
      audience = button.dataset.audience;
      updatePricing();
      // A different heading can wrap onto more lines. Keep the visible tabs
      // under the pointer instead of letting them move behind the sticky header.
      if (keepPosition) {
        const offset = controls.getBoundingClientRect().top - previousTop;
        if (Math.abs(offset) > 1) window.scrollBy({ top: offset, behavior: 'instant' });
      }
    });
  });
  pricing.querySelectorAll('[data-period]').forEach((button) => {
    button.addEventListener('click', () => { period = button.dataset.period; updatePricing(); });
  });
  pricing.querySelector('[data-pricing-controls]').hidden = false;
  updatePricing();
})();
