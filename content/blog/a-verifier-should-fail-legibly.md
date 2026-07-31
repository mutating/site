---
title: "A verifier should fail legibly"
date: 2026-06-20T09:00:00+03:00
description: "Infrastructure failure must remain distinguishable from a failed claim."
tags: [verification, reliability]
---

A timed-out runner and a disproven invariant are not the same result.

## Separate verdict from machinery

Give infrastructure errors their own state, provenance, and recovery path so they cannot masquerade as evidence.

