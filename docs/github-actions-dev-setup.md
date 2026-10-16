# Подготовка GitHub для интеграционных тестов

Эта инструкция готовит внешние доступы для
[интеграции Actions](github-actions-integration.md). Серверные обработчики GitHub
пока не реализованы; сама регистрация App не включает интеграцию на платформе.
Для проверок используется текущий Mac с обновлённым Power Cell агентом.

## Тестовый репозиторий

Создайте отдельный **private** репозиторий, например `powercell-ci-sandbox`,
с README и веткой `main`. В Settings → Actions → General убедитесь, что Actions
разрешены. В аккаунте должны быть доступны GitHub-hosted минуты.

Workflow и тестовый код будут добавлены во время реализации. Существующие
рабочие репозитории для тестов не нужны.

## GitHub App для разработки

Откройте Settings → Developer settings → GitHub Apps → New GitHub App в личном
аккаунте или организации, которой принадлежит тестовый репозиторий. Имя должно
быть уникальным в GitHub, например `Mutating Actions Dev <суффикс>`.

| Поле | Значение |
| --- | --- |
| Homepage URL | `https://platform.mutating.tech/` |
| Callback URL | `https://api.mutating.tech/v1/github/oauth/callback` |
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
3. В разделе Private keys нажмите Generate a private key и сохраните `.pem`.
4. В Install App выберите Only select repositories и только sandbox.

Обработчики callback/webhook пока отсутствуют. Возврат после установки и первая
доставка webhook могут завершиться ошибкой: работоспособность этих адресов
проверяется после реализации и развёртывания. Связь аккаунта платформы с GitHub
позже завершается через интерфейс платформы, с проверкой авторизации и `state`.

## Локальная конфигурация

Каталог на текущем Mac уже подготовлен с правами `0700`:

```text
/Users/pomponchik/.config/mutating/github-actions-dev/
```

Сохраните private key под именем `app.pem`, а рядом создайте `app.env`:

```dotenv
THRONG_GITHUB_APP_ID=<App ID>
THRONG_GITHUB_CLIENT_ID=<Client ID>
THRONG_GITHUB_CLIENT_SECRET=<Client Secret>
THRONG_GITHUB_WEBHOOK_SECRET=<Webhook secret>
THRONG_GITHUB_PRIVATE_KEY_PATH=/Users/pomponchik/.config/mutating/github-actions-dev/app.pem
```

Это планируемые имена параметров будущей интеграции; текущий API ещё не
использует их. Установите права `0600` на оба файла:

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
