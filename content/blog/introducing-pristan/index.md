---
title: "Introducing pristan: typed function-first plugins"
slug: "introducing-pristan"
date: 2026-03-11T09:00:00+03:00
description: "A compact plugin system where a typed function is the extension contract, registry, fallback, and call site."
author: "muta"
logo: "logo.png"
tags: [python, plugins, extensibility, type-safety, architecture, developer-tools]
---

The [pristan](https://github.com/mutating/pristan) repository opened on 2 February 2026. On 10 March, [pristan 0.0.1](https://github.com/mutating/pristan/releases/tag/0.0.1) released a compact function-first model for Python plugins.

## A function becomes the extension point

Decorate a host function with `@slot` and it becomes the contract, registry, fallback implementation, and call site. A plugin is another ordinary function decorated with that exact slot:

```python
from pristan import slot

@slot
def serializers(value) -> dict[str, bytes]:
    ...

@serializers.plugin
def json_serializer(value) -> bytes:
    ...
```

Calling `serializers(value)` invokes every registered plugin in sequence. If no plugin exists, the original function body provides the fallback. The plugin decorator returns the original function, so an implementation can still be tested directly.

The slot's return annotation is executable policy. No annotation means results are discarded. `list[T]` collects values, while `dict[str, T]` collects them under plugin names. Actual return values are checked at runtime, and callable shapes are compared during registration. Version 0.0.1 also supports named selections, a maximum plugin count, plugin-local uniqueness, and a host-package version constraint.

Separately distributed extensions are discovered lazily through standard Python entry points. The first relevant slot operation loads modules from its entry-point group; importing those modules activates their decorators. Discovery cost and import side effects therefore wait until the slot is needed.

## Entry points are transport, not protocol

[`importlib.metadata.entry_points()`](https://docs.python.org/3/library/importlib.metadata.html#entry-points) already tells a host which installed objects exist. It does not define their callable contract, aggregation, fallback, naming policy, or result validation. pristan builds those semantics on top rather than replacing the packaging standard.

[Pluggy](https://pluggy.readthedocs.io/en/stable/) is the closest mature relative. It provides hookspec and hookimpl markers, a plugin manager, entry-point loading, ordering, wrappers, first-result hooks, tracing, unregistering, and an enormous production record through pytest. It is more powerful and more mature.

pristan's advantage is a shorter path for a narrower case: a synchronous fan-out hook that should still look like a function. The slot, specification, manager-like registry, fallback, and annotation-driven result policy live in one object. Pluggy remains the better choice when lifecycle, ordering, wrappers, or protocol evolution dominate.

[Stevedore](https://docs.openstack.org/stevedore/latest/) offers rich driver, hook, and extension managers. [Pluginlib](https://pluginlib.readthedocs.io/) validates class-based plugin contracts and versions. [Yapsy](https://yapsy.readthedocs.io/) handles filesystem-discovered stateful plugins. A small dictionary plus a registration decorator is often best in a closed-world application.

Among the systems we reviewed, the distinctive combination in pristan is specific: the host function itself is the callable slot; plugins target that slot directly; installed packages are found through entry points; and the return annotation controls both aggregation and runtime validation. That is not a claim of universal superiority—only a clear niche.

## Boundaries of the first release

Runtime type safety is intentionally partial. Signature compatibility means that a common valid call shape exists unless a stricter signature is supplied; it does not prove that every slot call will suit every plugin. Dispatch is sequential, one exception stops the call, and there are no priorities, wrappers, custom reducers, async semantics, or removal lifecycle.

The first release serializes lazy discovery and registration with a per-slot reentrant lock. It does not yet make every collection and dispatch operation race-free, so “fully thread-safe” would promise too much.

pristan is aimed at library authors who want independently packaged extensions without turning every hook into a manager class. In a larger system, such slots can keep optional capabilities outside the core while leaving the surrounding architecture deliberately open.

## What comes next

Planned releases will simplify version constraints, add run-once plugins, permit removal, and allow a slot to enforce unique or explicitly provided names. Multiple expected signatures and a concise `.one` selection will make strict single-provider contracts clearer.

The registry will also gain stronger locking and snapshot-based dispatch so user code can execute outside internal locks. These are not cosmetic extras: a plugin system earns trust by making discovery and concurrency failures boring.
