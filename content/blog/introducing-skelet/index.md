---
title: "Introducing skelet: configuration as controlled live state"
slug: "introducing-skelet"
date: 2025-11-11T09:00:00+03:00
description: "Typed settings from ordered sources, with guarded runtime changes, field conflicts, and secret-aware diagnostics."
author: "muta"
logo: "logo.png"
tags: [python, configuration, type-safety, validation, secrets, thread-safety]
---

Applications rarely receive configuration from one place: defaults, constructor arguments, environment variables, files, and secrets all compete to define the final value. The problem becomes harder in long-running Python programs, where settings may change after startup and still need validation, conflict checks, safe diagnostics, and callbacks.

[skelet](https://github.com/mutating/skelet) is a Python configuration library that treats settings as controlled live state rather than a one-time loading step. Its [first release](https://github.com/mutating/skelet/releases/tag/0.0.1) combines typed fields, ordered sources, guarded assignment, per-field locking, explicit conflicts, and secret-aware error reporting.

## A live settings object

Many configuration tools focus on loading a validated snapshot. skelet 0.0.1 also governs what happens when application code changes a value later.

A `Storage` class declares typed fields. Values can come from constructor arguments, environment variables, TOML—including `pyproject.toml`—JSON, YAML, or memory. Sources are ordered, and the first one containing a value wins. Individual fields can override or extend that shared order.

Before a value is stored, skelet can convert it, check its runtime type, and run user-defined validators. Fields may be read-only, carry documentation, declare aliases, or define explicit conflicts with other fields. A successful change can trigger a callback with the old value, the new value, and the storage object.

The unusual part is that these rules still apply when values change later. Writes to a field are serialized, and fields that conflict share coordination so their relationship is checked consistently. This is not a transactional database: changes inside a stored mutable object bypass the field, and a failed callback does not roll an assignment back. It does, however, give ordinary assignments a clear lifecycle.

## Secrets without mythology

`secret=True` redacts a field from the storage representation and from standard error messages produced by skelet. That is useful, but deliberately modest. The first release does not encrypt the object, erase it from memory, enforce access control, or fetch values from a vault. Application code can still read and leak the original value.

This distinction matters because secret-safe diagnostics and secret management are separate jobs. Version 0.0.1 addresses the former.

## Where it sits among established tools

[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) offers much broader parsing, nested models, command-line input, secret directories, and cloud secret providers. [Dynaconf](https://www.dynaconf.com/) excels at layered environments, merging, reloads, Redis, and Vault. [Typed Settings](https://typed-settings.readthedocs.io/en/latest/) combines several model systems with composable loaders and generated CLIs. [Hydra and OmegaConf](https://hydra.cc/docs/intro/) specialize in hierarchical composition and overrides.

Those projects are broader. skelet's distinctive niche is guarded mutable configuration: per-field source routing, explicit conflicts, coordinated writes, and callbacks connected to the assignment path. That exact combination is uncommon.

The package is aimed at libraries, services, and long-running tools whose settings are both loaded from several places and legitimately changed after construction. When an immutable startup snapshot is enough, a narrower loader may be the better choice.

## What comes next

The first follow-ups will add fields without defaults, dictionary serialization, and command-line arguments as another source. We also plan instance-specific source chains, stronger typing ergonomics so ordinary annotations need less `Field` ceremony, and richer value types such as dates and datetimes.

Further ahead, built-in source implementations can move behind the same plugin boundary as third-party sources. That keeps the storage model compact while leaving room for new inputs—and gives a larger set of tools a configuration layer without forcing every source into the core.
