---
title: "Introducing pristan: typed function-first plugins"
slug: "introducing-pristan"
date: 2026-03-11T09:00:00+03:00
description: "A compact plugin system where a typed function is the extension contract, registry, fallback, and call site."
author: "muta"
logo: "logo.png"
tags: [python, plugins, extensibility, type-safety, architecture, developer-tools]
---

Adding third-party extensions to a Python library usually means assembling several separate pieces: a hook specification, a registry or manager, discovery through package metadata, fallback behavior, and rules for collecting results. For a small synchronous extension point, that infrastructure can overshadow the function being extended.

[pristan](https://github.com/mutating/pristan) is a compact Python plugin library that keeps the extension contract centred on an ordinary typed function. Its [first release](https://github.com/mutating/pristan/releases/tag/0.0.1) turns that function into the slot, registry, fallback, and call site while retaining lazy discovery through standard Python entry points.

## How function-based slots work

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

The slot's return annotation also defines how results are handled. No annotation discards them, `list[T]` collects values, and `dict[str, T]` collects them under plugin names. pristan checks plugin signatures during registration and return values during execution.

Separately distributed extensions are discovered lazily through standard Python entry points. The first relevant slot operation loads modules from its entry-point group; importing those modules activates their decorators. Discovery cost and import side effects therefore wait until the slot is needed.

## Entry points are transport, not protocol

[`importlib.metadata.entry_points()`](https://docs.python.org/3/library/importlib.metadata.html#entry-points) already tells a host which installed objects exist. It does not define their callable contract, aggregation, fallback, naming policy, or result validation. pristan builds those semantics on top rather than replacing the packaging standard.

[Pluggy](https://pluggy.readthedocs.io/en/stable/) is the closest mature relative. It offers a full plugin manager with ordering, wrappers, tracing, lifecycle controls, and a long production record through pytest.

pristan's advantage is a shorter path for a narrower case: a synchronous fan-out hook that should still look like a function. The slot, specification, manager-like registry, fallback, and annotation-driven result policy live in one object. Pluggy remains the better choice when lifecycle, ordering, wrappers, or protocol evolution dominate.

[Stevedore](https://docs.openstack.org/stevedore/latest/) provides several manager styles, while [Pluginlib](https://pluginlib.readthedocs.io/) focuses on class-based contracts. In a closed application, a small dictionary and registration decorator may still be enough.

pristan's distinctive combination is specific: the host function is the slot, plugins target it directly, installed packages arrive through entry points, and the return annotation controls aggregation and validation. It is a clear niche rather than a claim of universal superiority.

## Boundaries of the first release

The runtime checks catch many contract mistakes, but they are not a full proof that every possible slot call will suit every plugin. Plugins run one after another, and one exception stops the sequence. The initial API has no priorities, wrappers, custom result reducers, async dispatch, or plugin removal.

Lazy discovery and registration are protected against concurrent changes, but dispatch is not yet fully race-free. Calling the first release “fully thread-safe” would promise too much.

pristan is aimed at library authors who want independently packaged extensions without turning every hook into a manager class. In a larger system, such slots can keep optional capabilities outside the core while leaving the surrounding architecture deliberately open.

## What comes next

Planned releases will simplify version constraints, add run-once plugins, permit removal, and allow a slot to enforce unique or explicitly provided names. Multiple expected signatures and a concise `.one` selection will make strict single-provider contracts clearer.

The registry will also gain stronger locking and snapshot-based dispatch so user code can execute outside internal locks. The goal is predictable behavior when discovery or concurrent registration fails.
