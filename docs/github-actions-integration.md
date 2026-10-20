# GitHub Actions через Power Cells

Платформа связывает GitHub App/OAuth, репозиторий и пул Power Cells. Основной CI
выполняется на одноразовом официальном GitHub runner в Linux ARM64 VM на Mac.
API, кабинет и настройки развёртываются через Ansible. Контракты и ограничения:
[API](../platform/api/docs/github.md), [настройка](github-actions-dev-setup.md),
[проверки](github-actions-testing.md).

## Сценарий пользователя

1. Подключить GitHub во вкладке Actions и установить App на выбранные репозитории.
2. Создать привязку: репозиторий, workflow, job, ветка, личный/организационный пул,
   при необходимости конкретные компьютеры, CPU/RAM/диск и параллельность.
3. Внести сгенерированный routing job и матрицу основного job в workflow, сохранив
   исходные `needs` и permissions. Матрица содержит `workload` на Power Cell и
   `queue_guard` на GitHub-hosted ARM64. Каждый исходный CI step получает условие
   `if: matrix.role == 'workload'`; прежний `if` объединяется с ним через `&&`.
   Guard только наблюдает, исходный CI на нём не выполняется. Все upstream
   dependencies и согласования должны завершиться до routing; готовый runner
   ждёт job не более пяти минут.
4. В защите ветки требовать `powercell_route_<id>` вместе с checks
   `<job> (workload)` и `<job> (queue guard)`: имена основного job меняются.
5. Запустить push/workflow_dispatch выбранной ветки. При наличии ресурсов Mac
   резервируется сразу; затем платформа подготавливает VM/образ/JIT runner.
6. Нет подходящего свободного Mac — немедленный `no_capacity`. Ошибка API, GitHub
   или превышение срока подготовки — ошибка routing. Основной job пропущен,
   весь workflow имеет failure. Автоматического запуска на другом движке нет.
7. Логи, артефакты и результат остаются в GitHub; Actions показывает историю,
   причину отказа, ресурсы и ссылку на конкретный attempt.

Отключение GitHub в Actions также отключает все CI-привязки, созданные этим
пользователем, включая организационные. Повторное подключение не восстанавливает
их автоматически: создайте новые привязки и замените routing job в workflow
сгенерированным YAML. История отключённых привязок сохраняется.

Срок подготовки 10–300 секунд (по умолчанию 90) применяется к выделенному Mac,
ожидания появления свободных ресурсов нет. CPU и память относятся к одному Mac:
ресурсы разных компьютеров для одного job не складываются.

Routing job и queue guard выполняются на GitHub-hosted ARM64 и расходуют hosted
минуты. Guard завершает наблюдение после подтверждённого старта CI; его ожидание
ограничено 310 секундами, включая медленное чтение HTTP: процесс сам завершается
ошибкой до отдельного job timeout в шесть минут. Сам job timeout даёт GitHub
результат `cancelled` и не запускает matrix fail-fast. Timeout CI
на Power Cell остаётся прежним. Роль routing job —
авторизация через OIDC, ожидание готовности выделенного runner и явный failure,
когда Mac недоступен. OAuth callbacks и webhooks сохраняются.

Существующие workflows нужно обновить из нового примера целиком: старый Python
bootstrap сам выбирает hosted runner при потере связи. Поле `fallback`, output
`backend`, выбор политики и ограничения hosted-ресурсов удалены из API и UI.
Пример workflow нужно обновить и после удаления `/commit`, а также после
добавления queue guard: нужны вся матрица, оба условия ролей, `fail-fast: true`,
`max-parallel: 2` и `continue-on-error: false`. Не используйте этот пример как
автоматическое преобразование уже существующей матрицы: он рассчитан на один job.
При частичном rerun старые outputs не используются: повторите routing или все jobs.

## GitHub App и доступы

Первая версия использует runners, зарегистрированные для конкретного
репозитория. Это позволяет ограничить установку App выбранными репозиториями.

| Repository permission | Уровень | Назначение |
| --- | --- | --- |
| Metadata | Read | Идентификаторы и метаданные репозиториев |
| Contents | Read | Чтение workflow для настройки привязки |
| Actions | Read | Workflows, jobs и статусы запусков |
| Administration | Write | Регистрация JIT runners, проверка состояния и удаление |

`Administration: write` требуется GitHub API для repository JIT runners.
Права `Contents: write` и `Workflows: write` не входят в базовую установку:
пользователь применяет сгенерированное изменение workflow сам или через отдельный
доступ разработчика к тестовому репозиторию. Доступ для тестовых push/dispatch
не следует путать с постоянными разрешениями App.

Для runners уровня GitHub organization существует отдельное разрешение
`Self-hosted runners: write`. В первой версии оно не нужно.

App получает события `workflow_job`, `workflow_run` и события изменений установки
и списка доступных репозиториев. Webhook подпись и идентификатор доставки
проверяются; повторы и нарушение порядка событий не должны повторно назначать
ресурсы или менять уже завершённый результат.

Адреса действующих обработчиков:

| Назначение | Адрес |
| --- | --- |
| Homepage | `https://platform.mutating.tech/` |
| OAuth callback | `https://api.mutating.tech/v1/github/oauth/callback` |
| Installation setup | `https://api.mutating.tech/v1/github/installations/callback` |
| Webhook | `https://api.mutating.tech/v1/github/webhook` |

Создание App и installation выполняет владелец GitHub-аккаунта/организации.
Настройка сервера потребует App ID, Client ID, Client Secret, private key,
webhook secret и ключ шифрования сохранённых credentials. Секретные значения
хранятся в конфигурации сервера; они не входят в репозиторий и workflow.

## Выделение и очистка

Выбор Mac и резерв CPU/RAM/диска используют одну транзакцию общего планировщика.
Отказ не создаёт ожидающую VM и не оживает при последующем появлении ресурсов.
Идемпотентность: route + run ID + attempt + instance. Для матрицы нужен отдельный
bootstrap с устойчивым уникальным instance для каждого элемента.

Перед выдачей метки `reserve` атомарно перепроверяет готовность/права и фиксирует
выбор с ограниченным lease. Отдельного `/commit` нет. Потеря ответа приводит
к отмене подготовки; cancellation tombstone предотвращает поздний запуск.
Временные credentials зашифрованы и передаются только назначенному агенту,
не сохраняются в обычных command env/task payload. После завершения удаляются
runner и VM; ресурсы освобождаются после подтверждённой очистки/чистого рестарта.
Webhooks, polling, leases и deadlines обеспечивают восстановление после сбоев.
Ошибка выполняющегося job не приводит к неявному повторному исполнению.

После выдачи метки отдельная hosted leg той же матрицы наблюдает резервацию через
OIDC: только читает существующее состояние, не создаёт VM и не продлевает lease.
Потеря runner до назначения или истечение срока завершает guard ошибкой. GitHub
через `strategy.fail-fast` отменяет ожидающую или выполняющуюся соседнюю
`workload` leg именно этой матрицы текущего attempt. REST cancellation всего run
не используется; разрешение `Actions: write` приложению и workflow не нужно.
Другие jobs и новые attempts не затрагиваются. При обычном выполнении guard
завершается успешно после подтверждения старта; сбой наблюдения не вызывает
автоматический повтор CI. Краткие 429/5xx и транспортные ошибки повторяются
внутри общего срока; отказ авторизации и некорректные ответы завершают guard.

Эта защита зависит от запуска hosted guard и работы планировщика GitHub. При
недоступности самого GitHub нельзя обещать точное время отмены. Сам по себе
`timeout-minutes` основного job ограничивает исполнение, а не ожидание runner.
GitHub по умолчанию может держать self-hosted job в очереди до 24 часов.


## Совместимость и доверие

Поддерживаются push/workflow_dispatch выбранной ветки. PR/reusable workflows
не поддерживаются текущей политикой резервирования. macOS/Xcode и Docker
container/service actions не поддерживаются. Официальный runner image содержит
меньше инструментов, чем GitHub-hosted образ: нужные инструменты устанавливает CI.

Права GitHub repository admin и права расходовать пул платформы проверяются
раздельно и повторно перед назначением. Организации платформы и GitHub могут
различаться. Отзыв прав прекращает назначения и запускает очистку.

JIT runner зарегистрирован на репозиторий: его уникальная метка не является
секретом или границей доступа. Пул доверяет авторам всех jobs репозитория, включая
допущенные PR. OIDC ограничивает reserve API, но не назначение GitHub по меткам:
подключать недоверенный репозиторий к доверенному пулу небезопасно.

## Официальные источники

- [GitHub App: авторизация пользователя и установки](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app).
- [Установка App в выбранные репозитории](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app).
- [Repository JIT runner и требуемые разрешения](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository).
- [Контексты, доступные в runs-on](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability).
- [Правила очереди self-hosted runners](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#routing-precedence-for-self-hosted-runners).
- [GitHub OIDC](https://docs.github.com/en/actions/concepts/security/openid-connect).
- [GitHub-hosted runners: архитектуры и ресурсы](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

- [Matrix fail-fast отменяет queued и выполняющиеся sibling jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations).
