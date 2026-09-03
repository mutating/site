---
title: "Introducing simtypes: lightweight runtime checks for Python type hints"
slug: "introducing-simtypes"
date: 2025-10-14T09:00:00+03:00
description: "Checks whether a Python value broadly matches a type hint without recursively validating collections or changing the value."
author: "muta"
logo: "logo-mark.svg"
tags: [python, runtime-types, type-safety, validation, developer-tools]
---

Python type hints describe more than `isinstance()` can check directly: unions and parameterized collections need special handling. Dispatch, registration, and configuration often need only a cheap compatibility test—not recursive validation, coercion, or a schema system.

A local helper gives each project different rules. [Typeguard](https://typeguard.readthedocs.io/en/latest/), [Beartype](https://beartype.readthedocs.io/), and [Pydantic](https://docs.pydantic.dev/latest/concepts/type_adapter/) solve a broader problem through recursive checks, enforcement, or conversion—more than a low-cost predicate needs.

[simtypes](https://github.com/mutating/simtypes) is a dependency-free Python library for this specific check. It exposes one predicate that understands ordinary classes, unions, optionals, `Any`, `None`, and the observable outer type of parameterized annotations. [The release](https://github.com/mutating/simtypes/releases/tag/0.0.1) is available on GitHub.

## Supported runtime checks

The library exposes `check(type, value) -> bool`, with the type argument first:

```python
from simtypes import check

check(int, 42)                    # True
check(int | None, None)           # True
check(list[str], [1, 2, 3])       # True
```

The last result is intentional: `list[str]` checks only whether the value is a list. Ordinary classes use `isinstance`; `Any`, `None`, unions, optionals, and parameterized outer types have explicit rules.

Collection cost therefore does not grow with length, but simtypes cannot validate untrusted nested data or a complete static annotation.

There are no runtime dependencies, decorators, import hooks, coercion, or schemas: callers receive a boolean and decide what it means.

## Comparison with full validation tools

The broader tools above are better for complete validation. [Runtype](https://runtype.readthedocs.io/en/stable/validation.html), for example, checks collections recursively; simtypes deliberately does not.

A local helper may be enough for one call site. simtypes is useful when several libraries must share and test the same policy.

## Intended use

simtypes is for runtime infrastructure that needs a cheap compatibility decision. Dispatch, plugin registration, and configuration can share one rule without adopting a validation framework.

## What comes next

The project will cover more runtime types and optional conversions while keeping the default check cheap and limited to observable runtime properties.
