---
title: "Introducing cstvis: independent changes to Python source code"
slug: "introducing-cstvis"
aliases: ["/blog/introducing-cstvis/"]
date: 2025-12-17T09:00:00+03:00
description: "Finds every possible change in Python source and applies candidates one at a time without losing formatting or comments."
author: "muta"
logo: "logo-mark.svg"
tags: [python, cst, source-transformation, mutation-testing, static-analysis, developer-tools]
---

Mutation testing checks a test suite by making small changes to a program and seeing whether the tests notice. To do this well, a tool must find every place where a change is possible, then apply and test one change at a time. Ordinary syntax-tree transformers usually rewrite every matching piece in one pass, so they do not provide this separation by themselves.

[LibCST](https://libcst.readthedocs.io/) represents Python code as a tree while preserving its formatting and comments. It can rewrite that tree, but it does not number all possible edits so that a caller can choose exactly one. Complete mutation-testing systems such as [mutmut](https://github.com/boxed/mutmut) also run tests and manage sessions. A smaller library is useful when another system already handles those jobs.

[cstvis](https://github.com/mutating/cstvis) is a Python library built on LibCST for exactly that workflow. It finds and records every place where a requested source-code change can happen, then applies any one of those changes to the original file. [The release](https://github.com/mutating/cstvis/releases/tag/0.0.1) is available on GitHub.

## Discovering independent transformations

cstvis does not replace LibCST's parser or transformation API. It coordinates potential edits so that a caller can enumerate them first and apply a selected one later.

A function registered with `@changer.converter` handles the LibCST node type named by its first annotation. Filters remove ineligible occurrences. `iterate_coordinates()` returns the remaining source ranges, and `apply_coordinate()` applies one range to the untouched module.

```python
from libcst import Add, Subtract
from cstvis import Changer

changer = Changer("total = left + right")

@changer.converter
def replace_add(node: Add, context):
    return Subtract(
        whitespace_before=node.whitespace_before,
        whitespace_after=node.whitespace_after,
    )

coordinate = next(changer.iterate_coordinates())
print(changer.apply_coordinate(coordinate))
#> total = left - right
```

Two additions therefore produce two candidates, each applied separately to the original source.

Context includes the coordinate, the comment on the node's first line, and parsed metacode directives, so converters can use local rules without implementing comment lookup.

## Comparison with other transformation tools

Direct [LibCST codemods](https://libcst.readthedocs.io/en/latest/codemods.html) remain better for complex, multi-pass refactoring.

[Bowler](https://pybowler.io/) and [RedBaron](https://redbaron.readthedocs.io/) provide higher-level lossless refactoring workflows. Standard [`ast.NodeTransformer`](https://docs.python.org/3/library/ast.html#ast.NodeTransformer) is simpler when preserving formatting and comments does not matter.

cstvis specializes in a smaller layer: LibCST transformations with type-driven registration, access to source comments, and independent replay behind a runner-neutral API.

## Intended use

cstvis does not walk directories, store sessions, run tests, or schedule workers. It allows one converter per exact CST node type and always applies coordinates to the original source; another system can treat each coordinate as a unit of work.
