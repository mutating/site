---
title: "Introducing getsources: recovering Python function source"
slug: "introducing-getsources"
date: 2026-02-26T09:00:00+03:00
description: "Recovers clean, parseable source text from Python function objects, including nested and interactive definitions."
author: "muta"
logo: "logo.png"
tags: [python, introspection, source-code, ast, repl, developer-tools]
---

Mutation, transformation, and introspection tools need to recover source text from a Python function object before they can parse or rewrite it. The standard library handles ordinary modules well, but interactive definitions may be unavailable and nested functions retain indentation from their enclosing scope. Downstream parsing can therefore fail even when the original function is valid Python.

[`inspect.getsource()`](https://docs.python.org/3/library/inspect.html#inspect.getsource) is the standard solution, while [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) and [sourceinspect](https://pypi.org/project/sourceinspect/) cover more interactive environments. Their APIs and output differ, and none makes a project-wide decision about fallback order and indentation normalization. Without a shared adapter, every AST or CST consumer must implement and test that policy itself.

[getsources](https://github.com/mutating/getsources) is a small Python library that provides a consistent API for recovering and normalizing that text. It combines `inspect` with a `dill` fallback and provides a separate operation for removing shared leading indentation. [The release](https://github.com/mutating/getsources/releases/tag/0.0.1) is available on GitHub.

## Source retrieval and normalization

`getsource(function)` asks the standard library first. If `inspect.getsource()` raises `OSError`, it falls back to `dill.source.getsource()`, which can recover ordinary definitions and lambdas from the history of a classic interactive Python session.

`getclearsource(function)` then removes the leading indentation inherited from an enclosing class or function. A nested definition may be valid in its original block but fail when passed directly to `ast.parse()`. The cleaned form preserves the relative indentation of the body while making the fragment usable at top level.

```python
from getsources import getclearsource

class Parser:
    @staticmethod
    def parse(value):
        return int(value)

print(getclearsource(Parser.parse))
#> def parse(value):
#>     return int(value)
```

The returned text can now be parsed without reconstructing the enclosing class.

The library focuses on Python-defined functions, including methods and lambdas. It is not a universal inspector for modules, classes, builtins, C extensions, or arbitrary callable objects.

There is another important limit. A lambda may be accepted as a function object, but getsources does not isolate the lambda expression from the statement around it. Source such as `handler = lambda value: value + 1` may be returned with the surrounding assignment.

## Choosing a source-recovery tool

For ordinary file-backed code, `textwrap.dedent(inspect.getsource(function))` may be all a project needs. `inspect.getsourcelines()` also returns line numbers and supports more kinds of inspectable objects, while direct use of [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) exposes more recovery controls.

[sourceinspect](https://pypi.org/project/sourceinspect/) supports more interactive shells and exposes a broader inspection API. [executing](https://github.com/alexmojaki/executing) and [stack-data](https://github.com/alexmojaki/stack_data) are stronger when inspection starts from a runtime frame or traceback. [cloudpickle](https://github.com/cloudpipe/cloudpickle) solves a different problem: moving executable behavior between processes rather than recovering readable source.

Interactive recovery remains best-effort. Dynamic `exec` or `eval` code may have no trustworthy provenance, history-based lambda matching can be ambiguous, and source on disk may no longer match the loaded code object. This is source recovery, not bytecode decompilation.

## Intended use

The package is aimed at AST- and CST-based libraries whose users may pass a function object from a module, nested scope, or classic REPL. It gives those consumers one source API instead of exposing the differences between inspection environments.

## What comes next

The project will improve recovery for ambiguous or difficult function definitions and provide more reliable source identities to downstream tools, while keeping the public API focused on source retrieval.
