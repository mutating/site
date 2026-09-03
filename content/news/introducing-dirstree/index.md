---
title: "Introducing dirstree: reusable rules for choosing project files"
slug: "introducing-dirstree"
aliases: ["/blog/introducing-dirstree/"]
date: 2025-09-29T09:00:00+03:00
description: "Selects local source files through reusable extension and exclusion rules and returns ordinary Python Path objects."
author: "muta"
logo: "logo-mark.svg"
logo_frame: false
tags: [python, filesystem, directory-traversal, developer-tools, orchestration]
---

Linters, test tools, and source-code analyzers first need to go through a project directory and decide which files to process. They may want only Python files while ignoring virtual environments, generated output, and Git metadata. When every tool implements those rules separately, different parts of one system can accidentally process different files.

[`os.walk()`](https://docs.python.org/3/library/os.html#os.walk) and [`Path.rglob()`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rglob) traverse files; [PathSpec](https://python-path-specification.readthedocs.io/) handles ignore patterns. A caller must still combine them with extension checks and package the policy for reuse.

[dirstree](https://github.com/mutating/dirstree) is a small Python library that keeps this file-selection policy in one reusable object. It returns matching `pathlib.Path` values one at a time, filtered by filename extensions and patterns such as those used in `.gitignore`. [The release](https://github.com/mutating/dirstree/releases/tag/0.0.1) is available on GitHub.

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

dirstree walks one root and returns files. It accepts gitignore-style patterns but does not read `.gitignore`, reproduce every Git precedence rule, or skip traversal of an excluded directory.

## Comparison with broader filesystem tools

[WalkDir](https://walkdir.readthedocs.io/en/stable/) adds depth and directory filters, [PyFilesystem2](https://pyfilesystem2.readthedocs.io/en/latest/reference/walk.html) supports abstract filesystems, and [wcmatch](https://facelessuser.github.io/wcmatch/) offers richer patterns.

dirstree is for a smaller case: local source trees, `Path` values from end to end, and one place to declare file selection. It makes no speed claim over the standard library and is not a universal filesystem framework.

## Intended users

dirstree is intended for linters, analyzers, codemods, test tools, and build helpers that repeatedly select a defined file set. In a larger system, it can produce the worklist consumed by another component.
