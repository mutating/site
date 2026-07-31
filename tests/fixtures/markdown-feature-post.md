---
title: "Markdown feature fixture"
date: 2000-01-01T00:00:00Z
description: "Controlled Markdown content used only by the browser test build."
slug: "markdown-feature-fixture"
author: "test-author"
tags:
  - verification
  - testing
---

This article is generated only inside the temporary test site.

## Ordered evidence

1. Observe the result.
2. Record the invariant.
3. Explain the failure.

## Structured signals

> A useful result remains legible after the worker has stopped.

| Signal | Meaning |
| --- | --- |
| pass | The invariant held |
| fail | The evidence needs inspection |

## Executable evidence

```python
def require_evidence(result: dict) -> None:
    """Reject conclusions without a supporting signal."""
    if not result.get("evidence"):
        raise ValueError("Evidence is required.")
```
