---
title: "Introducing autofission: dynamic MaxScale for Fission"
slug: "introducing-autofission"
date: 2026-08-21T09:00:00+03:00
description: "Keeps the scaling limit of Fission Functions aligned with the capacity currently available in a Kubernetes cluster."
author: "muta"
logo: "logo.png"
tags: [kubernetes, fission, autoscaling, distributed-computing, infrastructure, scheduling]
---

[Fission](https://fission.io/) runs serverless functions on Kubernetes and scales them with demand. The maximum replica count of each Function is fixed in advance, however, even when the available cluster capacity changes. Operators must therefore choose between a low limit that wastes capacity and a high limit that the scheduler may be unable to satisfy.

[HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) and [KEDA](https://keda.sh/docs/2.20/concepts/scaling-deployments/) determine how many replicas demand requires, but both work within a configured maximum. Cluster Autoscaler and [Karpenter](https://karpenter.sh/docs/concepts/) can add nodes when capacity runs out. Neither approach answers a different question: how far may disposable Fission workloads scale on the nodes that already exist, without reserving those nodes exclusively for them?

[autofission](https://github.com/mutating/autofission) is an open-source Python controller and library that calculates a suitable `MaxScale` from the CPU, memory, and Pod capacity currently available on each node. It provides a controller, CLI, container, and Helm definitions for applying that calculation continuously to opted-in Fission Functions. [The release](https://github.com/mutating/autofission/releases/tag/0.0.1) is available on GitHub.

## How the dynamic limit works

Fission's `newdeploy` executor creates a Deployment, Service, and HorizontalPodAutoscaler for each Function. The HPA keeps the requested replica count between `MinScale` and `MaxScale`.

Autofission handles the other half of that decision: the HPA decides how many replicas demand requires, while Autofission estimates how many can fit. It continuously converts Kubernetes capacity not already reserved by scheduling requests into a dynamic `MaxScale` for each explicitly opted-in Fission Function.

Autofission evaluates CPU, memory, and Pod slots separately on every node. It does not combine spare CPU on one machine with spare memory on another. The calculation also includes Fission's own overhead and replicas of the Function that are already running.

Autofission updates the Fission Function rather than its generated HPA. Concurrent edits fail instead of being overwritten, and the calculated maximum never falls below `MinScale` or one replica.

## Opt-in behavior, priority, and limitations

Only explicitly labelled `newdeploy` Functions are managed. Their Pods can use a low, non-preempting priority class, so disposable work yields to more important workloads instead of evicting them.

Opting a Function in is an explicit Kubernetes operation:

```bash
kubectl label function hello \
  --namespace fission-function \
  autoscaling.fission.io/cluster-capacity=true
```

Removing the label stops future updates to that Function.

The calculation uses declared resource requests, not current CPU or memory utilization. “Spare capacity” therefore means uncommitted scheduler capacity, not borrowed resources inside another workload's reservation.

The calculation models CPU, memory, and Pod count—not every rule understood by the Kubernetes scheduler. It also evaluates each Function against the available budget independently, so simultaneous bursts can still overbook the cluster and leave Pods Pending.

The result is a scheduler-aware estimate, not a reservation or a proof of placement.

## Related Kubernetes controls

Other Kubernetes controls act at different points. The descheduler moves existing Pods, while PriorityClasses decide which workloads take precedence. Neither calculates a Fission Function's scaling limit.

[Kueue](https://kueue.sigs.k8s.io/docs/overview/), [Volcano](https://volcano.sh/docs/scheduler/overview/), and [Koordinator](https://koordinator.sh/docs/architecture/overview/) provide much richer queueing, fairness, and admission control. They are stronger when a cluster needs a new scheduling control plane. Autofission works through the Fission API and changes one upper bound.

The closest upstream analogue is [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity), which simulates how many copies of a Pod specification could be scheduled. Autofission adds continuous reconciliation for Fission Functions and Environments and writes the result to `MaxScale`.

This is useful for independent, disposable work that should use the remaining capacity of a shared cluster. Distributed analysis is one possible use case, but the controller remains general Fission infrastructure.

## What comes next

The main direction is more conservative capacity accounting and safer operation in busy shared clusters. Autofission should make better decisions when other workloads are competing for resources and provide stronger checks around the Functions it manages, while remaining a capacity estimator rather than a replacement for the Kubernetes scheduler.
