"""Unit coverage for the animated-wordmark jitter scanner."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT


MODULE_PATH = PROJECT_ROOT / "scripts" / "find_wordmark_jitter.py"
SPEC = importlib.util.spec_from_file_location("wordmark_jitter_scanner", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SCANNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCANNER
SPEC.loader.exec_module(SCANNER)

MotionSummary = SCANNER.MotionSummary
Viewport = SCANNER.Viewport
build_candidate_points = SCANNER.build_candidate_points
count_direction_reversals = SCANNER.count_direction_reversals
is_jitter = SCANNER.is_jitter
parse_viewport = SCANNER.parse_viewport
summarize_motion = SCANNER.summarize_motion


def _samples(widths: list[float]) -> list[dict[str, object]]:
    return [
        {
            "totalWidth": width,
            "retained": True,
            "hovered": True,
        }
        for width in widths
    ]


def test_parse_viewport_requires_positive_width_and_height() -> None:
    assert parse_viewport("1280x720") == Viewport(1280, 720)
    with pytest.raises(Exception, match="positive"):
        parse_viewport("1280x0")
    with pytest.raises(Exception, match="1280x720"):
        parse_viewport("wide")


def test_direction_detector_ignores_settling_and_subpixel_noise() -> None:
    assert count_direction_reversals(
        [0, 10, 30, 29.8, 50, 50.1],
        minimum_run_pixels=2,
    ) == 0
    assert count_direction_reversals(
        [0, 30, 10, 0],
        minimum_run_pixels=2,
    ) == 1


def test_direction_detector_reports_repeated_fold_unfold_motion() -> None:
    widths = [0, 20, 50, 25, 2, 30, 55, 20, 0]
    summary = summarize_motion(
        _samples(widths),
        minimum_run_pixels=2,
    )

    assert summary.direction_reversals == 3
    assert is_jitter(
        summary,
        minimum_reversals=2,
        minimum_amplitude_pixels=4,
    )


def test_state_changes_need_visible_motion_to_count_as_jitter() -> None:
    stable = MotionSummary(
        minimum_width=100,
        maximum_width=100.1,
        final_width=100,
        direction_reversals=0,
        retained_state_toggles=4,
        hover_state_toggles=4,
    )
    assert not is_jitter(
        stable,
        minimum_reversals=2,
        minimum_amplitude_pixels=4,
    )

    monotonic_fold_with_hover_rehit = MotionSummary(
        minimum_width=0,
        maximum_width=305,
        final_width=0,
        direction_reversals=0,
        retained_state_toggles=0,
        hover_state_toggles=2,
    )
    assert not is_jitter(
        monotonic_fold_with_hover_rehit,
        minimum_reversals=2,
        minimum_amplitude_pixels=4,
    )


def test_fast_scan_includes_center_and_every_stage_edge() -> None:
    stage = {"x": 10.0, "y": 20.0, "width": 40.0, "height": 10.0}
    points = build_candidate_points(
        stage,
        step=1,
        margin=2,
        exhaustive=False,
    )
    coordinates = {(point.x, point.y) for point in points}

    assert (30.0, 25.0) in coordinates
    assert (8.0, 25.0) in coordinates
    assert (52.0, 25.0) in coordinates
    assert (20.0, 18.0) in coordinates
    assert any(point.y == 18.0 for point in points)
    assert any(point.y == 32.0 for point in points)


def test_automatic_wordmark_jitter_scan(
    base_url: str,
    tmp_path: Path,
) -> None:
    """Run a bounded generic coordinate scan in every ordinary test run.

    The exhaustive diagnostic contains tens of thousands of attempts and is
    intentionally manual. This CI profile still searches the complete center
    line, all four boundaries, the outside margin that exposed the reported
    bug, and five vertical slices. It repeats those coordinates before, during,
    and after unfolding. The previously missed boundary loop produces several
    reversals within the 350 ms observation window and fails this test.

    When this test fails, use ``scripts/find_wordmark_jitter.py`` directly to
    investigate the cause. Its JSON report includes the offending coordinates
    and complete frame samples, while ``--headed`` and ``--exhaustive`` provide
    visual reproduction and a denser follow-up scan than this bounded CI run.
    """

    report_path = tmp_path / "wordmark-jitter-scan.json"
    started_at = time.perf_counter()
    scan = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--url",
            base_url,
            "--step",
            "32",
            "--sample-ms",
            "350",
            "--activation-delay-ms",
            "0",
            "--activation-delay-ms",
            "175",
            "--activation-delay-ms",
            "400",
            "--workers",
            "4",
            "--max-findings",
            "1",
            "--progress-every",
            "10000",
            "--output",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=90,
        check=False,
    )
    elapsed_seconds = time.perf_counter() - started_at
    assert report_path.exists(), scan.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    diagnostic = json.dumps(
        {
            "elapsedSeconds": round(elapsed_seconds, 3),
            "returnCode": scan.returncode,
            "findings": report["findings"],
            "viewports": report["viewports"],
            "stderr": scan.stderr,
        },
        ensure_ascii=False,
        indent=2,
    )

    assert scan.returncode == 0, diagnostic
    assert report["jitterFound"] is False, diagnostic
    assert len(report["viewports"]) == 1, diagnostic
    viewport_report = report["viewports"][0]
    assert (
        viewport_report["scannedAttempts"]
        == viewport_report["plannedAttempts"]
    ), diagnostic
