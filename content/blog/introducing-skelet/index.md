---
title: "Introducing skelet: typed settings with controlled runtime updates"
slug: "introducing-skelet"
date: 2025-11-11T09:00:00+03:00
description: "Loads typed Python settings from several sources, validates later changes, and reports errors without exposing secrets."
author: "muta"
logo: "logo.png"
tags: [python, configuration, type-safety, validation, secrets, thread-safety]
---

Applications rarely receive configuration from one place: defaults, constructor arguments, environment variables, files, and secrets all compete to define the final value. In a long-running Python program, those settings may also change after startup. If loading and later assignment follow different rules, invalid or conflicting values can enter a running application, callbacks may observe inconsistent state, and diagnostics may expose secrets.

[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [Dynaconf](https://www.dynaconf.com/), and [Hydra](https://hydra.cc/docs/intro/) provide powerful loading, validation, layering, and override systems. Their main abstractions do not combine per-field source selection with coordinated mutable assignments, explicit conflicts, callbacks, and secret-aware errors in one object. Applications that need those guarantees after startup otherwise have to build a second configuration lifecycle around the loader.

[skelet](https://github.com/mutating/skelet) is a Python configuration library for settings that can change after they are loaded. It combines typed fields, ordered sources, guarded assignment, per-field locking, explicit conflicts, and secret-aware error reporting. [The release](https://github.com/mutating/skelet/releases/tag/0.0.1) is available on GitHub.

## Validated runtime updates

A `Storage` class declares typed fields. Values can come from constructor arguments, environment variables, TOML—including `pyproject.toml`—JSON, YAML, or memory. Sources are ordered, and the first one containing a value wins. Individual fields can override or extend that shared order.

```python
from skelet import EnvSource, Field, Storage, TOMLSource

class Settings(
    Storage,
    sources=[
        EnvSource(prefix="my_app_"),
        TOMLSource("pyproject.toml", table="tool.my_app"),
    ],
):
    retries: int = Field(3, validation=lambda value: value >= 0)
    api_token: str = Field("", secret=True)

settings = Settings()
settings.retries = 5       # The same checks apply after loading.
settings.retries = -1      # Raises ValueError; 5 remains stored.
```

Here environment variables take priority over the TOML table, which in turn takes priority over field defaults. A later assignment still passes through the field's type and value checks.

Before a value is stored, skelet can convert it, check its runtime type, and run user-defined validators. Fields may be read-only, carry documentation, declare aliases, or define explicit conflicts with other fields. A successful change can trigger a callback with the old value, the new value, and the storage object.

These rules also apply when values change later. Writes to a field are serialized, and conflicting fields are coordinated so their relationship is checked consistently. This is not a transactional database: changes inside a stored mutable object bypass the field, and a failed callback does not roll an assignment back. Ordinary field assignments still follow a defined validation and notification sequence.

## Handling secret values

`secret=True` redacts a field from the storage representation and from standard error messages produced by skelet. It does not encrypt the object, erase it from memory, enforce access control, or fetch values from a vault. Application code can still read and expose the original value.

This distinction matters because secret-safe diagnostics and secret management are separate jobs. skelet addresses the former.

## Comparison with established configuration tools

Those projects offer richer loading ecosystems: nested models, command-line input, remote secret providers, hierarchical composition, and reloads. [Typed Settings](https://typed-settings.readthedocs.io/en/latest/) is another option for composable loaders and generated CLIs. skelet's narrower specialization is the lifecycle of mutable fields after loading: coordinated writes, explicit conflicts, and callbacks during assignment.

The package is aimed at libraries, services, and long-running tools whose settings are both loaded from several places and legitimately changed after construction. When an immutable startup snapshot is enough, a narrower loader may be the better choice.

## What comes next

The project will make typed configuration easier to declare and extend across more input sources and value types. Built-in and third-party sources will move toward the same extension model, keeping the storage core compact while supporting a broader configuration layer for related tools.
