---
title: "Introducing microbenchmark: benchmarks as composable Python objects"
slug: "introducing-microbenchmark"
date: 2026-04-10T09:00:00+03:00
description: "Turns small performance checks into reusable Python scenarios and results without requiring a full benchmark suite."
author: "muta"
tags: [python, benchmarking, performance, cli, testing, developer-tools]
---

A quick `timeit` snippet is easy to write, but it stops being convenient when several small measurements need names, shared execution, machine-readable results, and a simple failure threshold. Ad hoc scripts then become difficult to compare or automate, while full benchmark suites may require more setup and statistical machinery than a focused library check needs.

[`timeit`](https://docs.python.org/3/library/timeit.html) provides reliable timing primitives but no reusable model for named scenarios, groups, serialization, or a CI budget. [pyperf](https://pyperf.readthedocs.io/) and [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) provide statistically stronger harnesses, worker isolation, and regression analysis. The remaining gap is a small in-process API for projects that need structured measurements without adopting a full benchmarking system.

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

The benchmark definition and its raw measurements remain available as Python objects, so the caller can inspect or combine them without parsing console output.

The result is a `BenchmarkResult` that retains every duration rather than immediately reducing the run to one number. It exposes the mean, best, and worst observations. Its `p95` and `p99` properties return trimmed result sets—the fastest 95 or 99 percent of samples—not conventional scalar latency percentiles.

Scenarios compose as values. Add two of them and you get a flat, ordered `ScenarioGroup`. Results serialize to JSON so another process can inspect the raw observations. The restored result intentionally does not reconstruct the executable scenario itself.

The same object can expose a small command-line interface from a benchmark script. `--number` changes the run size, while `--max-mean` fails the process when the mean-time budget is exceeded. The script calls `.cli()` itself; microbenchmark does not install a separate command.

The library is typed and has no runtime dependencies.

## Alternatives and trade-offs

[`timeit`](https://docs.python.org/3/library/timeit.html) remains the right choice for a one-off measurement. Its batching and automatic range selection also reduce timing overhead.

[pyperf](https://pyperf.readthedocs.io/) is the better choice for measurement rigor, with calibrated loops, worker processes, metadata, and statistical comparisons. [pytest-benchmark](https://pytest-benchmark.readthedocs.io/en/latest/) adds rich statistics and regression checks to pytest. [Airspeed Velocity](https://asv.readthedocs.io/) tracks performance across commits and environments.

Timing every call separately makes observations easy to inspect but includes more timer and Python-call overhead. There is no automatic calibration, process isolation, or baseline comparison. The `--max-mean` limit is useful as a smoke test, but it remains sensitive to CI hardware and load.

## Intended users

The intended user is a library author who wants a few readable, reusable performance scenarios beside the code they measure. Small parsers, transformations, serializers, dispatch operations, and scheduling helpers can share one result format. Experiments that require stronger statistical guarantees should use a full benchmark system.

## What comes next

The project will make benchmark scenarios easier to use from both Python and the command line and will improve the clarity of their results. microbenchmark will remain a lightweight tool for structured measurements rather than become a full statistical benchmarking system.
