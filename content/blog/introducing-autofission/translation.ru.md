---
title: "Представляем autofission: динамический MaxScale для Fission"
description: "Подстраивает предел масштабирования Fission Functions под ёмкость, доступную в Kubernetes-кластере прямо сейчас."
---

[Fission](https://fission.io/) запускает serverless-функции в Kubernetes и масштабирует их вслед за нагрузкой. Однако максимальное число реплик каждой Function задаётся заранее, даже если доступная ёмкость кластера постоянно меняется. Поэтому оператору приходится выбирать между низким пределом, при котором часть ресурсов простаивает, и высоким пределом, который планировщик может оказаться не способен обеспечить.

[HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) и [KEDA](https://keda.sh/docs/2.20/concepts/scaling-deployments/) выбирают число реплик в пределах фиксированного максимума. Cluster Autoscaler и [Karpenter](https://karpenter.sh/docs/concepts/) добавляют узлы. Ни один из них не вычисляет, насколько можно масштабировать одноразовые задачи Fission на уже существующей ёмкости.

[autofission](https://github.com/mutating/autofission) — открытый Python-контроллер, который вычисляет `MaxScale` по доступным CPU, памяти и местам для Pod на каждом узле. В проект входят CLI, контейнер и Helm-описания для непрерывного обновления подключённых функций Fission. [Релиз](https://github.com/mutating/autofission/releases/tag/0.0.1) доступен на GitHub.

## Как работает динамический предел

Исполнитель Fission `newdeploy` создаёт для каждой Function Deployment, Service и HPA. HPA определяет требуемое число реплик, а Autofission оценивает, сколько из них поместится, и записывает это число в `MaxScale`.

Autofission считает CPU, память и места для Pod отдельно на каждом узле и не складывает свободный CPU одной машины с памятью другой. Расчёт также учитывает накладные расходы Fission и уже работающие реплики функции.

Autofission обновляет саму Fission Function, а не сгенерированный HPA. Конкурентное редактирование завершается конфликтом вместо тихой перезаписи, а вычисленный максимум не опускается ниже `MinScale` или одной реплики.

## Подключение, приоритет и ограничения

Управление включается явно и только для Functions с исполнителем `newdeploy`. Их Pods могут использовать низкий непрерывающий приоритет: одноразовая работа уступает место более важной, а не вытесняет её.

Подключение Function — явная операция Kubernetes:

```bash
kubectl label function hello \
  --namespace fission-function \
  autoscaling.fission.io/cluster-capacity=true
```

После удаления метки Autofission перестаёт обновлять эту Function.

Расчёт использует заявленные requests, а не текущую загрузку CPU и памяти. Поэтому «свободная ёмкость» здесь означает незарезервированную scheduler capacity, а не ресурсы внутри чужой резервации.

Расчёт моделирует CPU, память и число Pods, но не все правила scheduler. Каждая Function также оценивается независимо, поэтому одновременные всплески могут перегрузить кластер. Результат — оценка, а не резервация или гарантия размещения.

## Связанные механизмы Kubernetes

[Kueue](https://kueue.sigs.k8s.io/docs/overview/), [Volcano](https://volcano.sh/docs/scheduler/overview/) и [Koordinator](https://koordinator.sh/docs/architecture/overview/) предлагают гораздо более развитые очереди, справедливое распределение и admission control. Они сильнее, когда кластеру нужен новый планировочный слой. Autofission работает через API Fission и меняет одну верхнюю границу.

Ближайший upstream-аналог — [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity), который симулирует, сколько копий Pod specification может быть размещено. Autofission добавляет непрерывное согласование с объектами Function и Environment в Fission и записывает результат в `MaxScale`.

Это подходит для независимых одноразовых задач, которые должны использовать оставшуюся ёмкость общего кластера. Распределённый анализ — один из возможных сценариев, но сам контроллер остаётся общей инфраструктурой Fission.
