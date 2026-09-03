---
title: "Introducing dirstree: focused directory crawling"
slug: "introducing-dirstree"
date: 2025-09-29T09:00:00+03:00
description: "Selects local source files through reusable extension and ignore rules and returns ordinary Python Path objects."
author: "muta"
logo: "logo.png"
tags: [python, filesystem, directory-traversal, developer-tools, orchestration]
---

Almost every Python tool that works with source code begins by walking a directory, selecting useful file types, and excluding generated or irrelevant paths. When each tool implements that policy in its own loop, two components can silently process different file sets. The rules are also difficult to reuse, test, or pass intact to the next stage of a pipeline.

[`os.walk()`](https://docs.python.org/3/library/os.html#os.walk) and [`Path.rglob()`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rglob) handle traversal, while [PathSpec](https://python-path-specification.readthedocs.io/) handles ignore patterns. A caller still has to combine them with extension checks and package the resulting policy for reuse. Broader filesystem libraries add capabilities such as remote backends, but that is unnecessary when the real requirement is a portable description of which local source files belong to a job.

[dirstree](https://github.com/mutating/dirstree) is a small Python library that implements this policy as a reusable directory walker. It produces a lazy stream of `pathlib.Path` objects filtered by extensions and familiar gitignore-style patterns. [The release](https://github.com/mutating/dirstree/releases/tag/0.0.1) is available on GitHub.

## What dirstree does

Give `DirectoryWalker` a string or `Path` root, optionally restrict the accepted file extensions, add exclusion patterns in familiar `.gitignore` syntax, and iterate over the selected files.

For example, this walker yields Python files while excluding Git metadata and a virtual environment:

```python
from dirstree import DirectoryWalker

walker = DirectoryWalker(
    ".",
    extensions=[".py"],
    exclude_patterns=[".git", "venv"],
)

for path in walker.walk():
    print(path)
```

That scope is intentionally narrow. dirstree walks one root, returns files rather than directories, and filters candidates after discovery. It accepts gitignore-style patterns but does not read `.gitignore` automatically, reproduce every Git precedence rule, or skip traversal of an excluded directory.

## Comparison with broader filesystem tools

Broader libraries also make different trade-offs. [WalkDir](https://walkdir.readthedocs.io/en/stable/) supports depth and separate directory filters. [PyFilesystem2](https://pyfilesystem2.readthedocs.io/en/latest/reference/walk.html) traverses abstract filesystems such as archives and remote backends. [wcmatch](https://facelessuser.github.io/wcmatch/) offers a much richer wildcard language.

dirstree is for a smaller case: local source trees, `Path` values from end to end, and one place to declare file selection. It makes no speed claim over the standard library and is not a universal filesystem framework.

## Intended users

The immediate audience is authors of linters, analyzers, codemods, test tools, build helpers, and other applications that repeatedly select a well-defined set of files. It also suits projects that want to test file-selection rules independently.

In a larger execution system, a crawler like this can define the worklist that another component processes. dirstree does not prescribe how those components are assembled.

## What comes next

The project will make directory selection more flexible, composable, and reliable for larger or longer-running jobs, including workflows where the filesystem changes during processing. dirstree will remain focused on selecting local paths rather than becoming a general filesystem or transaction system.
