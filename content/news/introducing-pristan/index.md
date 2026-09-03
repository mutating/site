---
title: "Introducing pristan: plugins defined by typed functions"
slug: "introducing-pristan"
aliases: ["/blog/introducing-pristan/"]
date: 2026-03-11T09:00:00+03:00
description: "Turns a typed Python function into a complete plugin extension point with discovery, registration, default behavior, and result collection."
author: "muta"
logo: "logo-mark.svg"
tags: [python, plugins, extensibility, type-safety, architecture, developer-tools]
---

A plugin lets an optional package add behavior without changing the main library. Supporting separately installed plugins still requires the library to define what a plugin must do, find installed implementations, decide what happens when none are present, and combine their results. For one simple extension point, that infrastructure can become more complex than the function being extended.

Python entry points—the package metadata commonly used to advertise installed plugins—handle discovery but not function signatures or result collection. [Pluggy](https://pluggy.readthedocs.io/en/stable/) supplies a mature plugin manager, but many projects do not need all of its controls for one ordinary function call.

[pristan](https://github.com/mutating/pristan) is a compact Python plugin library built around an ordinary function with type hints. The same function defines the plugin contract, stores registered plugins, provides default behavior, and calls the plugins. Separately installed plugins are found through standard Python entry points only when first needed. [The release](https://github.com/mutating/pristan/releases/tag/0.0.1) is available on GitHub.

## How function-based slots work

Decorate a host function with `@slot` and it defines the requirements, stores registrations, provides default behavior, and becomes the call site. A plugin is another ordinary function registered through that slot:

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

Calling the host function invokes every plugin in sequence; if none exists, its original body provides the default behavior. Each plugin remains directly callable for isolated tests.

The slot's return annotation also defines how results are handled. No annotation discards them, `list[T]` collects values, and `dict[str, T]` collects them under plugin names. pristan checks plugin signatures during registration and return values during execution.

Installed extensions are discovered lazily through Python entry points. Their modules are imported only when a relevant slot is first used.

## Alternatives and scope

Compared with Pluggy, pristan requires less setup for a narrower case: an ordinary function that calls every registered plugin. Pluggy remains the better choice when detailed control over plugin lifetime, call order, wrappers, or protocol changes is more important.

[Stevedore](https://docs.openstack.org/stevedore/latest/) offers several manager styles, while [Pluginlib](https://pluginlib.readthedocs.io/) uses class-based contracts. A closed application may need only a dictionary and registration decorator.

## Limitations

Checks made while the program runs catch many contract mistakes, but they cannot prove that every call suits every plugin. Plugins run in sequence, one exception stops the call, and the API does not manage priorities, wrappers, custom ways to combine results, asynchronous calls, or plugin removal.

Discovery and registration are protected against simultaneous changes from different threads, but calling plugins is not completely protected. pristan therefore does not guarantee full thread safety.

pristan is for library authors who want independently packaged extensions without a manager class for every hook. Its slots can keep optional capabilities outside the core without defining the surrounding architecture.

## What comes next

The project will support more plugin contracts and make lifecycle and concurrency behavior more predictable without expanding the compact function-based API.
