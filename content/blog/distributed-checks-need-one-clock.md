---
title: "Distributed checks need one clock"
date: 2026-06-23T09:00:00+03:00
description: "Comparable evidence requires an explicit model of time across workers."
tags: [distributed-systems, time]
---

Wall clocks drift, retries overlap, and events arrive out of order.

## Make time part of the input

Use stable sequence identities and record timing assumptions whenever temporal order affects a verdict.

