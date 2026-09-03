---
title: "Introducing metacode: one grammar for machine-readable comments"
slug: "introducing-metacode"
date: 2025-12-09T09:00:00+03:00
description: "Gives Python tools one shared syntax for machine-readable instructions placed directly in source comments."
author: "muta"
logo: "logo.png"
tags: [python, source-code, comments, metadata, static-analysis, developer-tools]
---

Python tools use many incompatible forms of machine-readable comments: `noqa`, `type: ignore`, `fmt: off`, `pragma: no cover`, and others. Each form needs its own parser, escaping rules, and way to coexist with ordinary text or another directive. As a result, similar commands behave differently, tools may misread one another's comments, and every new analyzer repeats the same parsing work.

Decorators, `typing.Annotated`, and project configuration provide structured metadata at other scopes. They cannot express every instruction directly beside one source expression without changing the code. Inline directives need a shared syntax while leaving their meaning under each tool's control.

[metacode](https://github.com/mutating/metacode) is a Python library for parsing machine-readable instructions from comments through one compact grammar. It defines the common `key: command[arguments]` shape on which source-aware tools can build their own directives. [The release](https://github.com/mutating/metacode/releases/tag/0.0.1) is available on GitHub.

## Syntax and responsibilities

A metacode directive has a key, a command, and optional arguments:

```python
# mutating: ignore[comparison]
# fmt: off
# type: ignore[attr-defined]
```

metacode parses the form `key: command[arguments]` into `ParsedComment` records. Arguments can contain common Python literals and names. Consumers may also opt into receiving other expressions as inert AST nodes; metacode never executes them.

The caller supplies its own key, so unrelated directives on the same line are ignored:

```python
from metacode import parse

comment = "mutating: ignore[comparison] # fmt: off"

print(parse(comment, "mutating"))
#> [ParsedComment(key='mutating', command='ignore', arguments=['comparison'])]
```

Several hash-separated directives can share a physical line, and ordinary text between compatible fragments is preserved. Each tool requests its own key, while the consumer decides how the parsed command should be applied.

metacode only parses a string containing a comment. It does not read files, return source coordinates, attach directives to syntax nodes, or decide whether a command applies to the current line, the next node, a block, or an entire file.

## Why another convention?

metacode is not intended to replace every existing pragma. Some already fit its grammar: [`# type: ignore[code]`](https://typing.python.org/en/latest/spec/directives.html#type-ignore-comments), [`# fmt: off`](https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html#ignoring-sections), and `# isort: skip`. Others do not: bare `# noqa`, `# nosec`, `# pragma: no cover`, and Pylint's `disable=...` syntax need adapters or continued support for their native formats.

Similar approaches already exist. Python's [`doctest` directives](https://docs.python.org/3/library/doctest.html#directives) define a parsed comment language for one domain, while [Go tool directives](https://go.dev/doc/comment#syntax) use a shared namespaced syntax across tools.

Comments place a local instruction beside the relevant code, have no runtime effect, and require no imports. Project configuration is better for broad policy; an inline directive can describe an exception that applies to one expression.

## Use in source-processing tools

Tokenizers and CST libraries locate comments. metacode parses compatible comment contents. A source-aware tool can therefore keep discovery, parsing, and policy as separate stages.

That separation can support several future consumers without prescribing a single framework around them. Until independent tools adopt the same grammar, however, metacode remains a reusable convention and parser—not an ecosystem standard.

## What comes next

The project will expand from parsing individual directives into a bidirectional layer for managing metadata in comments. The main goal is predictable cooperation between tools, preservation of ordinary comments, and a grammar that can be implemented consistently in different environments.
