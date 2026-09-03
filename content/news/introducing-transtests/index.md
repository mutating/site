---
title: "Introducing transtests: one test for sync, async, and generator functions"
slug: "introducing-transtests"
aliases: ["/blog/introducing-transtests/"]
date: 2026-03-31T09:00:00+03:00
description: "Runs one pytest behavior check against regular, asynchronous, and generator forms of the same Python function template."
author: "muta"
logo: "logo-mark.svg"
tags: [python, pytest, testing, async, generators, code-generation, developer-tools]
---

Python functions can run in different ways. A regular function returns immediately, an asynchronous function finishes when it is awaited, and a generator produces values during iteration. Tools such as decorators and function wrappers often need to support all three. Copying the same behavior test three times makes it easy for the cases to drift apart.

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) runs several cases, while [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) and [AnyIO](https://anyio.readthedocs.io/en/stable/testing.html) run asynchronous tests. None creates and invokes all three callable forms from one source template.

[transtests](https://github.com/mutating/transtests) is a small Python testing library and pytest plugin. It turns one function template into regular, asynchronous, and generator forms, then runs the same test against each form. Pytest reports the three cases separately, so failures remain easy to identify. [The release](https://github.com/mutating/transtests/releases/tag/0.0.1) is available on GitHub.

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

The fixture returns a decorator. For asynchronous and generator cases, [transfunctions](https://github.com/mutating/transfunctions) builds variants from the same template. Shared assertions stay in one test, while pytest preserves separate case IDs.

Generator mode has one important limitation: it transforms explicit generator markers such as `yield_from_it`, but does not turn every ordinary `return` into `yield`. A template therefore needs an actual generator point. Async generators are not supported.

## Comparison with related tools

Plain pytest parametrization is better when sync and async APIs are implemented independently rather than generated from one template.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) and [AnyIO's pytest plugin](https://anyio.readthedocs.io/en/stable/testing.html) vary the async runtime; transtests varies the callable type. The tools are complementary.

[Hypothesis](https://hypothesis.readthedocs.io/) varies input data; transtests varies whether the tested callable is regular, asynchronous, or a generator. [unasync](https://github.com/python-trio/unasync) transforms whole source trees and is better suited to maintaining separate sync and async APIs.

## Intended users

The clearest users are authors of decorators, callback registries, dispatchers, middleware, instrumentation, and metaprogramming tools. These libraries often care not only about what a callback returns, but when its body actually runs.

With [`LockTraceWrapper`](https://pypi.org/project/locklib/), one test can verify that the callback body—not merely coroutine or generator creation—ran while a lock was held. This check can fit into a larger test suite without determining its architecture.
