const TAU = Math.PI * 2;
const RADIUS = 126;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const mix = (a, b, t) => a + (b - a) * t;
const pixel = (value) => value.toFixed(2);

const axisAngle = (x, y, z, angle) => {
  const s = Math.sin(angle / 2);
  return [x * s, y * s, z * s, Math.cos(angle / 2)];
};
function multiply([x, y, z, w], [a, b, c, d]) {
  const q = [w*a + x*d + y*c - z*b, w*b - x*c + y*d + z*a,
    w*c + x*b - y*a + z*d, w*d - x*a - y*b - z*c];
  const n = Math.hypot(...q);
  return q.map((v) => v / n);
}
function rotation([x, y, z, w]) {
  const a = 1 - 2*(y*y + z*z), b = 2*(x*y - z*w), c = 2*(x*z + y*w);
  const d = 2*(x*y + z*w), e = 1 - 2*(x*x + z*z), f = 2*(y*z - x*w);
  const g = 2*(x*z - y*w), h = 2*(y*z + x*w), i = 1 - 2*(x*x + y*y);
  return ([u, v, t]) => [a*u + b*v + c*t, d*u + e*v + f*t, g*u + h*v + i*t];
}
function trackballRotation(a, b) {
  const cross = [a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0]];
  const length = Math.hypot(...cross);
  if (length < 1e-7) return [0, 0, 0, 1];
  const dot = a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
  return axisAngle(...cross.map((v) => v / length), Math.atan2(length, dot));
}

// Each curve is sampled on the unit sphere, then split at the visible horizon.
const circle = (point) => Array.from({ length: 73 }, (_, i) => point(i / 72 * TAU));
const mesh = [
  ...Array.from({ length: 6 }, (_, i) => {
    const angle = i * Math.PI / 6;
    return circle((t) => [Math.cos(angle) * Math.sin(t), Math.cos(t), Math.sin(angle) * Math.sin(t)]);
  }),
  ...[-.83, -.5, 0, .5, .83].map((y) => {
    const r = Math.sqrt(1 - y*y);
    return circle((t) => [r * Math.cos(t), y, r * Math.sin(t)]);
  }),
];
const pointText = (p) => `${pixel(150 + p[0] * RADIUS)},${pixel(150 + p[1] * RADIUS)}`;
function projectMesh(rotate) {
  const paths = ['', ''];
  for (const curve of mesh) {
    let last = rotate(curve[0]);
    let side = Number(last[2] >= 0);
    paths[side] += `M${pointText(last)}`;
    for (let i = 1; i < curve.length; i++) {
      const point = rotate(curve[i]);
      const next = Number(point[2] >= 0);
      if (next !== side) {
        const t = last[2] / (last[2] - point[2]);
        const rim = pointText([mix(last[0], point[0], t), mix(last[1], point[1], t)]);
        paths[side] += `L${rim}`;
        paths[next] += `M${rim}`;
        side = next;
      }
      paths[side] += `L${pointText(point)}`;
      last = point;
    }
  }
  return paths;
}

export function initComputeGlobe(root, motion) {
  const grid = root.querySelector('[data-hero-grid]');
  if (!grid) return;
  const planet = grid.querySelector('.compute-hero__planet');
  const grip = planet.querySelector('[data-globe-grip]');
  const front = planet.querySelector('[data-globe-front]');
  const back = planet.querySelector('[data-globe-back]');
  const svg = grid.querySelector('[data-hero-routes]');
  const pulse = svg.querySelector('[data-hero-pulse]');
  const [pulseHalo, pulseTrail] = pulse.querySelectorAll('path');
  const pulseHead = pulse.querySelector('circle');
  const initial = multiply(axisAngle(0, 0, 1, -Math.PI / 10), axisAngle(1, 0, 0, .19));
  const inverseInitial = rotation([-initial[0], -initial[1], -initial[2], initial[3]]);
  const labelOffsets = { A: [-6, 29], B: [12, 29], C: [12, -29], D: [-12, 29], E: [-12, -29], F: [0, -29], G: [8, -29] };
  let orientation = initial;
  const nodes = [...grid.querySelectorAll('[data-hero-pin]')].map((pin) => {
    const key = pin.dataset.heroPin;
    const style = getComputedStyle(pin);
    const size = planet.getBoundingClientRect().width;
    const x = (parseFloat(style.left) / size - .5) * 300 / RADIUS;
    const y = (parseFloat(style.top) / size - .5) * 300 / RADIUS;
    const hemisphere = pin.hasAttribute('data-hero-rear') ? -1 : 1;
    return {
      key, pin, offset: labelOffsets[key] || [0, 29],
      surface: inverseInitial([x, y, hemisphere * Math.sqrt(1 - x*x - y*y)]),
      card: grid.querySelector(`[data-hero-device="${key}"]`),
      leader: svg.querySelector(`[data-hero-leader="${key}"]`),
      route: svg.querySelector(`[data-hero-route="${key}"]`),
      job: grid.querySelector(`[data-hero-job="${pin.dataset.heroJobKey}"]`),
      hit: -Infinity,
    };
  });
  const eligible = nodes.filter((node) => node.route && node.job);
  const source = grid.querySelector('[data-hero-source]');
  let frame, raf = 0, last = 0, time = 0, next = .22, flight, bag = [], drag, visible = true;
  let momentum = [0, 0, 0];
  function measure() {
    const bounds = grid.getBoundingClientRect();
    const relative = (element) => {
      const r = element.getBoundingClientRect();
      return { x: r.left - bounds.left, y: r.top - bounds.top, w: r.width, h: r.height };
    };
    const sourceBounds = relative(source);
    frame = { globe: relative(planet), w: bounds.width, h: bounds.height, top: sourceBounds.y + sourceBounds.h + 7 };
    svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    for (const node of nodes) {
      node.w = node.card.offsetWidth;
      node.h = node.card.offsetHeight;
      if (node.job) {
        const job = relative(node.job);
        node.origin = { x: job.x + job.w / 2, y: job.y + job.h };
      }
    }
  }

  function renderGeometry() {
    if (!frame) return;
    const rotate = rotation(orientation);
    const [rear, near] = projectMesh(rotate);
    back.setAttribute('d', rear);
    front.setAttribute('d', near);
    const { globe } = frame;
    for (const node of nodes) {
      const [x, y, z] = rotate(node.surface);
      const pinX = .5 + x * RADIUS / 300, pinY = .5 + y * RADIUS / 300;
      node.x = globe.x + globe.w * pinX;
      node.y = globe.y + globe.h * pinY;
      const light = clamp((z + .32) / .55, 0, 1);
      const depth = light * light * (3 - 2 * light);
      node.light = mix(.26, 1, depth);
      node.card.style.setProperty('--globe-label-light', depth);
      node.labelOpacity = mix(.18, 1, depth);
      node.card.style.zIndex = String(2 + Math.round((z + 1) * 4));
      node.pin.style.left = `${pinX * 100}%`;
      node.pin.style.top = `${pinY * 100}%`;
      node.pin.style.opacity = node.light;
      node.pin.style.zIndex = String(4 + Math.round((z + 1) * 4));
      node.pin.style.setProperty('--globe-hit', clamp(1 - (time - node.hit) * 2, 0, 1));

      // Each label keeps the same offset from its projected surface point.
      // Continuous clamping at the frame edge never swaps it to another side.
      const label = {
        x: clamp(node.x + x * 8 + node.offset[0] - node.w / 2, 5, frame.w - node.w - 5),
        y: clamp(node.y + node.offset[1] - node.h / 2, frame.top, frame.h - node.h - 5),
        w: node.w, h: node.h,
      };
      node.label = label;
      node.card.style.transform = `translate(${pixel(label.x)}px, ${pixel(label.y)}px)`;
      const lx = clamp(node.x, label.x, label.x + label.w);
      const ly = clamp(node.y, label.y, label.y + label.h);
      const length = Math.hypot(lx - node.x, ly - node.y) || 1;
      const px = node.x + (lx - node.x) / length * 13;
      const py = node.y + (ly - node.y) / length * 13;
      node.leader.setAttribute('d', `M${pixel(px)},${pixel(py)} L${pixel(lx)},${pixel(ly)}`);
      node.leader.style.opacity = depth * .16;
    }
    // Keep labels attached to their points. Fade overlapping names instead of
    // moving them to a different side as the globe turns.
    const placed = [];
    for (const node of nodes) {
      const a = node.label;
      for (const other of placed) {
        const b = other.label;
        const x = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const y = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        const overlap = clamp((x + 4) / 10, 0, 1) * clamp((y + 4) / 10, 0, 1);
        const prominence = clamp((other.labelOpacity - .18) / .82, 0, 1);
        node.labelOpacity *= 1 - .92 * overlap * prominence;
      }
      node.card.style.opacity = node.labelOpacity;
      placed.push(node);
    }
    for (const node of eligible) {
      const { origin } = node;
      const end = node.y - 14;
      const bend = mix(origin.y, end, .48);
      // Both control points stay between source and target, so the curve
      // cannot fold back or switch sides as the device rotates.
      node.route.setAttribute('d',
        `M${pixel(origin.x)},${pixel(origin.y)} C${pixel(origin.x)},${pixel(bend)} ${pixel(node.x)},${pixel(bend)} ${pixel(node.x)},${pixel(end)}`);
      node.route.style.opacity = node.light * (flight?.node === node ? .7 : .2);
    }
  }

  function renderPulse() {
    if (motion.matches || !flight) {
      pulse.setAttribute('visibility', 'hidden');
      delete pulse.dataset.target;
      return;
    }
    const { node, start, duration } = flight;
    const progress = clamp((time - start) / duration, 0, 1);
    const length = node.route.getTotalLength();
    const distance = length * progress;
    const tail = Math.max(0, distance - 38);
    let trail = '';
    for (let i = 0; i <= 10; i++) {
      const p = node.route.getPointAtLength(mix(tail, distance, i / 10));
      trail += `${i ? 'L' : 'M'}${pixel(p.x)},${pixel(p.y)}`;
    }
    const head = node.route.getPointAtLength(distance);
    pulseHalo.setAttribute('d', trail);
    pulseTrail.setAttribute('d', trail);
    pulseHead.setAttribute('cx', pixel(head.x));
    pulseHead.setAttribute('cy', pixel(head.y));
    pulse.dataset.target = node.key;
    pulse.dataset.job = node.job.dataset.heroJob;
    pulse.setAttribute('visibility', 'visible');
    const clearance = Math.min(...nodes.filter((other) => other !== node)
      .map((other) => Math.hypot(head.x - other.x, head.y - other.y)));
    pulse.style.opacity = mix(1, node.light, clamp((progress - .75) * 4, 0, 1)) * clamp((clearance - 14) / 14, 0, 1);
  }

  function tick(now) {
    raf = 0;
    const dt = last ? Math.min((now - last) / 1000, .05) : 0;
    last = now;
    if (!motion.matches && visible && !document.hidden) {
      time += dt;
      if (!drag) {
        const speed = Math.hypot(...momentum);
        const decay = Math.exp(-1.7 * dt);
        if (speed > .002) {
          orientation = multiply(axisAngle(...momentum.map((v) => v / speed), speed * (1 - decay) / 1.7), orientation);
          momentum = momentum.map((v) => v * decay);
        } else momentum = [0, 0, 0];
        orientation = multiply(orientation, axisAngle(0, 1, 0, dt * TAU / 220));
      }
      if (flight && time >= flight.start + flight.duration) {
        flight.node.hit = time;
        flight = undefined;
        next = time + .14 + Math.random() * .24;
      }
      if (!flight && time >= next && eligible.length) {
        if (!bag.length) {
          bag = [...eligible];
          for (let i = bag.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [bag[i], bag[j]] = [bag[j], bag[i]];
          }
        }
        flight = { node: bag.pop(), start: time, duration: 1.25 + Math.random() * .45 };
      }
    }
    renderGeometry();
    renderPulse();
    if (!motion.matches && visible && !document.hidden) schedule();
    else last = 0;
  }
  function schedule() {
    if (!raf) raf = requestAnimationFrame(tick);
  }
  function pointerVector(event, rect) {
    const x = (event.clientX - rect.left - rect.width / 2) / (rect.width / 2);
    const y = (event.clientY - rect.top - rect.height / 2) / (rect.height / 2);
    const r2 = x*x + y*y;
    // Join a sphere to a hyperbolic sheet with a continuous tangent.
    // This avoids a sudden acceleration when the cursor crosses its rim.
    const z = r2 <= .5 ? Math.sqrt(1 - r2) : .5 / Math.sqrt(r2);
    const length = Math.hypot(x, y, z);
    return [x / length, y / length, z / length];
  }
  grip.addEventListener('pointerdown', (event) => {
    if (drag || event.button !== 0) return;
    momentum = [0, 0, 0];
    const rect = grip.getBoundingClientRect();
    drag = { id: event.pointerId, rect, point: pointerVector(event, rect),
      x: event.clientX, y: event.clientY, time: event.timeStamp, active: event.pointerType !== 'touch' };
    grip.setPointerCapture(event.pointerId);
    if (drag.active) grip.dataset.dragging = '';
  });
  grip.addEventListener('pointermove', (event) => {
    if (drag?.id !== event.pointerId) return;
    if (!drag.active) {
      const dx = Math.abs(event.clientX - drag.x), dy = Math.abs(event.clientY - drag.y);
      if (dx < 8 || dx < dy) return;
      drag.active = true;
      grip.dataset.dragging = '';
    }
    const point = pointerVector(event, drag.rect);
    const turn = trackballRotation(drag.point, point);
    orientation = multiply(turn, orientation);
    const length = Math.hypot(...turn.slice(0, 3));
    const elapsed = Math.max((event.timeStamp - drag.time) / 1000, .008);
    const speed = Math.min(2 * Math.atan2(length, turn[3]) / elapsed, 4.5);
    const blend = 1 - Math.exp(-30 * elapsed);
    momentum = momentum.map((v, i) => mix(v, length ? turn[i] / length * speed : 0, blend));
    drag.point = point;
    drag.time = event.timeStamp;
    schedule();
  });
  function endDrag(event) {
    if (drag?.id !== event.pointerId) return;
    const decay = event.type === 'pointerup' && drag.active && !motion.matches
      ? Math.exp(-8 * Math.max(0, event.timeStamp - drag.time) / 1000) : 0;
    momentum = momentum.map((v) => v * decay);
    drag = undefined;
    delete grip.dataset.dragging;
    schedule();
  }
  grip.addEventListener('pointerup', endDrag);
  grip.addEventListener('pointercancel', endDrag);
  grip.addEventListener('lostpointercapture', endDrag);
  grip.addEventListener('keydown', (event) => {
    const axes = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [1, 0], ArrowDown: [-1, 0] };
    if (!axes[event.key]) return;
    event.preventDefault();
    momentum = [0, 0, 0];
    orientation = multiply(axisAngle(...axes[event.key], 0, Math.PI / 12), orientation);
    schedule();
  });

  grid.dataset.globeReady = '';
  planet.querySelector('[data-globe-fallback]').setAttribute('display', 'none');
  grip.hidden = false;
  grip.tabIndex = 0;
  grip.setAttribute('role', 'group');
  function relayout() {
    measure();
    renderGeometry();
    renderPulse();
    schedule();
  }
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(relayout);
    for (const element of [grid, source, ...nodes.map((node) => node.card)]) observer.observe(element);
  } else window.addEventListener('resize', relayout);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      last = 0;
      if (visible) schedule();
    }).observe(planet);
  }
  document.addEventListener('visibilitychange', () => { last = 0; if (!document.hidden) schedule(); });
  motion.addEventListener('change', () => { momentum = [0, 0, 0]; schedule(); });
  document.fonts?.ready.then(relayout);
  relayout();
}
