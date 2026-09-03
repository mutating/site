---
title: "Introducing metacode: one grammar for machine-readable comments"
slug: "introducing-metacode"
date: 2025-12-09T09:00:00+03:00
description: "Gives Python tools one shared syntax for machine-readable instructions placed directly in source comments."
author: "muta"
logo: "logo-mark.png"
tags: [python, source-code, comments, metadata, static-analysis, developer-tools]
---

Python tools use incompatible machine-readable comments: `noqa`, `type: ignore`, `fmt: off`, `pragma: no cover`, and others. Each has separate parsing and escaping rules, so new analyzers repeat the same work and tools may misread one another's comments.

Decorators, `typing.Annotated`, and project configuration work at other scopes. Inline instructions need a shared syntax that leaves their meaning to each tool.

[metacode](https://github.com/mutating/metacode) is a Python library for parsing machine-readable instructions from comments through one compact grammar. It defines the common `key: command[arguments]` shape on which source-aware tools can build their own directives. [The release](https://github.com/mutating/metacode/releases/tag/0.0.1) is available on GitHub.

## Syntax and responsibilities

A metacode directive has a key, a command, and optional arguments:

```python
# mutating: ignore[comparison]
# fmt: off
# type: ignore[attr-defined]
```

metacode parses `key: command[arguments]` into `ParsedComment` records. Arguments may contain common Python literals and names, or—when enabled—inert AST nodes that metacode never executes.

The caller supplies its own key, so unrelated directives on the same line are ignored:

```python
from metacode import parse

comment = "mutating: ignore[comparison] # fmt: off"

print(parse(comment, "mutating"))
#> [ParsedComment(key='mutating', command='ignore', arguments=['comparison'])]
```

Several hash-separated directives can share a line. Each tool requests its own key and decides what the parsed command means.

metacode parses comment text only. It does not read files, attach coordinates or syntax nodes, or decide the scope of a command.

## Why another convention?

metacode does not replace every pragma. [`# type: ignore[code]`](https://typing.python.org/en/latest/spec/directives.html#type-ignore-comments), [`# fmt: off`](https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html#ignoring-sections), and `# isort: skip` fit its grammar; `# noqa`, `# nosec`, `# pragma: no cover`, and Pylint's syntax require adapters or native support.

Python's [`doctest` directives](https://docs.python.org/3/library/doctest.html#directives) define a comment language for one domain; [Go tool directives](https://go.dev/doc/comment#syntax) share a namespaced syntax across tools.

Comments place a local instruction beside the relevant code, have no runtime effect, and require no imports. Project configuration is better for broad policy; an inline directive can describe an exception that applies to one expression.

## Use in source-processing tools

Tokenizers and CST libraries locate comments. metacode parses compatible comment contents. A source-aware tool can therefore keep discovery, parsing, and policy as separate stages.

Until independent tools adopt the grammar, metacode is a reusable convention and parser rather than an ecosystem standard.

## What comes next

The project will grow into a bidirectional layer for comment metadata while preserving ordinary comments and consistent parsing across environments.
