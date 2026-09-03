---
title: "Introducing skelet: configuration as controlled live state"
slug: "introducing-skelet"
date: 2025-11-11T09:00:00+03:00
description: "Typed settings from ordered sources, with guarded runtime changes, field conflicts, and secret-aware diagnostics."
author: "muta"
logo: "logo.png"
tags: [python, configuration, type-safety, validation, secrets, thread-safety]
---

The [skelet](https://github.com/mutating/skelet) repository opened on 8 October 2025. On 10 November, [skelet 0.0.1](https://github.com/mutating/skelet/releases/tag/0.0.1) shipped a large first slice of a configuration system built around one idea: settings do not stop being configuration after startup.

## A live settings object

Many configuration tools focus on loading a validated snapshot. skelet 0.0.1 also governs what happens when application code changes a value later.

A `Storage` class declares typed `Field` descriptors. Values can come from constructor arguments, environment variables, TOML—including a named `pyproject.toml` section—JSON, YAML, or an in-memory source. Sources are ordered, and the first source containing a value wins. A field may replace the class-wide chain with its own sources or use an ellipsis to prepend field-specific sources before falling back to the shared chain.

Before a value is stored, skelet can convert it, check its runtime type, and run user-defined validators. Fields may be read-only, carry documentation, declare aliases, or define explicit conflicts with other fields. A successful change can trigger a callback with the old value, the new value, and the storage object.

The unusual part is how those rules stay connected during mutation. Writes are serialized with per-field mutexes, while directly conflicting fields share a lock so that their pairwise invariant is checked against coordinated state. This is not a full transactional database—reads are not locked by default, mutations inside a stored list bypass the descriptor, and a failing post-write callback does not roll the value back—but it gives ordinary assignments a real lifecycle.

## Secrets without mythology

`secret=True` redacts a field from the storage representation and from standard error messages produced by skelet. That is useful, but deliberately modest. The first release does not encrypt the object, erase it from memory, enforce access control, or fetch values from a vault. Application code can still read and leak the original value.

This distinction matters because secret-safe diagnostics and secret management are separate jobs. Version 0.0.1 addresses the former.

## Where it sits among established tools

[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) offers much broader parsing, nested models, command-line input, secret directories, and cloud secret providers. [Dynaconf](https://www.dynaconf.com/) excels at layered environments, merging, reloads, Redis, and Vault. [Typed Settings](https://typed-settings.readthedocs.io/en/latest/) combines several model systems with composable loaders and generated CLIs. [Hydra and OmegaConf](https://hydra.cc/docs/intro/) specialize in hierarchical composition and overrides.

skelet 0.0.1 does not surpass these projects in breadth or type-system coverage. Its more distinctive niche is guarded mutable configuration: per-field source routing, explicit bidirectional conflicts, related locks, and callbacks connected to the assignment path. Among the established libraries we reviewed, that exact combination is uncommon.

The package is aimed at libraries, services, and long-running tools whose settings are both loaded from several places and legitimately changed after construction. When an immutable startup snapshot is enough, a narrower loader may be the better choice.

## What comes next

The first follow-ups will add fields without defaults, dictionary serialization, and command-line arguments as another source. We also plan instance-specific source chains, stronger typing ergonomics so ordinary annotations need less `Field` ceremony, and richer value types such as dates and datetimes.

Further ahead, built-in source implementations can move behind the same plugin boundary as third-party sources. That keeps the storage model compact while leaving room for new inputs—and gives a larger set of tools a configuration layer without forcing every source into the core.
