---
title: "Introducing microbenchmark: benchmarks as composable Python objects"
slug: "introducing-microbenchmark"
date: 2026-04-10T09:00:00+03:00
description: "Turns small performance checks into reusable Python scenarios and results without requiring a full benchmark suite."
author: "muta"
tags: [python, benchmarking, performance, cli, testing, developer-tools]
---

A `timeit` snippet becomes awkward when several measurements need names, shared execution, machine-readable results, and a failure threshold. Full benchmark suites may require more setup and statistical machinery than a small library check needs.

[`timeit`](https://docs.python.org/3/library/timeit.html) provides timing primitives but no scenario model or CI budget. [pyperf](https://pyperf.readthedocs.io/) and [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) provide stronger statistics and isolation. A small in-process API covers the space between them.

[microbenchmark](https://github.com/mutating/microbenchmark) is a lightweight Python library for structured, small-scale measurements. It represents benchmark scenarios, groups, and results as ordinary Python objects that can be combined, inspected, serialized, and checked against a basic performance budget. [The release](https://github.com/mutating/microbenchmark/releases/tag/0.0.1) is available on GitHub.

## Core API

A `Scenario` keeps a callable, positional arguments, a name, documentation, and the number of observations together. `run()` performs optional warmup calls, then times every invocation separately with `perf_counter()` by default.

```python
from microbenchmark import Scenario

def build_list():
    return list(range(1000))

scenario = Scenario(build_list, name="build_list", number=500)
result = scenario.run()

print(result.mean)
print(result.best)
print(result.worst)
```

The result is a `BenchmarkResult` that retains every duration rather than immediately reducing the run to one number. It exposes the mean, best, and worst observations. Its `p95` and `p99` properties return trimmed result sets—the fastest 95 or 99 percent of samples—not conventional scalar latency percentiles.

Adding scenarios creates a flat, ordered `ScenarioGroup`. Results serialize to JSON, but deserialization does not reconstruct executable scenarios.

Calling `.cli()` exposes `--number` and a `--max-mean` failure threshold from the benchmark script; microbenchmark installs no separate command.

The library is typed and has no runtime dependencies.

## Alternatives and trade-offs

[`timeit`](https://docs.python.org/3/library/timeit.html) remains the right choice for a one-off measurement. Its batching and automatic range selection also reduce timing overhead.

[pyperf](https://pyperf.readthedocs.io/) adds calibrated loops, worker processes, and statistical comparisons; [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) integrates regression checks with pytest; [Airspeed Velocity](https://asv.readthedocs.io/) tracks performance across commits.

Timing every call separately adds timer and Python-call overhead. There is no automatic calibration, process isolation, or baseline comparison, and `--max-mean` remains sensitive to CI hardware and load.

## Intended users

microbenchmark is for library authors who want a few reusable performance scenarios beside their code. Work requiring stronger statistical guarantees should use a full benchmark system.

## What comes next

The project will simplify Python and command-line use while remaining a lightweight tool rather than a statistical benchmarking system.
