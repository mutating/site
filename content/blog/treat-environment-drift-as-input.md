---
title: "Treat environment drift as input"
date: 2026-06-22T09:00:00+03:00
description: "Changed environments should produce explicit diffs, not mysterious changes in outcome."
tags: [environments, reproducibility]
---

Tools, kernels, services, and configuration all participate in a test result.

## Compare the environment

Fingerprint relevant state and surface drift before interpreting a changed verdict.

