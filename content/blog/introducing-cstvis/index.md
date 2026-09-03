---
title: "Introducing cstvis: one source tree, one change at a time"
slug: "introducing-cstvis"
date: 2025-12-17T09:00:00+03:00
description: "A small LibCST layer for discovering source-aware transformations and materializing them independently."
author: "muta"
logo: "logo.png"
tags: [python, cst, source-transformation, mutation-testing, static-analysis, developer-tools]
---

Source-transformation tools often need to find every possible edit in a Python file but apply only one candidate at a time. Conventional tree transformers are designed to rewrite every matching node in one pass, which makes isolated mutations surprisingly awkward.

[cstvis](https://github.com/mutating/cstvis) is a Python library built on LibCST for exactly that workflow. Its [first release](https://github.com/mutating/cstvis/releases/tag/0.0.1) discovers source-aware transformation coordinates and can replay any one of them independently against the original module.

## From a rewrite to a catalogue

[LibCST](https://libcst.readthedocs.io/) already provides a typed, source-faithful representation of Python. It preserves comments and formatting that a standard AST discards. cstvis does not replace that parser. It adds a coordination layer on top.

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
```

The important difference from a conventional transformer is not a richer tree. It is the one-change-at-a-time contract. Two additions in a file become two independent candidates, each replayed against the original source rather than combined into one rewrite.

Context also exposes the candidate coordinate, a comment found on the node's starting line, and parsed metacode directives. That makes source-local policy available without teaching every converter how to locate comments.

## The alternatives solve larger jobs

Direct [LibCST visitors, transformers, matchers, and codemods](https://libcst.readthedocs.io/en/latest/codemods.html) are more expressive and remain the right choice for complex, multi-pass refactoring.

[Bowler](https://pybowler.io/) and [RedBaron](https://redbaron.readthedocs.io/) provide higher-level lossless refactoring workflows. Standard [`ast.NodeTransformer`](https://docs.python.org/3/library/ast.html#ast.NodeTransformer) is simpler when preserving formatting and comments does not matter.

Full mutation tools such as [Cosmic Ray](https://cosmic-ray.readthedocs.io/) and [mutmut](https://github.com/boxed/mutmut) go much further: they manage mutation sessions, run tests, and optimize execution.

cstvis provides transformation primitives rather than a complete mutation-testing engine. Its advantage is the combination of LibCST fidelity, type-driven registration, source comments, and independent replay behind a small, runner-neutral API.

## The specialization

Version 0.0.1 handles transformation only. It does not walk directories, store sessions, run tests, or schedule workers. A coordinate can later serve as a unit of work for another system.

The initial model deliberately allows one converter per exact CST node type and always applies coordinates to the unchanged original source. Those limits keep each result easy to reason about.

## What comes next

Planned iterations will allow several conversions for the same node type, identify the chosen converter in each coordinate, and separate registration from execution through reusable collectors. Union annotations and shortcuts for strings and numbers will make operator declarations more compact.

After that, collectors will become composable, callbacks will be able to omit unused context, and metadata will travel with conversions. Richer context will expose the full source, character offsets, and the code before and after a node—computed lazily where possible.

The direction is deliberately incremental: first turn possible changes into addressable work, then make those work items easier to describe, combine, and hand to the systems around them.
