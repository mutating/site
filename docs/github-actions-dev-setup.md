# Подготовка GitHub для интеграционных тестов

Эта инструкция готовит внешние доступы для
[интеграции Actions](github-actions-integration.md). OAuth/setup callbacks и
вкладка Actions, привязки CI, JIT runners и webhook развёрнуты на публичной
платформе через Ansible. Исполнение настоящего GitHub job пока не проверено.
Для проверки предназначен текущий Mac; установленная сборка Power Cell
содержит поддержку официального образа Actions runner.

## Тестовый репозиторий

Тестовый репозиторий уже создан:
[mutating/powercell-ci-sandbox](https://github.com/mutating/powercell-ci-sandbox).
Для проверки нужны README, ветка `main` и включённые Actions
(Settings → Actions → General). В аккаунте должны быть доступны GitHub-hosted
минуты; private-репозиторий позволяет проверить сценарий с закрытым кодом.

Workflow и тестовый код будут добавлены во время реализации. Существующие
рабочие репозитории для тестов не нужны.

Тестовый App уже создан пользователем в организации `mutating`:

| Параметр | Значение |
| --- | --- |
| App ID | `5346530` |
| Client ID | `Iv23liLU830JEl13dHDz` |
| App slug | `mutating-power-cells-dev` |
| Installation ID, подтверждённый GitHub API | `172111136` |

Проверены подпись App, действующая установка в `mutating`, доступ только к
sandbox и получение ограниченного installation token. Репозиторий private,
ветка `main`. Добавлен отдельный ручной workflow
[`powercell-fallback-smoke.yml`](https://github.com/mutating/powercell-ci-sandbox/blob/main/.github/workflows/powercell-fallback-smoke.yml).
Временные installation tokens после операций отзываются.
Сам параметр installation ID в redirect не доказывает права пользователя:
привязка проверяется после авторизации через GitHub.

Первый [живой запуск fallback](https://github.com/mutating/powercell-ci-sandbox/actions/runs/38926729890)
принят GitHub, но job завершился до выделения runner: GitHub сообщил о неуспешном
платеже либо необходимости поднять spending limit организации. Основной job
пропущен. Перед повтором нужно устранить ограничение в GitHub Billing & plans.
Это также блокирует routing job, который использует GitHub-hosted runner даже
при выборе Power Cell. Никакой CI runner на текущем Mac при этой проверке не
запускался.

16 октября API и кабинет развёрнуты в релиз
`github-ci-20261016-163116` через `infra/playbooks/deploy-platform.yml`, с
резервной копией SQLite и миграцией `0010`. Credentials установлены через
`infra/playbooks/setup-github-actions.yml`; повторный запуск: `changed=0`.
Публичные health/API и Actions отвечают HTTP 200. Повторные настоящие доставки
GitHub `ping` и `workflow_run` получили HTTP 202; Setup URL возвращает HTTP 303
на `https://platform.mutating.tech/actions?github=installed`.

Чтобы продолжить живую проверку:

1. Откройте [Actions](https://platform.mutating.tech/actions), нажмите
   «Подключить GitHub» и завершите OAuth; должен появиться sandbox.
2. Устраните ограничение billing организации `mutating`.
3. Создайте тестовую привязку к текущему Mac. Для небольшой проверки достаточно
   1 CPU и 2 GiB RAM; сеть изолята должна быть разрешена политикой компьютера.
4. Примените сгенерированный workflow и проверьте выполнение на Power Cell,
   затем fallback при отсутствии подходящей свободной машины.

## GitHub App для разработки

Для App организации `mutating` откройте
[New GitHub App](https://github.com/organizations/mutating/settings/apps/new)
или Settings организации → Developer settings → GitHub Apps → New GitHub App.
Нужны права владельца организации или разрешение управлять её Apps.
Имя должно быть уникальным в GitHub, например `Mutating Power Cells Dev`.

| Поле | Значение |
| --- | --- |
| Homepage URL | `https://platform.mutating.tech/` |
| Redirect URI (Callback URL в прежнем интерфейсе) | `https://api.mutating.tech/v1/github/oauth/callback` |
| Setup URL | `https://api.mutating.tech/v1/github/installations/callback` |
| Webhook URL | `https://api.mutating.tech/v1/github/webhook` |
| Expire user authorization tokens | Включено |
| Request user authorization during installation | Выключено; вход начнётся из кабинета |
| Enable Device Flow | Выключено |
| Webhooks: Active | Включено |
| SSL verification | Включено |
| Where can this GitHub App be installed? | Only on this account для тестового App |

`Webhook secret` — отдельная случайная строка. Можно создать её командой
`openssl rand -hex 32`. Сохраните значение в локальном файле конфигурации,
описанном ниже, и вставьте то же значение в GitHub.

Разрешения **Repository permissions** для тестового App:

| Разрешение | Уровень | Для чего |
| --- | --- | --- |
| Metadata | Read-only | Добавляется GitHub автоматически |
| Administration | Read & write | Регистрация и очистка JIT runners |
| Contents | Read & write | Добавление тестового кода и workflow |
| Actions | Read & write | Запуск, отмена и проверка тестовых workflow |
| Workflows | Read & write | Изменение файлов `.github/workflows` |

Organization permissions и Account permissions оставьте без дополнительных
разрешений. Subscribe to events: **Workflow job** и **Workflow run**.

Права записи Contents / Workflows и запуска Actions нужны для автоматических
проверок в sandbox. Для обычной установки платформы базовый набор уже:
Contents read, Actions read, Administration write, Metadata read. Production App
должен быть отдельным либо иметь соответствующий набор разрешений; тестовые
права записи не обязательны для пользователей платформы.

Нажмите Create GitHub App. В настройках App:

1. Сохраните App ID, Client ID и ссылку на страницу App.
2. Создайте Client Secret.
3. Откройте Credentials → Key pairs (вкладка рядом с Client secrets), создайте
   ключевую пару и сохраните её private key в `.pem`. В прежнем интерфейсе этот
   раздел назывался Private keys, а кнопка — Generate a private key.
4. В Install App выберите Only select repositories и только sandbox.

Callbacks и webhook уже развёрнуты. Откройте Actions в кабинете и нажмите подключение
GitHub. OAuth проверяет текущую сессию и одноразовый `state`; ручная установка
App без `state` возвращает в кабинет и не связывает произвольную установку
с аккаунтом платформы.

## Локальная конфигурация

Каталог на текущем Mac уже подготовлен с правами `0700`:

```text
/Users/pomponchik/.config/mutating/github-actions-dev/
```

Сохраните private key под именем `app.pem`, а рядом создайте `app.env`:

```dotenv
THRONG_GITHUB_APP_ID=<App ID>
THRONG_GITHUB_APP_SLUG=mutating-power-cells-dev
THRONG_GITHUB_CLIENT_ID=<Client ID>
THRONG_GITHUB_CLIENT_SECRET=<Client Secret>
THRONG_GITHUB_WEBHOOK_SECRET=<Webhook secret>
THRONG_GITHUB_PRIVATE_KEY_PATH=/Users/pomponchik/.config/mutating/github-actions-dev/app.pem
THRONG_GITHUB_TOKEN_KEY=<persistent Fernet key>
THRONG_GITHUB_PUBLIC_API_URL=https://api.mutating.tech
THRONG_GITHUB_DASHBOARD_URL=https://platform.mutating.tech
```

API использует эти параметры. Все credentials и постоянный ключ шифрования
нужны вместе; без них вкладка скрыта, частичная конфигурация останавливает
старт сервера. `TOKEN_KEY` создаётся через `Fernet.generate_key()` и сохраняется
между рестартами: его замена делает сохранённые OAuth-токены нечитаемыми.
В текущем локальном `app.env` уже сохранены необходимые значения. Отдельный
случайный webhook secret синхронизирован с настройками тестового App через
GitHub API. Установите права `0600` на оба файла:

```sh
chmod 600 /Users/pomponchik/.config/mutating/github-actions-dev/app.pem \
  /Users/pomponchik/.config/mutating/github-actions-dev/app.env
```

В чат достаточно передать ссылку на sandbox и ссылку на App. Значения секретов
остаются в указанных файлах. Дополнительный персональный GitHub token или
приглашение отдельного аккаунта разработчика для этого способа не требуются:
автоматизация получит временный installation token тестового App.

При развёртывании credentials устанавливаются отдельно на сервер. Для живого
цикла нужны доступный по HTTPS API и зарегистрированный в нём Mac. Обновление
агента, создание тестового workflow и проверка обоих вариантов исполнения входят
в работу по интеграции.

Источники:

- [Регистрация GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
- [Выбор репозиториев при установке](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app).
- [Разрешения для repository JIT runners](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository).
- [Разрешения на изменение workflow через Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents).
- [Разрешения для workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
