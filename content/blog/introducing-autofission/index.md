---
title: "Introducing autofission: a dynamic capacity ceiling for Fission"
slug: "introducing-autofission"
date: 2026-08-21T09:00:00+03:00
description: "A Kubernetes controller that converts uncommitted scheduling capacity into a changing MaxScale for Fission Functions."
author: "muta"
logo: "logo.png"
tags: [kubernetes, fission, autoscaling, distributed-computing, infrastructure, scheduling]
---

A Fission Function can scale with demand, but its maximum replica count is fixed in advance—even when the capacity of the Kubernetes cluster changes underneath it. That leaves operators choosing between an artificially low ceiling and a ceiling the scheduler may never be able to satisfy.

[autofission](https://github.com/mutating/autofission) is an open-source Python controller and library that calculates a safer ceiling from the CPU, memory, and Pod capacity currently left on each node. Its [first release](https://github.com/mutating/autofission/releases/tag/0.0.1) includes the controller, CLI, container, and Helm definitions needed to apply that calculation continuously to opted-in Fission Functions.

## A ceiling, not another autoscaler

Fission's `newdeploy` executor already creates a Deployment, Service, and HorizontalPodAutoscaler for a Function. It decides how many replicas current demand needs between `MinScale` and `MaxScale`. The upper bound, however, is chosen in advance.

A fixed ceiling is awkward in a shared cluster. Set it too low and new or temporarily idle nodes remain unused. Set it arbitrarily high and the HPA may ask for more Function Pods than the scheduler can place.

Autofission closes a second control loop. It does not choose the desired replica count. It continuously converts Kubernetes capacity not already reserved by scheduling requests into a dynamic `MaxScale` for each explicitly opted-in Fission Function. Fission remains the owner of the generated HPA and the mechanism that reacts to demand.

Put simply: the HPA decides how many replicas are needed, while Autofission estimates how many can fit.

Autofission evaluates CPU, memory, and Pod slots separately on every node. That avoids promising a replica by combining spare CPU on one machine with spare memory on another. It also accounts for Fission's own overhead and for replicas of the Function that are already running.

Updates go to the Fission Function—the source of truth—rather than its generated HPA. Concurrent edits fail instead of being overwritten, and the calculated ceiling never falls below `MinScale` or one replica.

## Opt-in, priority, and honest limits

Only explicitly labelled `newdeploy` Functions are managed. Their Pods can use a low, non-preempting priority class, so disposable work yields to more important workloads instead of evicting them.

The calculation uses declared resource requests, not current CPU or memory utilization. “Spare capacity” therefore means uncommitted scheduler capacity, not borrowed resources inside another workload's reservation.

The calculation models CPU, memory, and Pod count—not every rule understood by the Kubernetes scheduler. It also evaluates each Function against the available budget independently, so simultaneous bursts can still overbook the cluster and leave Pods Pending.

The result is a scheduler-aware estimate, not a reservation or a proof of placement.

## Where it fits among Kubernetes systems

[HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) and [KEDA](https://keda.sh/docs/2.20/concepts/scaling-deployments/) translate demand into a replica target but keep a static maximum. Cluster Autoscaler and [Karpenter](https://karpenter.sh/docs/concepts/) add machines for unschedulable workloads—the opposite direction from harvesting the nodes already present. The descheduler moves Pods; PriorityClasses decide who yields.

[Kueue](https://kueue.sigs.k8s.io/docs/overview/), [Volcano](https://volcano.sh/docs/scheduler/overview/), and [Koordinator](https://koordinator.sh/docs/architecture/overview/) provide much richer queueing, fairness, and admission control. They are stronger when a cluster needs a new scheduling control plane. Autofission stays at the Fission API layer and changes one upper bound.

The closest upstream analogue is [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity), which simulates how many copies of a Pod specification could be scheduled. Autofission adds continuous Fission-aware reconciliation: Function and Environment semantics in, `MaxScale` out.

That small layer is useful for independent, disposable work that should expand into whatever a shared cluster can spare. Distributed analysis is one possible consumer, but the controller remains general Fission infrastructure.

## What comes next

The immediate plan is to harden packaging and operational safeguards. Most importantly, capacity estimates will reserve room for active ordinary Pods that are still waiting to be scheduled, and Autofission will verify that existing managed Function Pods actually use the intended low-priority class.

These changes make the estimate more conservative without pretending to become the kube-scheduler. Fair sharing between Functions, hard reservations, accelerators, and full placement parity remain separate design questions rather than promises attached to the first release.
