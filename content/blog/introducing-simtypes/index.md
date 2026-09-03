---
title: "Introducing simtypes: lightweight runtime type checks"
slug: "introducing-simtypes"
date: 2025-10-14T09:00:00+03:00
description: "Checks whether a Python value broadly matches a type hint without recursively validating collections or changing the value."
author: "muta"
logo: "logo.png"
tags: [python, runtime-types, type-safety, validation, developer-tools]
---

Python type hints describe more than `isinstance()` can check directly: even a union or a parameterized collection requires special handling. Many runtime components need this answer during dispatch, registration, or configuration, but they only need a cheap yes-or-no compatibility test—not recursive validation, coercion, decorators, or an entire schema system.

A helper based on `isinstance()` and `typing.get_origin()` can cover a few annotations, but every project then defines slightly different rules. [Typeguard](https://typeguard.readthedocs.io/en/latest/), [Beartype](https://beartype.readthedocs.io/), and [Pydantic](https://docs.pydantic.dev/latest/concepts/type_adapter/) solve the broader problem through recursive checks, automatic enforcement, or conversion. That additional behavior is unnecessary when dispatch or plugin registration only needs a predictable, low-cost predicate.

[simtypes](https://github.com/mutating/simtypes) is a dependency-free Python library for this specific check. It exposes one predicate that understands ordinary classes, unions, optionals, `Any`, `None`, and the observable outer type of parameterized annotations. [The release](https://github.com/mutating/simtypes/releases/tag/0.0.1) is available on GitHub.

## Supported runtime checks

The library exposes `check(type, value) -> bool`, with the type argument first:

```python
from simtypes import check

check(int, 42)                    # True
check(int | None, None)           # True
check(list[str], [1, 2, 3])       # True
```

The last result is intentional. `list[str]` asks whether the value is a list; it does not walk the list to verify every element. Ordinary classes use `isinstance`, while `Any`, `None`, unions, optionals, and the outer runtime type of parameterized annotations receive small explicit rules.

This gives collection checks a predictable cost that does not grow with collection length. It also means simtypes is not suitable for validating untrusted nested data or verifying a complete static annotation. That broader form of validation is outside its scope.

The result is a small checker with no runtime dependencies, decorators, import hooks, coercion, or schema compiler. Callers get a boolean and decide what it means.

## Comparison with full validation tools

The broader tools mentioned above are better when complete validation is required. [Runtype](https://runtype.readthedocs.io/en/stable/validation.html), for example, checks collection contents recursively. simtypes deliberately offers a smaller contract: no automatic enforcement, no value transformation, and no inspection of collection elements in its default model.

A local helper around `isinstance()` and `typing.get_origin()` may still be the best solution for one call site. A package becomes useful when the same modest policy must be tested, versioned, and shared by several libraries.

## Intended use

simtypes is designed for runtime infrastructure that needs a cheap compatibility decision rather than a full validation framework. Dispatch, plugin registration, and configuration code can share the same rule without adding an enforcement system or coupling their wider designs.

## What comes next

The project will cover more practical runtime types and optional conversion tasks while preserving a cheap, explicit default check. simtypes will remain focused on properties that can be observed at runtime instead of trying to reproduce a complete static type system.
