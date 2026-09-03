---
title: "Introducing metacode: one grammar for machine-readable comments"
slug: "introducing-metacode"
date: 2025-12-09T09:00:00+03:00
description: "A proposed shared syntax and parser for the tiny control languages Python tools place inside comments."
author: "muta"
logo: "logo.png"
tags: [python, source-code, comments, metadata, static-analysis, developer-tools]
---

Python tooling has accumulated a family of tiny languages inside comments: `noqa`, `type: ignore`, `fmt: off`, `pragma: no cover`, and many more. Each tool invents another parser, so commands that express similar ideas still behave differently and cannot share infrastructure.

[metacode](https://github.com/mutating/metacode) is a Python library for parsing machine-readable instructions from comments through one compact grammar. Its [first release](https://github.com/mutating/metacode/releases/tag/0.0.1) establishes the common `key: command[arguments]` shape on which source-aware tools can build their own directives.

## One grammar, tool-owned meaning

A metacode directive has a key, a command, and optional arguments:

```python
# mutating: ignore[comparison]
# fmt: off
# type: ignore[attr-defined]
```

Version 0.0.1 parses the form `key: command[arguments]` into `ParsedComment` records. Keys and commands use Python identifiers. Arguments may be identifiers, simple hyphenated names, strings, numbers, booleans, `None`, or an ellipsis. With `allow_ast=True`, a consumer can receive other Python expressions as inert AST nodes; metacode does not execute them.

Several hash-separated directives can share a physical line, and human text between compatible fragments may remain human text. Each tool asks for its own key, so the parser does not assign meaning to the command. Syntax is shared; policy stays with the consumer.

That boundary is central. metacode 0.0.1 parses a comment-shaped string. It does not read a file, return source coordinates, attach a directive to a syntax node, or decide whether a command governs the current line, the next node, a block, or an entire file.

## Why another convention?

The goal is not to erase existing pragmas. Some already fit naturally: [`# type: ignore[code]`](https://typing.python.org/en/latest/spec/directives.html#type-ignore-comments), [`# fmt: off`](https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html#ignoring-sections), and `# isort: skip`. Others do not: bare `# noqa`, `# nosec`, `# pragma: no cover`, and Pylint's `disable=...` syntax need adapters or continued support for their native dialects.

Existing tools can sometimes coexist on a line, but they do so through tool-specific parsing rules. metacode makes composition part of one grammar for tools that choose to adopt it. The opportunity is not to claim the past was wrong; it is to stop every new analyzer from shipping another almost-familiar regular expression.

There is prior art. Python's [`doctest` directives](https://docs.python.org/3/library/doctest.html#directives) already form a small parsed comment language for one domain. [Go tool directives](https://go.dev/doc/comment#syntax) demonstrate a shared namespaced envelope across tools. `typing.Annotated`, decorators, and `[tool.*]` tables in `pyproject.toml` provide structured metadata in other scopes.

Comments remain attractive for local intent: visible beside the exact exception, inert at runtime, and free of imports. Project configuration is better for broad policy; an inline directive is better for explaining why this particular comparison should not be transformed.

## Where metacode fits

Tokenizers and CST libraries answer where a comment lives. metacode answers what a compatible payload says. A source-aware tool can therefore keep discovery, parsing, and policy as separate stages.

That separation can support several future consumers without prescribing a single framework around them. Until independent tools adopt the same grammar, however, metacode remains a reusable convention and parser—not an ecosystem standard.

## What comes next

The next releases will parse several requested keys in one pass, publish a more explicit grammar, and move from reading directives to building and inserting them. Generation must preserve neighbouring tool markers and human comments rather than treating the rest of the line as disposable text.

Later questions are harder than parsing: scope, ownership, validation schemas, unknown directives, formatter-safe movement, and cross-language conformance. Version 0.0.1 does not pretend those questions are solved. It establishes a small, testable place to solve them.
