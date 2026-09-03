---
title: "Introducing getsources: from live functions back to source"
slug: "introducing-getsources"
date: 2026-02-26T09:00:00+03:00
description: "A small typed boundary over inspect and dill for recovering and normalizing Python function source."
author: "muta"
logo: "logo.png"
tags: [python, introspection, source-code, ast, repl, developer-tools]
---

Mutation, transformation, and introspection tools begin with a deceptively simple question: what source belongs to this live Python function? The standard library answers well for ordinary modules, but interactive definitions and indentation inherited from an enclosing scope make the boundary less predictable.

[getsources](https://github.com/mutating/getsources) is a small Python library that gives syntax tools one place to recover and normalize that text. Its [first release](https://github.com/mutating/getsources/releases/tag/0.0.1) combines `inspect` with a `dill` fallback and provides a separate operation for removing shared leading indentation.

## Two functions, two responsibilities

`getsource(function)` asks the standard library first. If `inspect.getsource()` raises `OSError`, it falls back to `dill.source.getsource()`, which can recover ordinary definitions and lambdas from the history of a classic interactive Python session.

`getclearsource(function)` then removes the leading indentation inherited from an enclosing class or function. A nested definition may be valid in its original block but fail when passed directly to `ast.parse()`. The cleaned form preserves the relative indentation of the body while making the fragment usable at top level.

The first release is intentionally focused on Python-defined functions: regular functions, methods, class methods, static methods, and functions created with lambda expressions. It is not a universal inspector for modules, classes, builtins, C extensions, `functools.partial`, or arbitrary callable objects.

There is another important limit. A lambda may be accepted as a function object, but 0.0.1 does not yet isolate the lambda expression from the statement around it. Source such as `handler = lambda value: value + 1` may be returned with the surrounding assignment.

## A compatibility layer, not a reconstruction engine

The extraction capability belongs primarily to Python's [`inspect`](https://docs.python.org/3/library/inspect.html#retrieving-source-code) and [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source). Direct dill exposes more controls, while `inspect.getsourcelines()` can return line numbers and supports a broader range of inspectable objects. For ordinary file-backed code, `textwrap.dedent(inspect.getsource(function))` may be all a project needs.

[sourceinspect](https://pypi.org/project/sourceinspect/) is the closest earlier wrapper, with adapters for more shells and APIs resembling `getsourcelines()` and `getsourcefile()`. [executing](https://github.com/alexmojaki/executing) and [stack-data](https://github.com/alexmojaki/stack_data) are stronger when the starting point is a live frame or traceback. `ast.get_source_segment()` extracts a precise fragment only after source and an AST node are already available. [cloudpickle](https://github.com/cloudpipe/cloudpickle) transfers executable behavior to another process, but does not recover readable source for transformation.

getsources is better only at its chosen boundary: keep `inspect` as the predictable primary path, use interactive-history recovery as a fallback, normalize indentation, and give downstream tools one function-focused contract.

Interactive recovery remains best-effort. Dynamic `exec` or `eval` code may have no trustworthy provenance, history-based lambda matching can be ambiguous, and source on disk may no longer match the loaded code object. This is source recovery, not bytecode decompilation.

## The specialization

The package is aimed at AST- and CST-based libraries whose users may pass a live function from a module, nested scope, or classic REPL. Keeping recovery and normalization in one small project prevents each consumer from depending directly on environment-specific inspection behavior.

It also creates a quiet seam for a larger toolchain: components above this one can ask for source without needing to know how it was found.

## What comes next

The next functional release will isolate a single lambda expression through the AST and report ambiguity when more than one lambda shares a source statement. It will also add `getsourcehash()`, a compact identity derived from cleaned source, with options to focus on the function body or ignore its docstring.

Those features follow the intended progression: retrieve source first, normalize it second, and only then derive stable identities that caches, registries, and schedulers may use.
