---
title: "Introducing `denial`: a marker for omitted values"
slug: "introducing-denial"
aliases: ["/blog/introducing-denial/"]
date: 2026-01-17T09:00:00+03:00
description: "Lets Python libraries distinguish a missing argument from a deliberate None value."
author: "muta"
logo: "logo-mark.svg"
logo_frame: false
tags: [python, sentinels, api-design, type-safety, developer-tools]
---

Python's `None` is usually enough. Library authors, however, sometimes need to distinguish two different events: the caller explicitly passed `None`, or the caller passed nothing at all. If both cases use `None`, an API may ignore a requested reset as though no value was supplied, making defaults, partial updates, and layered settings ambiguous.

A local `MISSING = object()` works inside one module; [`dataclasses.MISSING`](https://docs.python.org/3/library/dataclasses.html#dataclasses.MISSING) and [`attrs.NOTHING`](https://www.attrs.org/en/stable/api.html#attrs.NOTHING) belong to their own frameworks. Cooperating libraries instead need one framework-neutral marker with a public type.

[denial](https://github.com/mutating/denial) is a small Python library that gives the second state a dedicated marker value, often called a sentinel. It provides `InnerNone` to mean “not supplied” inside library code, while ordinary `None` remains available as real input. [The release](https://github.com/mutating/denial/releases/tag/0.0.1) is available on GitHub.

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

The same distinction appears in patch APIs, layered configuration, caches, and defaults. `InnerNone` is internal library state, not application data.

The API is deliberately small: `InnerNone` is the shared value, while `InnerNoneType` can be used in annotations and `isinstance` checks. Identity checks use `value is InnerNone`.

Code should compare the marker with `is`, as in the example. It is one shared marker for cooperating libraries, not a tool for creating many unrelated marker values.

## When a sentinel factory is a better fit

[sentinel-value](https://sentinel-value.readthedocs.io/) and [sentinels](https://pypi.org/project/sentinels/) provide factories and stronger serialization, making them better for projects that need several sentinels. denial standardizes one marker and public type for cooperating libraries.

[PEP 661](https://peps.python.org/pep-0661/), the proposal for a standard sentinel API, has Deferred status. A future standard could provide a common API, while older Python versions and project-specific semantics would still require separate implementations.
