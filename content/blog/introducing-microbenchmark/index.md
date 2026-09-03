---
title: "Introducing microbenchmark: benchmarks as composable Python objects"
slug: "introducing-microbenchmark"
date: 2026-04-10T09:00:00+03:00
description: "A small object model between one-off timing snippets and full benchmark frameworks."
author: "muta"
logo: "logo.png"
tags: [python, benchmarking, performance, cli, testing, developer-tools]
---

The [microbenchmark](https://github.com/mutating/microbenchmark) repository opened on 9 April 2026. Later that day, [microbenchmark 0.0.1](https://github.com/mutating/microbenchmark/releases/tag/0.0.1) turned focused Python benchmarks into ordinary values that can be named, combined, inspected, serialized, and given a simple performance budget.

## Three small building blocks

A `Scenario` keeps a callable, positional arguments, a name, documentation, and the number of observations together. `run()` performs optional warmup calls, then times every invocation separately with `perf_counter()` by default.

The result is a `BenchmarkResult` that retains all durations rather than immediately reducing the run to one number. It exposes the mean, best, and worst observations, plus `p95` and `p99` result views. Those views contain the fastest 95 or 99 percent of samples; they are useful for inspecting a trimmed distribution, but they are not conventional scalar p95 and p99 latency statistics.

Scenarios compose as values. Add two of them and you get a flat, ordered `ScenarioGroup`. Results serialize to JSON so another process can inspect the raw observations. The restored result intentionally does not reconstruct the executable scenario itself.

The same object can also expose a small command-line interface from a user-written benchmark script. `--number` changes the run size, while `--max-mean` exits with status 1 when the absolute mean-time budget is exceeded. Version 0.0.1 does not yet install a global `microbenchmark` command; the script calls `.cli()` itself.

The first release is typed, supports Python 3.8 and newer, and has no runtime dependencies.

## The missing middle

[`timeit`](https://docs.python.org/3/library/timeit.html) is the better stopwatch for a tiny expression. It ships with Python, batches operations, can autorange to a meaningful duration, and reduces timer overhead. It does not provide named scenario groups, a domain result object, JSON round trips, or a CI ceiling.

[pyperf](https://pyperf.readthedocs.io/) is the better choice for measurement rigor. It calibrates loops, uses warmups and worker processes, captures metadata, warns about instability, tunes systems, and performs statistical comparisons. [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) integrates calibration, rich statistics, stored baselines, and regression checks into pytest. [Airspeed Velocity](https://asv.readthedocs.io/) tracks performance across commits and environments and publishes a historical site.

microbenchmark does not reproduce those guarantees in miniature. Its niche is a lightweight in-process object protocol: richer than a one-off timer, but simple enough to live inside any Python project without a test runner or performance laboratory.

The trade-off is real. Timing every call separately makes observations easy to inspect but exposes timer and Python-call overhead. There is no automatic calibration, process isolation, CPU affinity, GC control, confidence interval, or baseline comparison. An absolute `--max-mean` limit is useful as a coarse performance smoke test, but it is sensitive to CI hardware and load.

## Who it is for

The intended user is a library author who wants a few readable, reusable performance scenarios beside the code they measure. Small parsers, transformations, serializers, dispatch operations, and scheduling helpers can share one result model without turning performance work into a separate project.

That gives the surrounding toolchain a common language for watching frequently executed operations while leaving serious benchmarking to serious benchmark systems.

## What comes next

The next releases will add positional and keyword argument objects, optional names derived from callables, the median, and more informative terminal summaries. A directly installed command will run scenarios by import path, while console histograms and clearer wall-time accounting will make results easier to inspect.

We also plan early callable-signature checks so a scenario fails before a long run begins. These improvements make the small protocol more pleasant; they do not change its statistical boundary. When the experiment needs calibrated workers, system metadata, or a historical baseline, pyperf, pytest-benchmark, and ASV should remain the tools of choice.
