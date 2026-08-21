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
    gpu_timing: bool,
) -> dict[str, Any]:
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        device_scale_factor=1,
        reduced_motion="no-preference",
    )
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    query_parameters = {
        "generation-seed": "12345",
        "manual-animation-frame": "1",
        "animation-time-origin": "0",
        "interaction-diagnostics": "1",
    }
    if gpu_timing:
        query_parameters["gpu-timing"] = "1"
    query = urlencode(query_parameters)
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

    if gpu_timing:
        for _index in range(warmup_count):
            page.evaluate("window.__mutaTentaclesDebugFrameAfter(1000 / 60)")
            page.wait_for_timeout(2)
        frame_milliseconds: list[float] = []
        physics_milliseconds: list[float] = []
        for _index in range(sample_count):
            sample = page.evaluate(
                """
                () => {
                  const canvas = document.querySelector("#muta");
                  const startedAt = performance.now();
                  window.__mutaTentaclesDebugFrameAfter(1001);
                  return {
                    frame: performance.now() - startedAt,
                    physics: Number(canvas.dataset.physicsMs),
                  };
                }
                """
            )
            frame_milliseconds.append(sample["frame"])
            physics_milliseconds.append(sample["physics"])
            page.wait_for_timeout(2)
        samples = {
            "frameMilliseconds": frame_milliseconds,
            "physicsMilliseconds": physics_milliseconds,
        }
    else:
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
                // Publishing the production timer requires one diagnostic
                // second; the simulation delta itself remains capped at the
                // 33 ms slow path.
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
            gpu: {
              status: data.gpuTiming,
              latestMs:
                data.gpuMs === undefined ? null : Number(data.gpuMs),
              averageMs:
                data.gpuMsAverage === undefined
                  ? null
                  : Number(data.gpuMsAverage),
              samples: Number(data.gpuTimingSamples || 0),
              pending: Number(data.gpuTimingPending || 0),
              dropped: Number(data.gpuTimingDroppedSamples || 0),
              disjoint: Number(data.gpuTimingDisjointCount || 0),
            },
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
    gpu_timing: bool,
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
          globalThis.__mutaPointerDownStartedAt = performance.now();
          globalThis.__mutaPointerDownEpoch =
            performance.timeOrigin + performance.now();
          globalThis.__mutaPointerDownRafMs = null;
          requestAnimationFrame(() => {
            globalThis.__mutaPointerDownRafMs =
              performance.now() - globalThis.__mutaPointerDownStartedAt;
          });
        }, { capture: true });
        addEventListener("pointerdown", () => {
          globalThis.__mutaPointerDownHandlerMs =
            performance.now() - globalThis.__mutaPointerDownStartedAt;
        });
        addEventListener("pointerup", () => {
          globalThis.__mutaPointerUpEpoch =
            performance.timeOrigin + performance.now();
        }, { capture: true });
        """
    )
    query_parameters = {
        "generation-seed": "12345",
        "interaction-diagnostics": "1",
    }
    if gpu_timing:
        query_parameters["gpu-timing"] = "1"
    query = urlencode(query_parameters)
    page.goto(f"{base_url}?{query}", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => Number(document.querySelector('#muta')?.dataset.physicsFrameId) > 0"
    )
    canvas = page.locator("#muta")
    bounds = canvas.bounding_box()
    if bounds is None:
        raise RuntimeError("Tentacle canvas has no layout box")
    page.mouse.move(
        bounds["x"] + bounds["width"] * 0.58,
        bounds["y"] + bounds["height"] * 0.52,
    )
    initial_physics_frame = int(
        canvas.get_attribute("data-physics-frame-id") or "0"
    )
    page.wait_for_function(
        """
        ([initialFrame, warmupFrames]) =>
          Number(document.querySelector("#muta").dataset.physicsFrameId) >=
            initialFrame + warmupFrames
        """,
        arg=[initial_physics_frame, warmup_count],
        timeout=30_000,
    )

    cdp = context.new_cdp_session(page)
    compositor_timestamps: list[float] = []

    def capture_compositor_frame(event: dict[str, Any]) -> None:
        timestamp = event["metadata"].get("timestamp")
        if timestamp is not None:
            compositor_timestamps.append(float(timestamp) * 1000)
        cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def wait_for_compositor_frame(
        not_before_epoch: float,
        latency_origin_epoch: float | None = None,
    ) -> float:
        if latency_origin_epoch is None:
            latency_origin_epoch = not_before_epoch
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            page.wait_for_timeout(2)
            candidates = [
                timestamp
                for timestamp in compositor_timestamps
                if timestamp >= not_before_epoch
            ]
            if candidates:
                return min(candidates) - latency_origin_epoch
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

    def submit_pointer_down() -> tuple[float, float, float, int, int, float]:
        baseline_rendered_frame = int(
            canvas.get_attribute("data-rendered-frame-id") or "0"
        )
        baseline_impulse_applications = int(
            canvas.get_attribute("data-impulse-physics-applications") or "0"
        )
        canvas.evaluate(
            """
            (element) => {
              delete element.dataset.inputToImpulseSubmissionMs;
            }
            """
        )
        page.mouse.down(button=button)
        handler_milliseconds = float(
            page.evaluate("globalThis.__mutaPointerDownHandlerMs")
        )
        page.wait_for_function(
            """
            (baselineFrame) => {
              const data = document.querySelector("#muta").dataset;
              return Number(data.renderedFrameId) > baselineFrame &&
                data.renderedPointerPressed === "true" &&
                data.inputToImpulseSubmissionMs !== undefined;
            }
            """,
            arg=baseline_rendered_frame,
            timeout=10_000,
        )
        submitted_rendered_frame = int(
            canvas.get_attribute("data-rendered-frame-id") or "0"
        )
        submitted_impulse_applications = int(
            canvas.get_attribute("data-impulse-physics-applications") or "0"
        )
        submission_milliseconds = float(
            canvas.get_attribute("data-input-to-impulse-submission-ms") or 0
        )
        page.wait_for_function(
            "globalThis.__mutaPointerDownRafMs !== null",
            timeout=10_000,
        )
        raf_milliseconds = float(
            page.evaluate("globalThis.__mutaPointerDownRafMs")
        )
        pointer_down_epoch = float(
            page.evaluate("globalThis.__mutaPointerDownEpoch")
        )
        return (
            submission_milliseconds,
            handler_milliseconds,
            raf_milliseconds,
            submitted_rendered_frame - baseline_rendered_frame,
            submitted_impulse_applications - baseline_impulse_applications,
            pointer_down_epoch,
        )

    for _index in range(5):
        compositor_timestamps.clear()
        submission_ms, _, _, _, _, pointer_down_epoch = submit_pointer_down()
        wait_for_compositor_frame(
            pointer_down_epoch + submission_ms,
            pointer_down_epoch,
        )
        compositor_timestamps.clear()
        page.mouse.up(button=button)
        pointer_up_epoch = float(page.evaluate("globalThis.__mutaPointerUpEpoch"))
        wait_for_compositor_frame(pointer_up_epoch)

    submission_milliseconds: list[float] = []
    handler_milliseconds: list[float] = []
    raf_milliseconds: list[float] = []
    submitted_rendered_frames: list[float] = []
    impulse_applications: list[float] = []
    compositor_frame_milliseconds: list[float] = []
    for sample_index in range(sample_count):
        # Real input can arrive at any point in a display interval. Sweep a
        # deterministic 0–16 ms offset so repeated clicks do not phase-lock to
        # the screencast acknowledgement and accidentally sample one RAF slot.
        phase_offset_milliseconds = (sample_index * 7) % 17
        if phase_offset_milliseconds:
            page.wait_for_timeout(phase_offset_milliseconds)
        compositor_timestamps.clear()
        (
            submission_ms,
            handler_ms,
            raf_ms,
            submitted_frames,
            applied_impulses,
            pointer_down_epoch,
        ) = submit_pointer_down()
        submission_milliseconds.append(submission_ms)
        handler_milliseconds.append(handler_ms)
        raf_milliseconds.append(raf_ms)
        submitted_rendered_frames.append(float(submitted_frames))
        impulse_applications.append(float(applied_impulses))
        compositor_frame_milliseconds.append(
            wait_for_compositor_frame(
                pointer_down_epoch + submission_ms,
                pointer_down_epoch,
            )
        )
        compositor_timestamps.clear()
        page.mouse.up(button=button)
        pointer_up_epoch = float(page.evaluate("globalThis.__mutaPointerUpEpoch"))
        wait_for_compositor_frame(pointer_up_epoch)

    gpu = canvas.evaluate(
        """
        (element) => ({
          status: element.dataset.gpuTiming,
          latestMs:
            element.dataset.gpuMs === undefined
              ? null
              : Number(element.dataset.gpuMs),
          averageMs:
            element.dataset.gpuMsAverage === undefined
              ? null
              : Number(element.dataset.gpuMsAverage),
          samples: Number(element.dataset.gpuTimingSamples || 0),
          pending: Number(element.dataset.gpuTimingPending || 0),
          dropped: Number(element.dataset.gpuTimingDroppedSamples || 0),
          disjoint: Number(element.dataset.gpuTimingDisjointCount || 0),
        })
        """
    )
    cdp.send("Page.stopScreencast")
    context.close()
    result: dict[str, Any] = {
        "submissionMs": summarize(submission_milliseconds),
        "handlerMs": summarize(handler_milliseconds),
        "nextRafMs": summarize(raf_milliseconds),
        "submittedRenderedFrames": summarize(submitted_rendered_frames),
        "impulseApplications": summarize(impulse_applications),
        "compositorFrameMs": summarize(compositor_frame_milliseconds),
        "gpu": gpu,
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
    parser.add_argument("--gpu-timing", action="store_true")
    parser.add_argument("--executable")
    parser.add_argument("--source", type=Path, default=TENTACLES_SOURCE)
    arguments = parser.parse_args()

    source_sha256 = hashlib.sha256(arguments.source.read_bytes()).hexdigest()
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
                gpu_timing=arguments.gpu_timing,
            )
            scenarios["hover"] = run_scenario(
                browser,
                base_url=arguments.url,
                name="hover",
                pointer=(0.58, 0.52),
                sample_count=arguments.samples,
                warmup_count=arguments.warmup,
                snapshot_path=arguments.snapshot,
                gpu_timing=arguments.gpu_timing,
            )
        if arguments.input_samples > 0:
            scenarios["leftInput"] = measure_input_latency(
                browser,
                base_url=arguments.url,
                button="left",
                sample_count=arguments.input_samples,
                warmup_count=arguments.warmup,
                gpu_timing=arguments.gpu_timing,
            )
            scenarios["rightInput"] = measure_input_latency(
                browser,
                base_url=arguments.url,
                button="right",
                sample_count=arguments.input_samples,
                warmup_count=arguments.warmup,
                gpu_timing=arguments.gpu_timing,
            )
        result = {
            "benchmark": {
                "sampleCount": arguments.samples,
                "inputSampleCount": arguments.input_samples,
                "warmupCount": arguments.warmup,
                "viewport": "1280x720@1x",
                "seed": 12345,
                "frameDelta": "33ms capped slow-frame path",
                "gpuTiming": arguments.gpu_timing,
                "inputPhaseSweep": "deterministic 0-16ms",
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
