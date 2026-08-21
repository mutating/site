#!/usr/bin/env python3
"""Measure deterministic Tentacles physics, frame submission, and JS heap."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Browser, sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TENTACLES_SOURCE = (
    PROJECT_ROOT / "themes" / "mutating" / "static" / "muta-tentacles.html"
)


def summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]

    return {
        "mean": round(sum(ordered) / len(ordered), 4),
        "p50": round(percentile(0.50), 4),
        "p95": round(percentile(0.95), 4),
        "p99": round(percentile(0.99), 4),
        "minimum": round(ordered[0], 4),
        "maximum": round(ordered[-1], 4),
    }


def run_scenario(
    browser: Browser,
    *,
    base_url: str,
    name: str,
    pointer: tuple[float, float] | None,
    sample_count: int,
    warmup_count: int,
    snapshot_path: Path | None,
) -> dict[str, Any]:
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        device_scale_factor=1,
        reduced_motion="no-preference",
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    query = urlencode(
        {
            "generation-seed": "12345",
            "manual-animation-frame": "1",
            "animation-time-origin": "0",
            "interaction-diagnostics": "1",
        }
    )
    page.goto(f"{base_url}?{query}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )

    canvas = page.locator("#muta")
    if pointer is not None:
        bounds = canvas.bounding_box()
        if bounds is None:
            raise RuntimeError("Tentacle canvas has no layout box")
        page.mouse.move(
            bounds["x"] + bounds["width"] * pointer[0],
            bounds["y"] + bounds["height"] * pointer[1],
        )

    page.evaluate(
        """
        (frames) => {
          for (let index = 0; index < frames; index += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """,
        warmup_count,
    )
    samples = page.evaluate(
        """
        (frames) => {
          const canvas = document.querySelector("#muta");
          const frameMilliseconds = new Array(frames);
          const physicsMilliseconds = new Array(frames);
          for (let index = 0; index < frames; index += 1) {
            const startedAt = performance.now();
            // Publishing the production timer requires one diagnostic second;
            // the simulation delta itself remains capped at the 33 ms slow path.
            window.__mutaTentaclesDebugFrameAfter(1001);
            frameMilliseconds[index] = performance.now() - startedAt;
            physicsMilliseconds[index] = Number(canvas.dataset.physicsMs);
          }
          return { frameMilliseconds, physicsMilliseconds };
        }
        """,
        sample_count,
    )

    cdp = context.new_cdp_session(page)
    cdp.send("HeapProfiler.collectGarbage")
    heap = cdp.send("Runtime.getHeapUsage")
    metadata = page.evaluate(
        """
        () => {
          const canvas = document.querySelector("#muta");
          const gl = canvas.getContext("webgl2");
          const extension = gl.getExtension("WEBGL_debug_renderer_info");
          const renderer = extension
            ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL)
            : gl.getParameter(gl.RENDERER);
          const data = canvas.dataset;
          return {
            canvas: `${canvas.width}x${canvas.height}`,
            renderer,
            tentacles: Number(data.tentacles),
            surfaceGrid: data.surfaceGrid,
            spatialShaderPath: data.surfaceShaderPath,
            maximumSpatialCellLoad: Number(data.maximumSpatialCellLoad),
            pointer: {
              phase: data.pointerInteractionPhase,
              forceMultiplier: data.pointerInteractionForceMultiplier,
              radiusMultiplier: data.pointerInteractionRadiusMultiplier,
              activeTips: data.activeTips,
              maximumAttraction: data.maximumAttraction,
              minimumTipClearance: data.minimumTipClearance,
              minimumCursorDistance: data.minimumCursorDistance,
              grabberCount: data.grabberCount,
              maximumGrabberError: data.maximumGrabberError,
              maximumRestDisplacement: data.renderedMaximumRestDisplacement,
            },
            userAgent: navigator.userAgent,
          };
        }
        """
    )

    if snapshot_path is not None and name == "hover":
        canvas.screenshot(path=snapshot_path)
    context.close()
    return {
        "frameMs": summarize(samples["frameMilliseconds"]),
        "physicsMs": summarize(samples["physicsMilliseconds"]),
        "heap": {
            "usedBytes": heap["usedSize"],
            "totalBytes": heap["totalSize"],
        },
        "metadata": metadata,
        "pageErrors": errors,
    }


def measure_input_latency(
    browser: Browser,
    *,
    base_url: str,
    button: str,
    sample_count: int,
    warmup_count: int,
) -> dict[str, Any]:
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        device_scale_factor=1,
        reduced_motion="no-preference",
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script(
        """
        addEventListener("pointerdown", () => {
          globalThis.__mutaPointerDownEpoch =
            performance.timeOrigin + performance.now();
        }, { capture: true });
        addEventListener("pointerup", () => {
          globalThis.__mutaPointerUpEpoch =
            performance.timeOrigin + performance.now();
        }, { capture: true });
        """
    )
    query = urlencode(
        {
            "generation-seed": "12345",
            "manual-animation-frame": "1",
            "animation-time-origin": "0",
            "interaction-diagnostics": "1",
        }
    )
    page.goto(f"{base_url}?{query}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof window.__mutaTentaclesDebugFrameAfter === 'function'"
    )
    canvas = page.locator("#muta")
    bounds = canvas.bounding_box()
    if bounds is None:
        raise RuntimeError("Tentacle canvas has no layout box")
    page.mouse.move(
        bounds["x"] + bounds["width"] * 0.58,
        bounds["y"] + bounds["height"] * 0.52,
    )
    page.evaluate(
        """
        (frames) => {
          for (let index = 0; index < frames; index += 1) {
            window.__mutaTentaclesDebugFrameAfter(1000 / 60);
          }
        }
        """,
        warmup_count,
    )
    page.wait_for_timeout(500)

    cdp = context.new_cdp_session(page)
    compositor_timestamps: list[float] = []

    def capture_compositor_frame(event: dict[str, Any]) -> None:
        timestamp = event["metadata"].get("timestamp")
        if timestamp is not None:
            compositor_timestamps.append(float(timestamp) * 1000)
        cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def wait_for_compositor_frame(event_epoch: float) -> float:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            page.wait_for_timeout(2)
            candidates = [
                timestamp
                for timestamp in compositor_timestamps
                if timestamp >= event_epoch
            ]
            if candidates:
                return min(candidates) - event_epoch
        raise TimeoutError("Chrome did not emit a compositor frame after input")

    cdp.on("Page.screencastFrame", capture_compositor_frame)
    cdp.send(
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": 30,
            "maxWidth": 320,
            "maxHeight": 180,
            "everyNthFrame": 1,
        },
    )
    page.wait_for_timeout(300)

    for _index in range(5):
        compositor_timestamps.clear()
        page.mouse.down(button=button)
        pointer_down_epoch = float(page.evaluate("globalThis.__mutaPointerDownEpoch"))
        wait_for_compositor_frame(pointer_down_epoch)
        compositor_timestamps.clear()
        page.mouse.up(button=button)
        pointer_up_epoch = float(page.evaluate("globalThis.__mutaPointerUpEpoch"))
        wait_for_compositor_frame(pointer_up_epoch)

    submission_milliseconds: list[float] = []
    compositor_frame_milliseconds: list[float] = []
    for _index in range(sample_count):
        compositor_timestamps.clear()
        page.mouse.down(button=button)
        submission_milliseconds.append(
            float(canvas.get_attribute("data-input-to-impulse-submission-ms") or 0)
        )
        pointer_down_epoch = float(page.evaluate("globalThis.__mutaPointerDownEpoch"))
        compositor_frame_milliseconds.append(
            wait_for_compositor_frame(pointer_down_epoch)
        )
        compositor_timestamps.clear()
        page.mouse.up(button=button)
        pointer_up_epoch = float(page.evaluate("globalThis.__mutaPointerUpEpoch"))
        wait_for_compositor_frame(pointer_up_epoch)

    cdp.send("Page.stopScreencast")
    context.close()
    result: dict[str, Any] = {
        "submissionMs": summarize(submission_milliseconds),
        "compositorFrameMs": summarize(compositor_frame_milliseconds),
        "pageErrors": errors,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:41737/muta-tentacles.html",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--samples", type=int, default=360)
    parser.add_argument("--warmup", type=int, default=180)
    parser.add_argument("--input-samples", type=int, default=0)
    parser.add_argument("--input-only", action="store_true")
    parser.add_argument("--executable")
    arguments = parser.parse_args()

    source_sha256 = hashlib.sha256(TENTACLES_SOURCE.read_bytes()).hexdigest()
    with sync_playwright() as playwright:
        launch_options: dict[str, Any] = {
            "headless": True,
            "args": ["--enable-precise-memory-info"],
        }
        if arguments.executable:
            launch_options["executable_path"] = arguments.executable
        browser = playwright.chromium.launch(**launch_options)
        scenarios = {}
        if not arguments.input_only:
            scenarios["idle"] = run_scenario(
                browser,
                base_url=arguments.url,
                name="idle",
                pointer=None,
                sample_count=arguments.samples,
                warmup_count=arguments.warmup,
                snapshot_path=arguments.snapshot,
            )
            scenarios["hover"] = run_scenario(
                browser,
                base_url=arguments.url,
                name="hover",
                pointer=(0.58, 0.52),
                sample_count=arguments.samples,
                warmup_count=arguments.warmup,
                snapshot_path=arguments.snapshot,
            )
        if arguments.input_samples > 0:
            scenarios["leftInput"] = measure_input_latency(
                browser,
                base_url=arguments.url,
                button="left",
                sample_count=arguments.input_samples,
                warmup_count=arguments.warmup,
            )
            scenarios["rightInput"] = measure_input_latency(
                browser,
                base_url=arguments.url,
                button="right",
                sample_count=arguments.input_samples,
                warmup_count=arguments.warmup,
            )
        result = {
            "benchmark": {
                "sampleCount": arguments.samples,
                "warmupCount": arguments.warmup,
                "viewport": "1280x720@1x",
                "seed": 12345,
                "frameDelta": "33ms capped slow-frame path",
            },
            "browserVersion": browser.version,
            "sourceSha256": source_sha256,
            "scenarios": scenarios,
        }
        browser.close()

    serialized = f"{json.dumps(result, indent=2)}\n"
    if arguments.output is not None:
        arguments.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
