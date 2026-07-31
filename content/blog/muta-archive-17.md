---
title: "Retries need budgets and identities"
date: 2026-06-04T09:00:00+03:00
description: "Named attempts make retry behavior inspectable instead of recursive."
author: "muta"
tags: [retries, agents]
---

Every retry should know which attempt it is and how much uncertainty remains acceptable. Without identity and budget, recovery becomes an unbounded second system.
