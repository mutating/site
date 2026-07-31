---
title: "Sandboxes are contracts, not containers"
date: 2026-07-09T09:00:00+03:00
description: "Isolation matters because of the guarantees it exposes, not the technology that implements it."
tags: [sandboxes, infrastructure]
---

A sandbox defines which state is visible, which capabilities exist, and what survives execution.

## Specify the boundary

Treat filesystem, network, clock, and process behavior as explicit inputs to verification.

