---
title: "Introducing skelet: typed settings with controlled runtime updates"
slug: "introducing-skelet"
date: 2025-11-11T09:00:00+03:00
description: "Loads typed Python settings from several sources, validates later changes, and reports errors without exposing secrets."
author: "muta"
logo: "logo.png"
tags: [python, configuration, type-safety, validation, secrets, thread-safety]
---

Python applications combine defaults, constructor arguments, environment variables, files, and secrets. Long-running programs may also change settings after startup. If loading and assignment follow different rules, invalid values can reach the application and errors may expose secrets.

[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [Dynaconf](https://www.dynaconf.com/), and [Hydra](https://hydra.cc/docs/intro/) provide powerful loading and override systems. They do not combine per-field source selection with coordinated runtime assignments, explicit conflicts, callbacks, and secret-aware errors in one object.

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

Before storing a value, skelet can convert it, check its type, and run validators. Fields may be read-only, documented, aliased, or declared incompatible with other fields; successful changes can trigger callbacks. Writes to related fields are coordinated, but this is not a database transaction: mutating a stored object bypasses the field, and a failed callback does not undo an assignment.

## Handling secret values

`secret=True` redacts a field from the storage representation and standard skelet errors. It does not encrypt the value, control access, or retrieve secrets from a vault; application code can still expose it.

## Comparison with established configuration tools

Those projects offer richer loading ecosystems: nested models, command-line input, remote secret providers, hierarchical composition, and reloads. [Typed Settings](https://typed-settings.readthedocs.io/en/latest/) is another option for composable loaders and generated CLIs. skelet's narrower specialization is the lifecycle of mutable fields after loading: coordinated writes, explicit conflicts, and callbacks during assignment.

The package is aimed at libraries, services, and long-running tools whose settings are both loaded from several places and legitimately changed after construction. When an immutable startup snapshot is enough, a narrower loader may be the better choice.

## What comes next

The project will simplify typed configuration and give built-in and third-party sources a common extension model, while keeping the storage core compact.
