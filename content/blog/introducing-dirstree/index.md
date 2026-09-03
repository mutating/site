---
title: "Introducing dirstree: focused directory crawling"
slug: "introducing-dirstree"
date: 2025-09-29T09:00:00+03:00
description: "A small Path-first crawler for selecting files with extension filters and familiar ignore patterns."
author: "muta"
logo: "logo.png"
tags: [python, filesystem, directory-traversal, developer-tools, orchestration]
---

Almost every Python tool that works with source code begins by walking a directory, selecting useful file types, and excluding generated or irrelevant paths. When that policy lives in another handwritten loop, it becomes difficult to reuse, test, or pass intact to the next stage of a pipeline.

[dirstree](https://github.com/mutating/dirstree) is a small Python library that turns this policy into a reusable directory walker. Its [first release](https://github.com/mutating/dirstree/releases/tag/0.0.1) produces a lazy stream of `pathlib.Path` objects filtered by extensions and familiar gitignore-style patterns.

## What dirstree does

Give `DirectoryWalker` a string or `Path` root, optionally restrict the accepted file extensions, add exclusion patterns in familiar `.gitignore` syntax, and iterate over the selected files.

That scope is intentionally narrow. Version 0.0.1 walks one root, returns files rather than directories, and filters candidates after discovery. It accepts gitignore-style patterns; it does not automatically read a repository's `.gitignore`, promise every edge case of Git's precedence rules, or prune an excluded directory before entering it.

The value is not a new filesystem primitive. It is a reusable discovery policy. A caller can pass one configured object through a pipeline instead of spreading suffix checks and ignore rules across loops.

## Why not just use the standard library?

Often, you should. [`os.walk()`](https://docs.python.org/3/library/os.html#os.walk) gives excellent control over traversal, errors, pruning, order, and symbolic links. [`Path.rglob()`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rglob) is hard to beat for a compact one-off search. dirstree itself builds on `pathlib` and [PathSpec](https://python-path-specification.readthedocs.io/), rather than replacing them.

Broader libraries also make different trade-offs. [WalkDir](https://walkdir.readthedocs.io/en/stable/) supports depth and separate directory filters. [PyFilesystem2](https://pyfilesystem2.readthedocs.io/en/latest/reference/walk.html) traverses abstract filesystems such as archives and remote backends. [wcmatch](https://facelessuser.github.io/wcmatch/) offers a much richer wildcard language.

dirstree is better suited to a smaller, common case: local source trees, `Path` values from end to end, and one compact place to declare file selection. It makes no speed claim over the standard library and does not attempt to be a universal filesystem framework.

## Who it is for

The immediate audience is authors of linters, analyzers, codemods, test tools, build helpers, and other applications that repeatedly select a well-defined set of files. It is also useful whenever the selection rule deserves a name and a test of its own.

In a larger execution system, a crawler like this can define the worklist that another component processes. That boundary is useful without prescribing how those components will eventually be assembled.

## What comes next

Planned iterations will add callable filters, cooperative cancellation, multiple roots, and combinations of independently configured crawlers. We also intend to support callbacks, optional directory results, and a frozen path worklist for jobs that modify the tree while processing it.

That frozen mode will be a snapshot of selected path names, not a filesystem transaction: contents may still change, paths may disappear, and rollback remains someone else's job. The distinction matters. dirstree should make controlled discovery convenient without pretending that a live filesystem has become immutable.

dirstree starts at the useful minimum: select local files, keep the policy together, and hand ordinary `Path` objects to whatever comes next.
