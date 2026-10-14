#!/usr/bin/env python3
"""Find cursor coordinates that make the animated wordmark oscillate.

The scanner uses real Playwright pointer input.  For every candidate point it
folds the logo, enters through the compact ``T`` anchor, moves to the candidate,
and records the reveal widths on animation frames while the pointer is still.
An ordinary unfold, fold, or single reversal is accepted.  Repeated direction
changes are reported as jitter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import Browser, Page, async_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DELAYS_MS = (0.0, 175.0, 400.0)
FINISH_WORDMARK_ANIMATIONS = """
() => {
  const wordmark = document.querySelector(".brand__wordmark");
  for (const animation of wordmark.getAnimations({subtree: true})) {
    animation.finish();
  }
  wordmark.getBoundingClientRect();
}
"""
SAMPLE_WORDMARK = """
([durationMs]) => new Promise((resolve) => {
  const wordmark = document.querySelector(".brand__wordmark");
  const reveals = [...wordmark.querySelectorAll(".brand__word-reveal")];
  const samples = [];
  let startedAt = null;

  const sample = (timestamp) => {
    if (startedAt === null) startedAt = timestamp;
    const widths = reveals.map(
      (reveal) => reveal.getBoundingClientRect().width,
    );
    samples.push({
      timeMs: timestamp - startedAt,
      widths,
      totalWidth: widths.reduce((total, width) => total + width, 0),
      retained: wordmark.dataset.hoverExpanded === "true",
      hovered: wordmark.matches(":hover"),
    });
    if (timestamp - startedAt >= durationMs) {
      resolve(samples);
      return;
    }
    requestAnimationFrame(sample);
  };

  requestAnimationFrame(sample);
})
"""


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class MotionSummary:
    minimum_width: float
    maximum_width: float
    final_width: float
    direction_reversals: int
    retained_state_toggles: int
    hover_state_toggles: int


def parse_viewport(value: str) -> Viewport:
    """Parse WIDTHxHEIGHT into a positive viewport."""

    try:
        width_text, height_text = value.lower().split("x", maxsplit=1)
        viewport = Viewport(int(width_text), int(height_text))
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "viewport must look like 1280x720"
        ) from error
    if viewport.width <= 0 or viewport.height <= 0:
        raise argparse.ArgumentTypeError("viewport dimensions must be positive")
    return viewport


def _state_toggles(values: Sequence[bool]) -> int:
    return sum(current != previous for previous, current in zip(values, values[1:]))


def count_direction_reversals(
    values: Sequence[float],
    *,
    minimum_run_pixels: float,
) -> int:
    """Count meaningful reversals while ignoring subpixel rendering noise."""

    if len(values) < 2:
        return 0

    origin = float(values[0])
    extreme = origin
    direction = 0
    reversals = 0
    for raw_value in values[1:]:
        value = float(raw_value)
        if direction == 0:
            if value - origin >= minimum_run_pixels:
                direction = 1
                extreme = value
            elif origin - value >= minimum_run_pixels:
                direction = -1
                extreme = value
            continue

        if direction > 0:
            if value > extreme:
                extreme = value
            elif extreme - value >= minimum_run_pixels:
                reversals += 1
                direction = -1
                extreme = value
        else:
            if value < extreme:
                extreme = value
            elif value - extreme >= minimum_run_pixels:
                reversals += 1
                direction = 1
                extreme = value
    return reversals


def summarize_motion(
    samples: Sequence[dict[str, Any]],
    *,
    minimum_run_pixels: float,
) -> MotionSummary:
    """Reduce frame samples to the signals used by the jitter classifier."""

    widths = [float(sample["totalWidth"]) for sample in samples]
    retained = [bool(sample["retained"]) for sample in samples]
    hovered = [bool(sample["hovered"]) for sample in samples]
    return MotionSummary(
        minimum_width=min(widths),
        maximum_width=max(widths),
        final_width=widths[-1],
        direction_reversals=count_direction_reversals(
            widths,
            minimum_run_pixels=minimum_run_pixels,
        ),
        retained_state_toggles=_state_toggles(retained),
        hover_state_toggles=_state_toggles(hovered),
    )


def is_jitter(
    summary: MotionSummary,
    *,
    minimum_reversals: int,
    minimum_amplitude_pixels: float,
) -> bool:
    """Return whether motion repeatedly reverses with visible amplitude."""

    amplitude = summary.maximum_width - summary.minimum_width
    return amplitude >= minimum_amplitude_pixels and (
        summary.direction_reversals >= minimum_reversals
        or summary.retained_state_toggles >= minimum_reversals
    )


def _axis_values(start: float, end: float, step: float) -> list[float]:
    count = max(0, math.floor((end - start) / step))
    values = [start + index * step for index in range(count + 1)]
    if not values or end - values[-1] > step * 0.01:
        values.append(end)
    return values


def build_candidate_points(
    stage: dict[str, float],
    *,
    step: float,
    margin: float,
    exhaustive: bool,
) -> list[Point]:
    """Cover the stable stage, prioritizing lines where edge races occur."""

    left = stage["x"] - margin
    right = stage["x"] + stage["width"] + margin
    top = stage["y"] - margin
    bottom = stage["y"] + stage["height"] + margin
    xs = _axis_values(left, right, step)
    ys = _axis_values(top, bottom, step)

    if exhaustive:
        raw_points = (Point(x, y) for y in ys for x in xs)
    else:
        horizontal_ys = (
            stage["y"] + stage["height"] * 0.5,
            stage["y"] + 0.25,
            stage["y"] + stage["height"] - 0.25,
        )
        vertical_xs = tuple(
            stage["x"] + stage["width"] * fraction
            for fraction in (0.001, 0.25, 0.5, 0.75, 0.999)
        )
        raw_points = (
            [Point(x, y) for y in horizontal_ys for x in xs]
            + [Point(x, y) for x in vertical_xs for y in ys]
        )

    unique: dict[tuple[float, float], Point] = {}
    for point in raw_points:
        key = (round(point.x, 4), round(point.y, 4))
        unique[key] = point

    center = Point(
        stage["x"] + stage["width"] * 0.5,
        stage["y"] + stage["height"] * 0.5,
    )
    return sorted(
        unique.values(),
        key=lambda point: (
            min(
                abs(point.x - stage["x"]),
                abs(point.x - (stage["x"] + stage["width"])),
                abs(point.y - stage["y"]),
                abs(point.y - (stage["y"] + stage["height"])),
            ),
            abs(point.y - center.y),
            abs(point.x - center.x),
        ),
    )


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        return int(port_socket.getsockname()[1])


def _wait_for_url(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Hugo exited before the diagnostic site started")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.05)
    raise RuntimeError(f"Hugo did not answer at {url} within 60 seconds")


@contextmanager
def diagnostic_site(configured_url: str | None) -> Iterator[str]:
    """Use a supplied site or run an isolated local Hugo server."""

    if configured_url:
        yield configured_url.rstrip("/")
        return

    port = _available_port()
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            "hugo",
            "server",
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--baseURL",
            f"{url}/",
            "--disableFastRender",
            "--disableLiveReload",
            "--noBuildLock",
            "--renderToMemory",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    try:
        _wait_for_url(url, process)
        yield url
    finally:
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


async def prepare_page(
    browser: Browser,
    *,
    url: str,
    viewport: Viewport,
    device_scale_factor: float,
) -> tuple[Any, Page]:
    context = await browser.new_context(
        viewport={"width": viewport.width, "height": viewport.height},
        device_scale_factor=device_scale_factor,
        reduced_motion="no-preference",
    )
    await context.route(
        "**/muta-tentacles.html*",
        lambda route: route.abort(),
    )
    await context.route(
        "**/tokamak-reactor.html*",
        lambda route: route.abort(),
    )
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_function(
        "document.documentElement.dataset.wordmarkFontReady !== 'false'"
    )
    await page.locator(".brand__wordmark-stage").wait_for(state="visible")
    return context, page


async def read_geometry(page: Page) -> dict[str, Any]:
    """Read stable stage and compact activation coordinates."""

    await page.mouse.move(
        (await page.evaluate("innerWidth")) - 1,
        (await page.evaluate("innerHeight")) - 1,
    )
    await page.evaluate(FINISH_WORDMARK_ANIMATIONS)
    geometry = await page.evaluate(
        """
        () => {
          const rect = (element) => {
            const box = element.getBoundingClientRect();
            return {
              x: box.x,
              y: box.y,
              width: box.width,
              height: box.height,
            };
          };
          const stage = document.querySelector(".brand__wordmark-stage");
          const anchor = document.querySelector(
            ".brand__word-part--t .brand__word-anchor",
          );
          return {
            stage: rect(stage),
            anchor: rect(anchor),
            canExpand: stage.dataset.canExpand,
          };
        }
        """
    )
    if geometry["canExpand"] != "true":
        raise RuntimeError(
            "wordmark expansion is disabled at this viewport; choose a wider one"
        )
    return geometry


async def scan_point(
    page: Page,
    *,
    point: Point,
    anchor: dict[str, float],
    viewport: Viewport,
    activation_delay_ms: float,
    sample_ms: float,
    minimum_run_pixels: float,
) -> tuple[MotionSummary, list[dict[str, Any]]]:
    """Run one real-pointer attempt and return its complete frame trace."""

    await page.mouse.move(viewport.width - 1, viewport.height - 1)
    await page.evaluate(FINISH_WORDMARK_ANIMATIONS)
    await page.mouse.move(
        anchor["x"] + anchor["width"] * 0.5,
        anchor["y"] + anchor["height"] * 0.5,
    )
    if activation_delay_ms:
        await page.wait_for_timeout(activation_delay_ms)
    await page.mouse.move(point.x, point.y)
    samples = await page.evaluate(SAMPLE_WORDMARK, [sample_ms])
    return (
        summarize_motion(
            samples,
            minimum_run_pixels=minimum_run_pixels,
        ),
        samples,
    )


async def scan_viewport(
    browser: Browser,
    *,
    url: str,
    viewport: Viewport,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Scan one viewport with independent pages sharing a work queue."""

    probe_context, probe_page = await prepare_page(
        browser,
        url=url,
        viewport=viewport,
        device_scale_factor=args.device_scale_factor,
    )
    try:
        geometry = await read_geometry(probe_page)
    finally:
        await probe_context.close()

    points = build_candidate_points(
        geometry["stage"],
        step=args.step,
        margin=args.margin,
        exhaustive=args.exhaustive,
    )
    attempts = [
        (point, delay)
        for delay in args.activation_delay_ms
        for point in points
    ]
    queue: asyncio.Queue[tuple[Point, float]] = asyncio.Queue()
    for attempt in attempts:
        queue.put_nowait(attempt)

    findings: list[dict[str, Any]] = []
    scanned = 0
    result_lock = asyncio.Lock()
    stop = asyncio.Event()
    worker_count = min(args.workers, len(attempts))
    print(
        f"Scanning {viewport.width}x{viewport.height}: "
        f"{len(points)} points x {len(args.activation_delay_ms)} timings "
        f"with {worker_count} workers",
        file=sys.stderr,
    )

    async def worker() -> None:
        nonlocal scanned
        context, page = await prepare_page(
            browser,
            url=url,
            viewport=viewport,
            device_scale_factor=args.device_scale_factor,
        )
        try:
            while not queue.empty() and not stop.is_set():
                try:
                    point, delay = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                summary, samples = await scan_point(
                    page,
                    point=point,
                    anchor=geometry["anchor"],
                    viewport=viewport,
                    activation_delay_ms=delay,
                    sample_ms=args.sample_ms,
                    minimum_run_pixels=args.minimum_run_pixels,
                )
                finding = is_jitter(
                    summary,
                    minimum_reversals=args.minimum_reversals,
                    minimum_amplitude_pixels=args.minimum_amplitude_pixels,
                )
                async with result_lock:
                    scanned += 1
                    if scanned % args.progress_every == 0:
                        print(
                            f"  {scanned}/{len(attempts)} attempts",
                            file=sys.stderr,
                        )
                    if finding and len(findings) < args.max_findings:
                        entry = {
                            "viewport": asdict(viewport),
                            "point": asdict(point),
                            "stageRelativePoint": {
                                "x": point.x - geometry["stage"]["x"],
                                "y": point.y - geometry["stage"]["y"],
                            },
                            "activationDelayMs": delay,
                            "summary": asdict(summary),
                            "samples": samples,
                        }
                        findings.append(entry)
                        print(
                            "  JITTER at "
                            f"({point.x:.2f}, {point.y:.2f}), "
                            f"delay={delay:g} ms, "
                            f"reversals={summary.direction_reversals}",
                            file=sys.stderr,
                        )
                        if len(findings) >= args.max_findings:
                            stop.set()
                queue.task_done()
        finally:
            await context.close()

    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return {
        "viewport": asdict(viewport),
        "stage": geometry["stage"],
        "candidatePoints": len(points),
        "plannedAttempts": len(attempts),
        "scannedAttempts": scanned,
        "findings": findings,
    }


async def run_scan(
    *,
    url: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    started_at = time.time()
    async with async_playwright() as playwright:
        browser_type = getattr(playwright, args.browser)
        launch_options: dict[str, Any] = {"headless": not args.headed}
        if args.channel:
            if args.browser != "chromium":
                raise ValueError("--channel can only be used with chromium")
            launch_options["channel"] = args.channel
        browser = await browser_type.launch(**launch_options)
        try:
            viewport_reports = []
            for viewport in args.viewport:
                viewport_reports.append(
                    await scan_viewport(
                        browser,
                        url=url,
                        viewport=viewport,
                        args=args,
                    )
                )
                if sum(
                    len(report["findings"])
                    for report in viewport_reports
                ) >= args.max_findings:
                    break
        finally:
            await browser.close()

    findings = [
        finding
        for report in viewport_reports
        for finding in report["findings"]
    ]
    return {
        "jitterFound": bool(findings),
        "findingCount": len(findings),
        "url": url,
        "browser": args.browser,
        "channel": args.channel,
        "headed": args.headed,
        "deviceScaleFactor": args.device_scale_factor,
        "configuration": {
            "step": args.step,
            "margin": args.margin,
            "exhaustive": args.exhaustive,
            "activationDelayMs": args.activation_delay_ms,
            "sampleMs": args.sample_ms,
            "minimumRunPixels": args.minimum_run_pixels,
            "minimumAmplitudePixels": args.minimum_amplitude_pixels,
            "minimumReversals": args.minimum_reversals,
            "workers": args.workers,
        },
        "elapsedSeconds": round(time.time() - started_at, 3),
        "viewports": viewport_reports,
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan the animated header wordmark for stationary cursor points "
            "that cause repeated fold/unfold direction changes."
        )
    )
    parser.add_argument(
        "--url",
        help="existing site URL; omit to start a temporary local Hugo server",
    )
    parser.add_argument(
        "--viewport",
        action="append",
        type=parse_viewport,
        help="viewport WIDTHxHEIGHT; repeat for multiple sizes (default: 1280x720)",
    )
    parser.add_argument(
        "--browser",
        choices=("chromium", "firefox", "webkit"),
        default="chromium",
    )
    parser.add_argument(
        "--channel",
        help="installed Chromium channel such as chrome or msedge",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--device-scale-factor", type=float, default=1.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--step", type=float, default=1.0)
    parser.add_argument("--margin", type=float, default=2.0)
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="scan the full 2D grid instead of center and boundary lines",
    )
    parser.add_argument(
        "--activation-delay-ms",
        action="append",
        type=float,
        help=(
            "delay between entering compact TMC and moving to the candidate; "
            "repeat as needed (defaults: 0, 175, 400)"
        ),
    )
    parser.add_argument("--sample-ms", type=float, default=1600.0)
    parser.add_argument("--minimum-run-pixels", type=float, default=2.0)
    parser.add_argument("--minimum-amplitude-pixels", type=float, default=4.0)
    parser.add_argument("--minimum-reversals", type=int, default=2)
    parser.add_argument("--max-findings", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--output", type=Path)
    return parser


def validate_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    positive_values = {
        "--device-scale-factor": args.device_scale_factor,
        "--workers": args.workers,
        "--step": args.step,
        "--sample-ms": args.sample_ms,
        "--minimum-run-pixels": args.minimum_run_pixels,
        "--minimum-amplitude-pixels": args.minimum_amplitude_pixels,
        "--minimum-reversals": args.minimum_reversals,
        "--max-findings": args.max_findings,
        "--progress-every": args.progress_every,
    }
    for option, value in positive_values.items():
        if value <= 0:
            parser.error(f"{option} must be positive")
    if args.margin < 0:
        parser.error("--margin cannot be negative")
    if any(delay < 0 for delay in args.activation_delay_ms):
        parser.error("--activation-delay-ms cannot be negative")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.viewport is None:
        args.viewport = [Viewport(1280, 720)]
    if args.activation_delay_ms is None:
        args.activation_delay_ms = list(DEFAULT_DELAYS_MS)
    validate_args(parser, args)

    try:
        with diagnostic_site(args.url) as url:
            report = asyncio.run(run_scan(url=url, args=args))
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(rendered)
    return 1 if report["jitterFound"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
