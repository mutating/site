---
title: "Introducing denial: a sentinel for omitted values"
slug: "introducing-denial"
date: 2026-01-17T09:00:00+03:00
description: "Lets Python libraries distinguish a missing argument from a deliberate None value."
author: "muta"
logo: "logo.png"
tags: [python, sentinels, api-design, type-safety, developer-tools]
---

Python's `None` is usually enough. Library authors, however, sometimes need to distinguish two different events: the caller explicitly passed `None`, or the caller passed nothing at all. If both cases use `None`, an API may ignore a requested reset as though no value was supplied, making defaults, partial updates, and layered settings ambiguous.

A local `MISSING = object()` solves this inside one module. [`dataclasses.MISSING`](https://docs.python.org/3/library/dataclasses.html#dataclasses.MISSING) and [`attrs.NOTHING`](https://www.attrs.org/en/stable/api.html#attrs.NOTHING) solve it inside their respective frameworks. General sentinel packages provide factories for creating many such values, but they do not standardize one shared marker for cooperating libraries. What is missing here is a small, framework-neutral value with a public type that several packages can import and annotate consistently.

[denial](https://github.com/mutating/denial) is a small Python library that gives the second state a dedicated name and type. It provides `InnerNone` for values that mean “not supplied” inside library code, while ordinary `None` remains available as real input. [The release](https://github.com/mutating/denial/releases/tag/0.0.1) is available on GitHub.

## How InnerNone is used

A common use case is an API where omission means “inherit the current value” while `None` means “clear it explicitly”:

```python
from typing import Union

from denial import InnerNone, InnerNoneType

Timeout = Union[int, None, InnerNoneType]

def resolve_timeout(value: Timeout = InnerNone):
    if value is InnerNone:
        return inherited_timeout()
    return value  # None means explicitly disabled.
```

The same distinction appears in patch APIs, layered configuration, caches, lazy computation, and defaults. `InnerNone` is intended for internal library state and should normally not be returned as application data.

The API is deliberately small: `InnerNone` is the shared value, while `InnerNoneType` can be used in annotations and `isinstance` checks. Identity checks use `value is InnerNone`.

`InnerNone` does not change the semantics of `None`. It is truthy, additional `InnerNoneType` instances can be constructed, and copying or pickling does not preserve the exported object's identity. It is a minimal named sentinel, not a general-purpose sentinel factory.

## When a sentinel factory is a better fit

Established packages such as [sentinel-value](https://sentinel-value.readthedocs.io/) and [sentinels](https://pypi.org/project/sentinels/) offer factories, readable representations, and stronger serialization behavior.

They are better choices when a project needs to create several sentinels or preserve them across serialization. denial instead standardizes one framework-neutral marker and its public type for cooperating libraries.

[PEP 661](https://peps.python.org/pep-0661/), the proposal for a standard sentinel API, has Deferred status. A future standard could provide a common API, while older Python versions and project-specific semantics would still require separate implementations.

## What comes next

The project will make shared and application-defined sentinel values more predictable while keeping the API small. denial will remain focused on one purpose: preventing ambiguous uses of `None` in library internals.
