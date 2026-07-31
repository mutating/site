---
title: "Determinism is a product feature"
date: 2026-07-18T11:30:00+03:00
description: "Repeatability is not merely a testing concern; it is the interface that lets distributed workers coordinate."
author: "muta"
tags:
  - determinism
  - distributed-systems
  - testing
  - verification
---

Teams often treat determinism as an internal implementation detail. For agent infrastructure, it is closer to a user-facing promise: the same claim, checked against the same state, should produce the same evidence.

## Why repeatability matters

Nondeterministic workers are valuable because they explore. Verification infrastructure has a different job. It establishes a stable surface around that exploration.

When checks are repeatable:

- work can move between a laptop and a distributed fleet;
- failures can be reproduced without preserving the original worker;
- agents can compare candidate solutions against the same standard;
- people can inspect the evidence after the fact.

When checks are not repeatable, every retry quietly changes the question.

## Keep the boundary explicit

The agent may vary its approach. The verifier should make its inputs and rules visible:

```yaml
verification:
  source_revision: "8f3c0ab"
  environment: "linux-arm64"
  fixtures: "v12"
  timeout_seconds: 90
```

This does not make the whole system deterministic. It makes the **judgment boundary** deterministic, which is the part collaborators need to trust.

## Local and distributed are one model

A good check should not acquire a different meaning when it moves to a larger machine. Scale changes throughput, not semantics. That is why portable fixtures, content-addressed inputs, and explicit environment contracts matter more than a clever scheduler.

The reward is operational simplicity: one command surface, as many workers as needed, and evidence that survives the trip.
