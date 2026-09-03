---
title: "Introducing cstvis: independent LibCST transformations"
slug: "introducing-cstvis"
date: 2025-12-17T09:00:00+03:00
description: "Finds every possible change in Python source and applies candidates one at a time without losing formatting or comments."
author: "muta"
logo: "logo.png"
tags: [python, cst, source-transformation, mutation-testing, static-analysis, developer-tools]
---

Mutation testing and other source-analysis tools often need to find every possible edit in a Python file but apply only one candidate at a time. If several edits are applied together, the tool can no longer test or distribute them as independent units. Conventional tree transformers rewrite every matching node in one pass, so they do not directly support this workflow.

[LibCST](https://libcst.readthedocs.io/) preserves formatting and comments, but its visitors and transformers do not by themselves turn every possible edit into an independently addressable operation. Complete mutation systems such as [mutmut](https://github.com/boxed/mutmut) also manage test execution and mutation sessions. A reusable transformation layer is needed when a tool wants LibCST's fidelity and independent changes without adopting an entire mutation runner.

[cstvis](https://github.com/mutating/cstvis) is a Python library built on LibCST for exactly that workflow. It discovers source-aware transformation coordinates and can replay any one of them independently against the original module. [The release](https://github.com/mutating/cstvis/releases/tag/0.0.1) is available on GitHub.

## Discovering independent transformations

cstvis does not replace LibCST's parser or transformation API. It coordinates potential edits so that a caller can enumerate them first and apply a selected one later.

A converter is an ordinary function registered with `@changer.converter`. The type annotation on its first parameter selects the concrete LibCST node type it handles. Filters decide whether an individual occurrence is eligible. `iterate_coordinates()` exposes the surviving candidates as source ranges; `apply_coordinate()` starts from the untouched module and produces a complete source string with only the selected occurrence changed.

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

Unlike a conventional transformer, cstvis treats every matching node as an independent candidate. Two additions in a file produce two candidates, and each candidate is applied separately to the original source.

Context also exposes the candidate coordinate, a comment found on the node's starting line, and parsed metacode directives. Each converter can therefore use source-local rules without implementing comment lookup itself.

## Comparison with other transformation tools

Direct [LibCST visitors, transformers, matchers, and codemods](https://libcst.readthedocs.io/en/latest/codemods.html) are more expressive and remain the right choice for complex, multi-pass refactoring.

[Bowler](https://pybowler.io/) and [RedBaron](https://redbaron.readthedocs.io/) provide higher-level lossless refactoring workflows. Standard [`ast.NodeTransformer`](https://docs.python.org/3/library/ast.html#ast.NodeTransformer) is simpler when preserving formatting and comments does not matter.

cstvis specializes in a smaller layer: LibCST transformations with type-driven registration, access to source comments, and independent replay behind a runner-neutral API.

## Intended use

cstvis handles transformation only. It does not walk directories, store sessions, run tests, or schedule workers. A coordinate can serve as a unit of work for another system.

The model allows one converter per exact CST node type and always applies coordinates to the unchanged original source. These limits make the result of each operation predictable.

## What comes next

The direction is to make independent transformations easier to describe, combine, and reuse. Clearer separation between discovery and execution will help other tools build larger workflows without losing the one-change-at-a-time model.
