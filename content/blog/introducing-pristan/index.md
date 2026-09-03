---
title: "Introducing pristan: plugins defined by typed functions"
slug: "introducing-pristan"
date: 2026-03-11T09:00:00+03:00
description: "Turns a typed Python function into a complete plugin extension point with discovery, registration, fallback behavior, and result collection."
author: "muta"
logo: "logo.png"
tags: [python, plugins, extensibility, type-safety, architecture, developer-tools]
---

To let separately installed packages extend a Python library, its author must define what plugins can provide, discover them, register their functions, handle the case where none are installed, and combine their results. When those pieces are defined separately, the callable contract is harder to see, type-check, and test. For a small synchronous extension point, the supporting infrastructure can become more complex than the function being extended.

Python entry points solve package discovery, but they do not define a callable contract or how several results should be combined. [Pluggy](https://pluggy.readthedocs.io/en/stable/) provides a mature plugin manager with ordering, wrappers, tracing, and lifecycle controls. A smaller library still has to choose between writing the missing glue itself and adopting a manager whose concepts exceed the needs of one typed synchronous hook.

[pristan](https://github.com/mutating/pristan) is a compact Python plugin library that keeps the extension contract centred on an ordinary typed function. That function serves as the slot, registry, fallback, and call site, while installed plugins are discovered lazily through standard Python entry points. [The release](https://github.com/mutating/pristan/releases/tag/0.0.1) is available on GitHub.

## How function-based slots work

Decorate a host function with `@slot` and it becomes the contract, registry, fallback implementation, and call site. A plugin is another ordinary function decorated with that exact slot:

```python
from pristan import slot

@slot
def serializers(value) -> dict[str, bytes]:
    ...

@serializers.plugin
def text_serializer(value) -> bytes:
    return str(value).encode()

print(serializers(42))
#> {'text_serializer': b'42'}
```

Calling `serializers(value)` invokes every registered plugin in sequence. If no plugin exists, the original function body provides the fallback. The plugin decorator returns the original function, so an implementation can still be tested directly.

The slot's return annotation also defines how results are handled. No annotation discards them, `list[T]` collects values, and `dict[str, T]` collects them under plugin names. pristan checks plugin signatures during registration and return values during execution.

Separately distributed extensions are discovered lazily through standard Python entry points. The first relevant slot operation loads modules from its entry-point group; importing those modules activates their decorators. Discovery cost and import side effects therefore wait until the slot is needed.

## Alternatives and scope

Compared with Pluggy, pristan requires less setup for a narrower case: a synchronous function that calls every registered plugin. Pluggy remains the better choice when lifecycle, ordering, wrappers, or protocol evolution are more important.

[Stevedore](https://docs.openstack.org/stevedore/latest/) provides several manager styles, while [Pluginlib](https://pluginlib.readthedocs.io/) focuses on class-based contracts. In a closed application, a small dictionary and registration decorator may still be enough.

## Limitations

The runtime checks catch many contract mistakes, but they are not a full proof that every possible slot call will suit every plugin. Plugins run one after another, and one exception stops the sequence. The API has no priorities, wrappers, custom result reducers, async dispatch, or plugin removal.

Lazy discovery and registration are protected against concurrent changes, but dispatch is not fully race-free. pristan therefore does not guarantee full thread safety.

pristan is aimed at library authors who want independently packaged extensions without turning every hook into a manager class. In a larger system, such slots can keep optional capabilities outside the core while leaving the surrounding architecture deliberately open.

## What comes next

The project will broaden the kinds of plugin contracts a slot can express while keeping the function-based API compact. Another priority is more predictable lifecycle and concurrency behavior, especially when plugins are discovered, changed, or invoked at the same time.
