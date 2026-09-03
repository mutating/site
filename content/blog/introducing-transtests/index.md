---
title: "Introducing transtests: one test across function shapes"
slug: "introducing-transtests"
date: 2026-03-31T09:00:00+03:00
description: "A focused pytest fixture for checking shared behavior across synchronous, asynchronous, and generator-oriented function templates."
author: "muta"
logo: "logo.png"
tags: [python, pytest, testing, async, generators, code-generation, developer-tools]
---

Python tools that inspect, wrap, or transform callables rarely get to support only one kind of function. A decorator may behave perfectly on a regular function and still fail when execution is deferred until a coroutine is awaited or a generator is iterated. Copying the same behavioral test three times makes those cases easy to let drift apart.

[transtests](https://github.com/mutating/transtests) is a small Python testing library and pytest plugin for exercising one function template in synchronous, asynchronous, and generator-oriented forms. Its [first release](https://github.com/mutating/transtests/releases/tag/0.0.1) packages that compatibility check as a fixture with three separately reported cases.

## A new axis of parametrization

After installation, pytest discovers the `transformed` fixture automatically. Any test that requests it becomes three separately reported cases: `sync`, `async`, and `generator`.

The fixture returns a decorator. In the synchronous case it returns the template function. In the other cases it asks [transfunctions](https://github.com/mutating/transfunctions) to build coroutine- and generator-oriented variants from the same source template. The test then invokes the result in the appropriate way: directly, through an event loop, or by iteration.

This keeps shared behavior and assertions in one place while preserving ordinary pytest isolation and case IDs. Kind-specific syntax can stay inside the `sync_context`, `async_context`, and `generator_context` markers supplied by transfunctions.

There is one important boundary: generator mode transforms explicit generator markers such as `yield_from_it`; it does not turn every ordinary `return` into `yield`. A template therefore needs a real generator point. Async generators are not part of the initial three-way model.

## What neighboring tools do

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) can reproduce the three cases if a project supplies its own transformation factory and invocation rules. It remains more universal and is better when tests should exercise independently implemented sync and async APIs rather than generated variants.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) teaches pytest to execute coroutine tests and manage asyncio fixtures. [AnyIO's pytest plugin](https://anyio.readthedocs.io/en/stable/testing.html) runs async tests across asyncio and Trio backends. They vary the runtime around an async test; transtests varies the shape of the callable passed to the code under test. The tools are complementary.

[Hypothesis](https://hypothesis.readthedocs.io/) explores input data; transtests explores a fixed structural dimension of one function template. [unasync](https://github.com/python-trio/unasync) transforms whole source trees and is better suited to maintaining separate sync and async APIs.

Unlike these neighboring tools, transtests focuses specifically on generating and exercising regular, coroutine, and generator forms from one template. That narrow scope saves projects from rebuilding the same test scaffolding locally.

## Who needs it

The clearest users are authors of decorators, callback registries, dispatchers, middleware, instrumentation, and metaprogramming tools. These libraries often care not only about what a callback returns, but when its body actually runs.

Combined with an instrument such as [`LockTraceWrapper`](https://pypi.org/project/locklib/), one shared contract can verify that the callback body—not merely the creation of a coroutine or generator object—ran while a lock was held. This is a small example of why callable shape belongs in infrastructure tests.

In a larger testing stack, transtests can remain a narrow guardrail. It does not decide how the surrounding components are composed; it simply keeps three behavior paths from drifting apart.

## What comes next

The next steps are clearer mode-specific diagnostics, more consistent invocation, and better interoperability with async runners. The fixture documentation will also gain a complete example so its three-way contract is visible without searching through a separate guide.
