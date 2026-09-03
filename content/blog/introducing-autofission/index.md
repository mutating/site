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

[HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) and [KEDA](https://keda.sh/docs/2.20/concepts/scaling-deployments/) choose a replica count within a fixed maximum. Cluster Autoscaler and [Karpenter](https://karpenter.sh/docs/concepts/) add nodes. None calculates how far disposable Fission workloads may scale on capacity that already exists.

[autofission](https://github.com/mutating/autofission) is an open-source Python controller that calculates `MaxScale` from the CPU, memory, and Pod capacity available on each node. It includes a CLI, container, and Helm definitions for continuously updating opted-in Fission Functions. [The release](https://github.com/mutating/autofission/releases/tag/0.0.1) is available on GitHub.

## How the dynamic limit works

Fission's `newdeploy` executor creates a Deployment, Service, and HPA for each Function. The HPA decides how many replicas demand requires; Autofission estimates how many fit and writes that number to `MaxScale`.

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

The calculation models CPU, memory, and Pod count—not every scheduler rule. Each Function is also evaluated independently, so simultaneous bursts may still overbook the cluster. The result is an estimate, not a reservation or proof of placement.

## Related Kubernetes controls

[Kueue](https://kueue.sigs.k8s.io/docs/overview/), [Volcano](https://volcano.sh/docs/scheduler/overview/), and [Koordinator](https://koordinator.sh/docs/architecture/overview/) provide much richer queueing, fairness, and admission control. They are stronger when a cluster needs a new scheduling control plane. Autofission works through the Fission API and changes one upper bound.

The closest upstream analogue is [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity), which simulates how many copies of a Pod specification could be scheduled. Autofission adds continuous reconciliation for Fission Functions and Environments and writes the result to `MaxScale`.

This is useful for independent, disposable work that should use the remaining capacity of a shared cluster. Distributed analysis is one possible use case, but the controller remains general Fission infrastructure.

## What comes next

The project will make capacity estimates more conservative when workloads compete for resources and add stronger checks around managed Functions. It will remain an estimator rather than a replacement for the Kubernetes scheduler.
