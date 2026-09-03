---
title: "Introducing getsources: getting source code from Python functions"
slug: "introducing-getsources"
aliases: ["/blog/introducing-getsources/"]
date: 2026-02-26T09:00:00+03:00
description: "Recovers clean, parseable source text from Python function objects, including nested and interactive definitions."
author: "muta"
logo: "logo-mark.svg"
tags: [python, introspection, source-code, ast, repl, developer-tools]
---

Tools that analyze or rewrite Python code often receive a live function object but need the readable source text that created it. Finding that text is not always straightforward: a function typed in an interactive shell may not have a normal file, and a function nested inside a class or another function carries indentation that prevents it from being parsed on its own.

[`inspect.getsource()`](https://docs.python.org/3/library/inspect.html#inspect.getsource) is the standard solution; [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) and [sourceinspect](https://pypi.org/project/sourceinspect/) cover more interactive environments. Their APIs and output differ, leaving every AST or CST consumer to choose fallbacks and normalize indentation.

[getsources](https://github.com/mutating/getsources) is a small Python library that provides one consistent way to recover that text and make it ready for parsing. It tries Python's standard `inspect` module first, uses `dill` when needed, and can remove indentation inherited from surrounding code. [The release](https://github.com/mutating/getsources/releases/tag/0.0.1) is available on GitHub.

## Source retrieval and normalization

`getsource(function)` tries `inspect.getsource()` first and falls back to `dill.source.getsource()` after `OSError`, including for definitions stored in a classic REPL history.

`getclearsource(function)` removes indentation inherited from an enclosing class or function while preserving indentation inside the function body.

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

The library handles Python-defined functions, including methods and lambdas—not modules, classes, builtins, or arbitrary callables. A lambda may still be returned with its surrounding statement, such as `handler = lambda value: value + 1`.

## Choosing a source-recovery tool

For ordinary file-backed code, `textwrap.dedent(inspect.getsource(function))` may be all a project needs. `inspect.getsourcelines()` also returns line numbers and supports more kinds of inspectable objects, while direct use of [dill](https://dill.readthedocs.io/en/latest/dill.html#module-dill.source) exposes more recovery controls.

[sourceinspect](https://pypi.org/project/sourceinspect/) covers more interactive shells. [executing](https://github.com/alexmojaki/executing) and [stack-data](https://github.com/alexmojaki/stack_data) are better when inspection begins with a frame or traceback; [cloudpickle](https://github.com/cloudpipe/cloudpickle) moves behavior between processes instead of recovering readable source.

Interactive recovery remains best-effort. Dynamic `exec` or `eval` code may have no trustworthy provenance, history-based lambda matching can be ambiguous, and source on disk may no longer match the loaded code object. This is source recovery, not bytecode decompilation.

## Intended use

The package gives AST- and CST-based libraries one source API for functions from modules, nested scopes, or a classic REPL.
