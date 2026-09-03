---
title: "Introducing denial: a sentinel for omitted values"
slug: "introducing-denial"
date: 2026-01-17T09:00:00+03:00
description: "A tiny named sentinel for distinguishing an omitted value from an explicitly supplied None."
author: "muta"
logo: "logo.png"
tags: [python, sentinels, api-design, type-safety, developer-tools]
---

Python's `None` is usually enough. Library authors, however, sometimes need to distinguish two different events: the caller explicitly passed `None`, or the caller passed nothing at all. Reusing `None` for both makes defaults, patch operations, and layered settings ambiguous.

[denial](https://github.com/mutating/denial) is a tiny Python library that gives the second state a dedicated name and type. Its [first release](https://github.com/mutating/denial/releases/tag/0.0.1) provides `InnerNone` for values that mean “not supplied” inside library code while leaving ordinary `None` available as real input.

## How InnerNone is used

The strongest use case is an API where omission means “inherit the current value” while `None` means “clear it explicitly”:

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

“A second `None`” describes purpose, not Python language semantics. In the first release, `InnerNone` is truthy, additional `InnerNoneType` instances can be constructed, and copying or pickling does not preserve the exported object's identity. It is a named minimal sentinel, not yet a hardened universal sentinel factory.

## Existing alternatives

The one-line local idiom `MISSING = object()` is often the right answer. denial adds a stable import and a dedicated public type, but the first release does not yet improve that idiom's representation, copy, or serialization behavior.

The standard library's [`dataclasses.MISSING`](https://docs.python.org/3/library/dataclasses.html#dataclasses.MISSING), [`attrs.NOTHING`](https://www.attrs.org/en/stable/api.html#attrs.NOTHING), and Pydantic's undefined markers solve the same ambiguity inside their own frameworks. They are not intended as a shared application-level value.

Established packages such as [sentinel-value](https://sentinel-value.readthedocs.io/) and [sentinels](https://pypi.org/project/sentinels/) offer factories, readable representations, and stronger serialization behavior.

denial 0.0.1 does not beat those projects on mechanics. Its specialization is conceptual and deliberately narrow: one framework-neutral marker for library-internal absence, with a memorable name and almost no surface area.

At the time of this release, [PEP 661](https://peps.python.org/pep-0661/), the proposal for a standard sentinel API, was still deferred. A future standard could eventually make part of this small problem universal; older Python versions and project-specific semantics would still need an answer.

## What comes next

The next releases will give the marker a stable readable representation and allow applications to create their own identity-based values. We also plan a unified type annotation for `None` and denial values, clearer separation between identifier spaces, docstrings for sentinels, and stronger singleton options for custom classes.

This progression matters: first name the missing state, then make its behavior robust enough for richer uses. The package can remain tiny while becoming less surprising across diagnostics, typing, copying, and serialization.

denial is intended to remain a small dependency. Its role is simply to prevent ambiguous uses of `None` in libraries that depend on it.
