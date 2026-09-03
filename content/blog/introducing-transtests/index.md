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

There is a subtle but important boundary in 0.0.1. The generator transformer converts explicit generator markers such as `yield_from_it`; it does not turn every ordinary `return` into `yield`. A template without a generator yield point may therefore remain an ordinary function in that mode. The first README example did not make this distinction clearly enough. Async generators are not part of the initial three-way model either.

## What neighboring tools do

[`pytest.mark.parametrize`](https://docs.pytest.org/en/stable/how-to/parametrize.html) can reproduce the three cases if a project supplies its own transformation factory and invocation rules. It remains more universal and is better when tests should exercise independently implemented sync and async APIs rather than generated variants.

[pytest-asyncio](https://pytest-asyncio.readthedocs.io/) teaches pytest to execute coroutine tests and manage asyncio fixtures. [AnyIO's pytest plugin](https://anyio.readthedocs.io/en/stable/testing.html) runs async tests across asyncio and Trio backends. They vary the runtime around an async test; transtests varies the shape of the callable passed to the code under test. The tools are complementary.

[Hypothesis](https://hypothesis.readthedocs.io/) explores input data and shrinks failures. transtests explores a fixed structural dimension of a function template. [unasync](https://github.com/python-trio/unasync) transforms whole async source trees into synchronous code at build time; it is the stronger choice for maintaining two full public APIs.

We found no maintained published pytest plugin aimed at this exact regular/coroutine/generator template contract. That does not make the idea impossible to write locally. It makes the small reusable fixture worthwhile for projects that otherwise repeat the same scaffolding.

## Who needs it

The clearest users are authors of decorators, callback registries, dispatchers, middleware, instrumentation, and metaprogramming tools. These libraries often care not only about what a callback returns, but when its body actually runs.

Combined with an instrument such as [`LockTraceWrapper`](https://pypi.org/project/locklib/), one shared contract can verify that the callback body—not merely the creation of a coroutine or generator object—ran while a lock was held. This is a small example of why callable shape belongs in infrastructure tests.

In a larger testing stack, transtests can remain a narrow guardrail. It does not decide how the surrounding components are composed; it simply keeps three behavior paths from drifting apart.

## What comes next

The next release will make the fixture easier to discover by adding a full usage example to its own docstring. That is a documentation improvement, not a new execution model.

Beyond the published history, the natural work is to make each mode structurally explicit, improve generator diagnostics, normalize invocation, and test interoperability with async runners. Those are directions to validate before expanding the public contract—not promises hidden inside 0.0.1.
