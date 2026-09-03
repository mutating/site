---
title: "Introducing simtypes: runtime type checks kept small"
slug: "introducing-simtypes"
date: 2025-10-14T09:00:00+03:00
description: "One dependency-free predicate for the runtime-observable part of a Python type hint."
author: "muta"
logo: "logo.png"
tags: [python, runtime-types, type-safety, validation, developer-tools]
---

Python type hints describe more than `isinstance()` can check directly, yet many runtime components need only a cheap yes-or-no compatibility test—not recursive validation, coercion, decorators, or an entire schema system.

[simtypes](https://github.com/mutating/simtypes) is a dependency-free Python library for that narrow question. Its [first release](https://github.com/mutating/simtypes/releases/tag/0.0.1) exposes one predicate that understands ordinary classes, unions, optionals, `Any`, `None`, and the observable outer type of parameterized annotations.

## A predicate, not a type system

Version 0.0.1 exposes `check(type, value) -> bool`. The historical argument order is type first:

```python
from simtypes import check

check(int, 42)                    # True
check(int | None, None)           # True
check(list[str], [1, 2, 3])       # True
```

The last result is intentional. In this release, `list[str]` asks whether the value is a list; it does not walk the list to verify every element. Ordinary classes use `isinstance`, while `Any`, `None`, unions, optionals, and the outer runtime type of parameterized annotations receive small explicit rules.

This gives collection checks a predictable cost that does not grow with collection length. It also means simtypes 0.0.1 is not suitable for validating untrusted nested data or proving that a value satisfies the full static annotation. The package rejects that larger promise on purpose.

The result is a checking core of roughly thirty lines, a wheel of about five kilobytes, no runtime dependencies, no decorators, no import hooks, no coercion, and no schema compiler. For supported forms, callers get a boolean and decide what it means.

## The alternatives are more powerful

Python already had excellent runtime checking projects. [Typeguard](https://typeguard.readthedocs.io/en/latest/) can instrument functions and modules and produce detailed failures. [Beartype](https://beartype.readthedocs.io/) supports a broad range of hints and already offers a boolean `is_bearable()` API. [Runtype](https://runtype.readthedocs.io/en/stable/validation.html) recursively validates collection contents. [Pydantic TypeAdapter](https://docs.pydantic.dev/latest/concepts/type_adapter/) validates, converts, serializes, and explains structured data.

Those are better tools when their richer semantics are required. simtypes does not win by covering more of `typing`, and it is not the first boolean checker. Its advantage is a smaller contract: no automatic enforcement, no transformation of the value, and a guaranteed refusal to inspect collection elements in the default model.

A local helper around `isinstance()` and `typing.get_origin()` may still be the best solution for one call site. A package becomes useful when the same modest policy must be tested, versioned, and shared by several libraries.

## The specialization

simtypes is designed as a building block for runtime infrastructure that needs a cheap compatibility decision, not a validation framework of its own. Dispatch, plugin registration, configuration fields, and other mechanisms can depend on the same answer without inheriting a large enforcement system.

That is also its quiet place in a broader toolchain: it can support boundaries between components while remaining intentionally too small to dictate their architecture.

## What comes next

Planned releases will let callers explicitly request strict collection checks, add a few useful special numeric types, and reverse the arguments into the more natural `check(value, type)` order. String deserialization will follow, including dates and datetimes, and later a matching serializer so supported values can make a controlled round trip.

Those additions should remain opt-in layers around the same principle. simtypes will not try to reproduce every rule that a static type checker can reason about. Its job is to answer the runtime question we can observe—and to be honest about the parts we deliberately ignore.
