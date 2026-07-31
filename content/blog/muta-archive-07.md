---
title: "Preserve the failing seed"
date: 2026-05-25T09:00:00+03:00
description: "A captured random seed turns a transient failure into a shared artifact."
author: "muta"
tags: [testing, reproducibility]
---

Property tests and stochastic systems are most valuable when failure can be replayed. Record the seed beside the result before any automatic retry occurs.
