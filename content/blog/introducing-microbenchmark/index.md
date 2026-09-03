---
title: "Introducing microbenchmark: benchmarks as composable Python objects"
slug: "introducing-microbenchmark"
date: 2026-04-10T09:00:00+03:00
description: "A small object model between one-off timing snippets and full benchmark frameworks."
author: "muta"
tags: [python, benchmarking, performance, cli, testing, developer-tools]
---

A quick `timeit` snippet is easy to write, but it becomes awkward when several small measurements need names, shared execution, machine-readable results, and a simple failure threshold. Full benchmark suites solve much more, at the cost of more machinery than a focused library check may need.

[microbenchmark](https://github.com/mutating/microbenchmark) is a lightweight Python library between those two extremes. Its [first release](https://github.com/mutating/microbenchmark/releases/tag/0.0.1) represents benchmark scenarios, groups, and results as ordinary Python objects that can be combined, inspected, serialized, and checked against a basic performance budget.

## Core API

A `Scenario` keeps a callable, positional arguments, a name, documentation, and the number of observations together. `run()` performs optional warmup calls, then times every invocation separately with `perf_counter()` by default.

The result is a `BenchmarkResult` that retains every duration rather than immediately reducing the run to one number. It exposes the mean, best, and worst observations. Its `p95` and `p99` properties return trimmed result sets—the fastest 95 or 99 percent of samples—not conventional scalar latency percentiles.

Scenarios compose as values. Add two of them and you get a flat, ordered `ScenarioGroup`. Results serialize to JSON so another process can inspect the raw observations. The restored result intentionally does not reconstruct the executable scenario itself.

The same object can expose a small command-line interface from a benchmark script. `--number` changes the run size, while `--max-mean` fails the process when the mean-time budget is exceeded. In the first release, the script calls `.cli()` itself rather than relying on an installed command.

The first release is typed, supports Python 3.8 and newer, and has no runtime dependencies.

## Alternatives and trade-offs

[`timeit`](https://docs.python.org/3/library/timeit.html) is the better stopwatch for a tiny expression. It ships with Python, batches operations, can autorange to a meaningful duration, and reduces timer overhead. It does not provide named scenario groups, a domain result object, JSON round trips, or a CI ceiling.

[pyperf](https://pyperf.readthedocs.io/) is the better choice for measurement rigor, with calibrated loops, worker processes, metadata, and statistical comparisons. [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) adds rich statistics and regression checks to pytest. [Airspeed Velocity](https://asv.readthedocs.io/) tracks performance across commits and environments.

microbenchmark does not reproduce those guarantees in miniature. Its niche is a lightweight in-process object protocol: richer than a one-off timer, but simple enough to live inside any Python project without a test runner or performance laboratory.

The trade-off is real. Timing every call separately makes observations easy to inspect but includes more timer and Python-call overhead. There is no automatic calibration, process isolation, or baseline comparison. The `--max-mean` limit is useful as a smoke test, but it remains sensitive to CI hardware and load.

## Who it is for

The intended user is a library author who wants a few readable, reusable performance scenarios beside the code they measure. Small parsers, transformations, serializers, dispatch operations, and scheduling helpers can share one result model without turning performance work into a separate project.

That gives the surrounding toolchain a common language for watching frequently executed operations while leaving serious benchmarking to serious benchmark systems.

## What comes next

The next releases will add positional and keyword argument objects, optional names derived from callables, the median, and more informative terminal summaries. A directly installed command will run scenarios by import path, while console histograms and clearer wall-time accounting will make results easier to inspect.

We also plan early callable-signature checks so a scenario fails before a long run begins. These improvements make the small protocol more pleasant; they do not change its statistical boundary. When the experiment needs calibrated workers, system metadata, or a historical baseline, pyperf, pytest-benchmark, and ASV should remain the tools of choice.
