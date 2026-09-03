---
title: "Introducing denial: a second None for library internals"
slug: "introducing-denial"
date: 2026-01-17T09:00:00+03:00
description: "A tiny named sentinel for distinguishing an omitted value from an explicitly supplied None."
author: "muta"
logo: "logo.png"
tags: [python, sentinels, api-design, type-safety, developer-tools]
---

Python's `None` is usually enough. Library authors, however, sometimes need to distinguish two different events: the caller explicitly passed `None`, or the caller passed nothing at all.

The [denial](https://github.com/mutating/denial) repository opened on 15 January 2026. The next day, [denial 0.0.1](https://github.com/mutating/denial/releases/tag/0.0.1) gave that second state a name: `InnerNone`.

## A control state, not another spelling

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

The same distinction appears in patch APIs, layered configuration, caches, lazy computation, and defaults. `InnerNone` belongs to the control plane inside a library; it should normally not leak into the caller's value space.

Version 0.0.1 is extremely small: one empty public class, one module-level instance, and no runtime dependencies. `InnerNoneType` provides an ordinary class-based annotation and a target for `isinstance`; identity checks use `value is InnerNone`.

“A second `None`” describes purpose, not Python language semantics. In the first release, `InnerNone` is truthy, additional `InnerNoneType` instances can be constructed, and copying or pickling does not preserve the exported object's identity. It is a named minimal sentinel, not yet a hardened universal sentinel factory.

## A very old problem

The one-line local idiom `MISSING = object()` is often the right answer. denial adds a stable import and a dedicated public type, but the first release does not yet improve that idiom's representation, copy, or serialization behavior.

The standard library's [`dataclasses.MISSING`](https://docs.python.org/3/library/dataclasses.html#dataclasses.MISSING) solves the same ambiguity inside dataclasses, but its documentation discourages general reuse. [`attrs.NOTHING`](https://www.attrs.org/en/stable/api.html#attrs.NOTHING) and Pydantic's internal undefined markers are similarly tied to their own frameworks. An `Enum` member provides stronger singleton and pickle behavior at the price of a more ceremonial declaration.

Established packages such as [sentinel-value](https://sentinel-value.readthedocs.io/), [sentinels](https://pypi.org/project/sentinels/), and [boltons.make_sentinel](https://boltons.readthedocs.io/en/latest/_modules/boltons/typeutils.html) already offer readable representations, factories, registries, or identity-preserving serialization. The older [nulltype](https://pypi.org/project/nulltype/) even explores several meanings of emptiness.

denial 0.0.1 does not beat those projects on mechanics. Its specialization is conceptual and deliberately narrow: one framework-neutral marker for library-internal absence, with a memorable name and almost no surface area.

At the time of this release, [PEP 661](https://peps.python.org/pep-0661/), the proposal for a standard sentinel API, was still deferred. A future standard could eventually make part of this small problem universal; older Python versions and project-specific semantics would still need an answer.

## What comes next

The next releases will give the marker a stable readable representation and allow applications to create their own identity-based values. We also plan a unified type annotation for `None` and denial values, clearer separation between identifier spaces, docstrings for sentinels, and stronger singleton options for custom classes.

This progression matters: first name the missing state, then make its behavior robust enough for richer uses. The package can remain tiny while becoming less surprising across diagnostics, typing, copying, and serialization.

Within a wider family of infrastructure, denial is meant to stay nearly invisible. Its success is not becoming a framework; it is preventing one ambiguous `None` from leaking into the frameworks built above it.
