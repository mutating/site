---
title: "Представляем autofission: динамический предел ёмкости для Fission"
description: "Kubernetes-контроллер, превращающий незарезервированную планировочную ёмкость в изменяемый MaxScale для Fission Functions."
---

Репозиторий [autofission](https://github.com/mutating/autofission) появился 19 августа 2026 года. 20 августа я пометил тегом [исходный релиз 0.0.1](https://github.com/mutating/autofission/releases/tag/0.0.1) — небольшой Kubernetes-контроллер для конкретного пробела в модели масштабирования Fission.

В исходный релиз вошли контроллер, CLI, контейнер и Helm-описания, но 0.0.1 не попал на PyPI: первоначальный workflow ожидал тег с другим префиксом. Первый пакет появился в версии 0.0.2. GitHub Release остаётся первым продуктовым рубежом, о котором идёт речь в этой публикации.

## Предел, а не ещё один autoscaler

Исполнитель Fission `newdeploy` уже создаёт для Function отдельные Deployment, Service и HorizontalPodAutoscaler. Он решает, сколько реплик требует текущая нагрузка между `MinScale` и `MaxScale`. Но верхняя граница выбирается заранее.

Фиксированный предел неудобен в общем кластере. Слишком низкое значение оставляет новые или временно свободные узлы без работы. Произвольно высокое позволяет HPA запросить больше Function Pods, чем сможет разместить scheduler.

Autofission замыкает второй контур управления. Он не выбирает желаемое число реплик. Он постоянно превращает незарезервированную Kubernetes requests-ёмкость в динамический `MaxScale` для каждой явно подключённой Fission Function. Fission остаётся владельцем сгенерированного HPA и механизма реакции на спрос.

На каждом reconciliation Autofission читает Functions, Environments, доступные для планирования Nodes и Pods. CPU, memory и Pod slots считаются отдельно на каждом узле, поэтому свободный CPU одной машины не складывается с памятью другой в ложное обещание. Requests одной реплики выводятся из Function и Environment, учитывают Fission fetcher, а уже работающие реплики этой Function возвращаются в её собственный общий бюджет.

Обновление патчит Fission Function как источник истины, а не сгенерированный HPA. Предусловие `resourceVersion` превращает конкурентное редактирование в конфликт вместо тихой перезаписи. Значение никогда не опускается ниже `MinScale` или одной реплики.

## Opt-in, приоритет и честные ограничения

Управляются только помеченные label-ом `newdeploy` Functions. Удаление label прекращает будущие обновления и оставляет последний вычисленный предел. Helm-описания создают низкий непрерывающий runtime PriorityClass: при правильной настройке Fission Function Pods не могут вытеснять обычные workloads и сами уступают место более важным Pods.

Расчёт использует заявленные requests, а не текущую загрузку CPU и памяти. Поэтому «свободная ёмкость» здесь означает незарезервированную scheduler capacity, а не ресурсы внутри чужой резервации.

Версия 0.0.1 намеренно моделирует только CPU, memory и число Pods. Она не воспроизводит quota, storage, accelerators, affinity, topology, architecture или весь Kubernetes scheduler. Каждая Function оценивается так, будто одна может занять весь остаток, поэтому одновременные bursts способны переобещать верхние пределы и оставить Pods в Pending. Минимум в одну реплику или заданный `MinScale` также может превышать доступную ёмкость.

Результат — scheduler-aware оценка, а не резервация или доказательство размещения.

## Место среди Kubernetes-систем

[HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) и [KEDA](https://keda.sh/docs/2.20/concepts/scaling-deployments/) превращают спрос в целевое число реплик, но сохраняют статический максимум. Cluster Autoscaler и [Karpenter](https://karpenter.sh/docs/concepts/) добавляют машины для unschedulable workloads — это направление, противоположное использованию уже существующих узлов. Descheduler перемещает Pods, а PriorityClasses определяют, кто уступит место.

[Kueue](https://kueue.sigs.k8s.io/docs/overview/), [Volcano](https://volcano.sh/docs/scheduler/overview/) и [Koordinator](https://koordinator.sh/docs/architecture/overview/) предлагают куда более богатые системы очередей, fairness, admission и opportunistic resources. Они сильнее, когда кластеру нужен новый планировочный control plane. Autofission намеренно уже: остаётся на уровне API Fission и меняет одну верхнюю границу.

Ближайший upstream-аналог — [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity), который симулирует, сколько копий Pod specification может быть размещено. Autofission добавляет непрерывный Fission-aware reconciliation: на входе семантика Function и Environment, на выходе `MaxScale`.

Этот небольшой слой подходит для независимой одноразовой работы, которая должна расширяться на весь свободный остаток общего кластера. Распределённый анализ — один из возможных потребителей, но сам контроллер остаётся общей инфраструктурой Fission.

## Что дальше

Ближайший план — усилить упаковку и эксплуатационные safeguards. Главное: расчёт будет резервировать место для активных обычных Pods, которые ещё ждут планирования, а Autofission начнёт проверять, что существующие управляемые Function Pods действительно используют задуманный низкий priority class.

Эти изменения делают оценку консервативнее, не притворяясь новым kube-scheduler. Fair sharing между Functions, жёсткие резервации, accelerators и полное совпадение с размещением остаются отдельными вопросами дизайна, а не обещаниями первого релиза.
