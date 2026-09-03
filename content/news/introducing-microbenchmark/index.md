---
title: "Introducing microbenchmark: structured performance checks for Python"
slug: "introducing-microbenchmark"
aliases: ["/blog/introducing-microbenchmark/"]
date: 2026-04-10T09:00:00+03:00
description: "Turns small performance checks into reusable Python scenarios and results without requiring a full benchmark suite."
author: "muta"
logo_frame: false
tags: [python, benchmarking, performance, cli, testing, developer-tools]
---

A benchmark repeatedly measures how long an operation takes. Python's `timeit` is convenient for one quick measurement, but a project may need several named checks, reusable settings, saved results, and a clear failure when an operation becomes too slow. Full benchmark suites can require more setup and statistical machinery than a small library needs.

[`timeit`](https://docs.python.org/3/library/timeit.html) provides timing primitives but no scenario model or CI budget. [pyperf](https://pyperf.readthedocs.io/) and [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) provide stronger statistics and isolation. Smaller projects may need named, serializable measurements without a full harness.

[microbenchmark](https://github.com/mutating/microbenchmark) is a lightweight Python library for organizing small performance checks. Each measurement and its result is an ordinary Python object, so checks can be grouped, inspected, saved as JSON, and compared with a simple time limit. [The release](https://github.com/mutating/microbenchmark/releases/tag/0.0.1) is available on GitHub.

## Core API

A `Scenario` keeps the measured function, its arguments, a name, documentation, and the number of repetitions together. `run()` can make warm-up calls first, then times every invocation separately with `perf_counter()` by default.

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

The result is a `BenchmarkResult` that keeps every duration rather than immediately reducing the run to one number. It exposes the mean, best, and worst measurements. Its `p95` and `p99` properties return the fastest 95 or 99 percent of samples, not the single percentile values that monitoring tools commonly use.

Adding scenarios creates a flat, ordered `ScenarioGroup`. Results serialize to JSON, but deserialization does not reconstruct executable scenarios.

Calling `.cli()` exposes `--number` and a `--max-mean` failure threshold from the benchmark script; microbenchmark installs no separate command.

The library is typed and has no runtime dependencies.

## Alternatives and trade-offs

[`timeit`](https://docs.python.org/3/library/timeit.html) remains the right choice for a one-off measurement. Its batching and automatic range selection also reduce timing overhead.

[pyperf](https://pyperf.readthedocs.io/) adds calibrated loops, worker processes, and statistical comparisons; [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) integrates regression checks with pytest; [Airspeed Velocity](https://asv.readthedocs.io/) tracks performance across commits.

Timing every call separately adds timer and Python-call overhead. There is no automatic calibration, process isolation, or baseline comparison, and `--max-mean` remains sensitive to CI hardware and load.

## Intended users

microbenchmark is for library authors who want a few reusable performance scenarios beside their code. Work requiring stronger statistical guarantees should use a full benchmark system.
