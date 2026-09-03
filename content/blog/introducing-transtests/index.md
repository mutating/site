---
title: "Introducing transtests: testing three kinds of Python functions"
slug: "introducing-transtests"
date: 2026-03-31T09:00:00+03:00
description: "Runs one pytest behavior check against regular, asynchronous, and generator forms of the same Python function template."
author: "muta"
logo: "logo.png"
tags: [python, pytest, testing, async, generators, code-generation, developer-tools]
---

Python tools that inspect, wrap, or transform callables usually need to support several kinds of functions. A decorator may work on a regular function but acquire a lock at the wrong time, skip instrumentation, or handle exceptions differently when execution is deferred until a coroutine is awaited or a generator is iterated. Copying the same behavioral test three times makes those cases easy to maintain inconsistently.

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) can run several cases, but the project must still create each callable form and invoke it correctly. [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) and [AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) execute asynchronous tests; they do not derive synchronous, coroutine, and generator variants from one source template. A dedicated fixture is needed to test one behavior across all three forms without duplicating the transformation and invocation logic.

[transtests](https://github.com/mutating/transtests) is a small Python testing library and pytest plugin for exercising one function template in synchronous, asynchronous, and generator-oriented forms. It provides that compatibility check as a fixture with three separately reported cases. [The release](https://github.com/mutating/transtests/releases/tag/0.0.1) is available on GitHub.

## Parametrizing tests by function type

After installation, pytest discovers the `transformed` fixture automatically. Any test that requests it becomes three separately reported cases: `sync`, `async`, and `generator`.

```python
from asyncio import run
from inspect import iscoroutinefunction, isgeneratorfunction

def test_addition(transformed):
    @transformed
    def add(left, right):
        return left + right

    if iscoroutinefunction(add):
        assert run(add(1, 2)) == 3
    elif isgeneratorfunction(add):
        assert list(add(1, 2)) == [3]
    else:
        assert add(1, 2) == 3
```

Pytest runs this test three times and reports each function type separately.

The fixture returns a decorator. In the synchronous case it returns the template function. In the other cases it asks [transfunctions](https://github.com/mutating/transfunctions) to build coroutine- and generator-oriented variants from the same source template. The test then invokes the result in the appropriate way: directly, through an event loop, or by iteration.

This keeps shared behavior and assertions in one place while preserving ordinary pytest isolation and case IDs. Kind-specific syntax can stay inside the `sync_context`, `async_context`, and `generator_context` markers supplied by transfunctions.

Generator mode has one important limitation: it transforms explicit generator markers such as `yield_from_it`, but does not turn every ordinary `return` into `yield`. A template therefore needs an actual generator point. Async generators are not supported.

## Comparison with related tools

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) can reproduce the three cases if a project supplies its own transformation factory and invocation rules. It remains more universal and is better when tests should exercise independently implemented sync and async APIs rather than generated variants.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) enables pytest to execute coroutine tests and manage asyncio fixtures. [AnyIO's pytest plugin](https://anyio.readthedocs.io/en/stable/testing.html) runs async tests across asyncio and Trio backends. They vary the runtime used for an async test; transtests varies the type of callable passed to the code under test. The tools are complementary.

[Hypothesis](https://hypothesis.readthedocs.io/) varies input data; transtests varies whether the tested callable is regular, asynchronous, or a generator. [unasync](https://github.com/python-trio/unasync) transforms whole source trees and is better suited to maintaining separate sync and async APIs.

## Intended users

The clearest users are authors of decorators, callback registries, dispatchers, middleware, instrumentation, and metaprogramming tools. These libraries often care not only about what a callback returns, but when its body actually runs.

Combined with a tool such as [`LockTraceWrapper`](https://pypi.org/project/locklib/), one shared contract can verify that the callback body—not merely the creation of a coroutine or generator object—ran while a lock was held. In a larger test suite, transtests supplies this compatibility check without prescribing the rest of the test architecture.

## What comes next

The project will make the three execution modes easier to diagnose and use consistently across different async test environments. Documentation and integration will improve, while the library remains focused on verifying one shared behavior across several kinds of Python functions.
