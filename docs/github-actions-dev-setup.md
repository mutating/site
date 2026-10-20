# Подготовка GitHub для интеграционных тестов

**Актуальная схема с 19 октября 2026:** только Power Cells. Нет свободного Mac —
немедленный отказ без VM/JIT; ошибки API/подготовки завершают routing и весь workflow
с failure, основной job пропускается. Политика fallback и её UI удалены. Routing
и queue guard остаются hosted: нужны hosted минуты. Требуйте routing, workload
и queue guard checks в защите ветки. Все исходные CI steps должны иметь условие
`matrix.role == 'workload'`; guard только наблюдает.
Существующий YAML необходимо заменить новым примером целиком. Упоминания прежней
схемы и ссылки на её прогоны ниже — история проверок, не текущая инструкция.


Эта инструкция готовит внешние доступы для
[интеграции Actions](github-actions-integration.md). OAuth/setup callbacks и
вкладка Actions, привязки CI, JIT runners и webhook развёрнуты на публичной
платформе через Ansible. Настоящие Power Cell job и hosted fallback проверены
с приватным checkout и загрузкой артефакта; ошибка и отмена job также проверены.
Для проверки предназначен текущий Mac; установленная сборка Power Cell
содержит поддержку официального образа Actions runner.

## Тестовый репозиторий

Исходный тестовый репозиторий был создан в организации:
[mutating/powercell-ci-sandbox](https://github.com/mutating/powercell-ci-sandbox).
17 октября пользователь перенёс его в личный аккаунт:
[pomponchik/powercell-ci-sandbox](https://github.com/pomponchik/powercell-ci-sandbox).
App установлен на новом владельце, OAuth завершён; прежний installation ID
`172111136` относился к исторической установке в `mutating`.
Для проверки нужны README, ветка `main` и включённые Actions
(Settings → Actions → General). В аккаунте должны быть доступны GitHub-hosted
минуты; private-репозиторий позволяет проверить сценарий с закрытым кодом.

Workflow `.github/workflows/ci.yml` и тестовые шаги уже добавлены в sandbox.
Существующие рабочие репозитории для тестов не нужны.

Тестовый App уже создан пользователем в организации `mutating`:

| Параметр | Значение |
| --- | --- |
| App ID | `5346530` |
| Client ID | `Iv23liLU830JEl13dHDz` |
| App slug | `mutating-power-cells-dev` |
| Installation ID, подтверждённый GitHub API | `172451419` |

16 октября проверены подпись App, действующая установка в `mutating`, доступ только к
sandbox и получение ограниченного installation token. Репозиторий private,
ветка `main`. Добавлен отдельный ручной workflow
[`powercell-fallback-smoke.yml`](https://github.com/pomponchik/powercell-ci-sandbox/blob/e14ce0f197919edf4b3f28033bef1882a81f5ab5/.github/workflows/powercell-fallback-smoke.yml).
Временные installation tokens после операций отзываются.
Сам параметр installation ID в redirect не доказывает права пользователя:
привязка проверяется после авторизации через GitHub.

Первый [живой запуск fallback](https://github.com/mutating/powercell-ci-sandbox/actions/runs/38926729890)
был принят GitHub, но job завершился до выделения runner: GitHub сообщил о неуспешном
платеже либо необходимости поднять spending limit организации. Основной job
был пропущен. Это блокировало routing job, который использует GitHub-hosted runner
даже при выборе Power Cell. При проверке 16 октября CI runner на Mac не запускался;
после переноса в личный аккаунт hosted и Power Cell jobs успешно выполнены.

16 октября API и кабинет развёрнуты в релиз
`github-ci-20261016-163116` через `infra/playbooks/deploy-platform.yml`, с
резервной копией SQLite и миграцией `0010`. Credentials установлены через
`infra/playbooks/setup-github-actions.yml`; повторный запуск: `changed=0`.
Публичные health/API и Actions отвечают HTTP 200. Повторные настоящие доставки
GitHub `ping` и `workflow_run` получили HTTP 202; Setup URL возвращает HTTP 303
на `https://platform.mutating.tech/actions?github=installed`.

17 октября через Ansible развёрнут релиз `github-ci-20261017-003014` с исправлениями
ревью и интерфейсной приёмки. Статический сайт сохранён из предыдущего релиза;
новые API и кабинет прошли проверки готовности и публичного HTTPS. На текущем
Mac уже установлена актуальная сборка агента (build 17); бинарник совпадает с
последней собранной версией. Перед изменением настроек активных изолятов не было.
Через локальный Ansible play сохранены предыдущие настройки, включён интернет
с сохранением `internet_only`, активный бюджет памяти поднят до 2 GiB при 1 CPU.
Mac снова online и публикует семейство `ghcr.io/actions/actions-runner`.
Существующий idle-бюджет не менялся; ресурсы самого CI job будут ограничены привязкой.
Mac находится в пуле организации `fac`, поэтому для приёмки нужен именно этот пул.

18 октября App и OAuth в личном аккаунте подтверждены. В браузере создана
привязка `2d624926-c4dd-4e82-b3d8-d4b79d8678bb` к текущему Mac в пуле `fac`:
**0,5 CPU, 512 MiB RAM, 4 GiB диска**, `max_parallel = 1`, hosted fallback,
ожидание подготовки до 300 секунд.
Прежняя привязка к пустому личному пулу отключена. Пользовательские CPU/RAM
не увеличивались; исходные 10 GiB диска не помещались в квоту Mac.

В sandbox добавлен `.github/workflows/ci.yml`: сгенерированный OIDC routing,
проверка ARM64, приватный checkout, небольшой артефакт и ручные сценарии
`success`, `failure`, `wait`. [Проверка отсутствия ресурсов](https://github.com/pomponchik/powercell-ci-sandbox/actions/runs/39072977375)
успешна: платформа выбрала `no_capacity`, job выполнился на GitHub, резерв очищен.
Личный аккаунт больше не заблокирован прежним billing организации.

Первое назначение на Mac выявило смешанную сборку: host поддерживал Actions,
но bundled runtime от 15 октября не содержал OCI RPC (`Missing ID`).
Платформа выполнила fallback и cleanup. Исправлена упаковка: provenance связывает
guest, его исходники и runtime; пять регрессионных тестов прошли. Все 13 guest
тестов прошли при пересборке. Согласованный runtime установлен локальным Ansible
в подписанную сборку 18; конфигурация ресурсов Mac сохранена. Доступ macOS к
сохранённому подключению подтверждён пользователем. Успех, контрольная ошибка
и отмена работающего job на Mac проверены. [Настоящий job на Mac](https://github.com/pomponchik/powercell-ci-sandbox/actions/runs/39075283950)
завершился успешно на runner `powercell-69dc1d33-74f3-4f93-8bff-2db6d10fcbf7`:
приватный checkout, ARM64 shell step и артефакт 172 байта. Изолят удалён,
runner больше не зарегистрирован в GitHub. Бюджет холодного старта расширен до 300
секунд с обновлением OIDC identity и достаточным таймаутом router; API и кабинет
развёрнуты Ansible в `github-ci-20261018-cold-start`.

[Контрольная ошибка](https://github.com/pomponchik/powercell-ci-sandbox/actions/runs/39075663414)
вернула ожидаемый `exit 23` на Mac, платформа сохранила `github_failure`.
[Отмена работающего job](https://github.com/pomponchik/powercell-ci-sandbox/actions/runs/39075932995)
остановила шаг с `sleep`, платформа сохранила `github_cancelled`.
Для обоих случаев VM/runner удалены, резервы освобождены; история различает
успех, ошибку, отмену и fallback. Credentials не сохраняются в command env.

Артефакты развёртывания 17 октября:
`/Volumes/CodexHome/power-cell-build/ci-live-20261017/`:
`release-vars.json`, `release-overlays.json`, `deploy.log`, `verify-release.log`,
`postdeploy-inspect.log`, `github-discovery.json`. Настройки сервера и Mac
применялись Ansible; предыдущие настройки Mac сохранены в `preferences.before-ci.json`.

Артефакты живой приёмки 18 октября и нового релиза:
`/Volumes/CodexHome/power-cell-build/ci-live-20261018/`: `verification.json`,
`release-vars.json`, `deploy.log`, `verify-release.log`, `install-agent.log`,
`run-*.json`, `state-*.log`, `logs-*/`.

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
| Where can this GitHub App be installed? | Any account; App принадлежит `mutating`, sandbox после переноса принадлежит личному аккаунту |

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

В разделе «Привязки CI» раскройте «Новая привязка», если уже есть сохранённые
настройки; для первой привязки форма открывается сразу. «Workflow и запуски»
показывает сохранённые ресурсы и историю, затем три шага подключения и YAML.
У отключённой привязки остаются история и сохранённый YAML; для продолжения
нужно создать новую привязку и заменить routing job в репозитории.

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

После браузерной проверки отключения/подключения 20 октября активная привязка
sandbox пересоздана: `5ac58cbc-77b3-4954-a1e5-892db7251c02`, пул `fac`, любой
подходящий Mac; 500m CPU, 512 MiB, 4 GiB, подготовка 300 с, параллельность 1.
В `.github/workflows/ci.yml` заменён ID привязки с сохранением CI-шагов.
Временные `ui-audit.yml` и `ui-no-mac.yml` удалены через Ansible.

Источники:

- [Регистрация GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).
- [Выбор репозиториев при установке](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app).
- [Разрешения для repository JIT runners](https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository).
- [Разрешения на изменение workflow через Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents).
- [Разрешения для workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
