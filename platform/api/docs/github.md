# GitHub Actions

Реализация включает OAuth GitHub App, зашифрованные пользовательские
токены с обновлением, просмотр установок/репозиториев, привязки workflow к пулам,
OIDC-резервирование, JIT runner lifecycle и подписанные webhooks. Для работы на
публичной платформе требуются развёртывание и серверные credentials; на
`api.mutating.tech` они установлены через Ansible 16 октября 2026 года.
Подтверждён приём подписанных webhook от GitHub. Настоящий GitHub job пока
не проверен из-за billing организации; HTTP-тестовый агент не заменяет такую проверку.
Сквозные сценарии, утверждения, границы моков и команды запуска описаны в
[проверках интеграции](../../../docs/github-actions-testing.md).

## Настройка

Параметры `THRONG_GITHUB_*` описаны в `.env.example` и
[инструкции подготовки](../../../docs/github-actions-dev-setup.md). Частичная
конфигурация отклоняется при старте. Без credentials `/v1/github/status`
возвращает `enabled: false`, и навигационная вкладка Actions скрыта.

Для миграции используйте `throng-api migrate`: revision `0010` добавляет
`github_accounts`, `github_oauth_flows`, `ci_routes`, `ci_reservations` и
`github_deliveries`. Применяйте её перед запуском нового API. Сохраняйте постоянный
Fernet key вместе с резервными копиями БД. Секреты App не хранятся в БД.

## Подключение аккаунта

Все операции, кроме callbacks, CI routing и webhook, используют обычный
AccessToken/BrowserSession платформы. Изменения требуют `manage`, подключение —
личного токена. Cookie-запросы сохраняют существующую проверку Origin/CSRF.

| Метод | URL относительно `/v1/github` | Назначение |
| --- | --- | --- |
| GET | `/status` | Доступность интеграции, ID/login подключённого GitHub пользователя |
| POST | `/authorize` | GitHub OAuth URL с одноразовым state текущей сессии |
| GET | `/oauth/callback` | Обмен code, проверка GitHub identity и возврат в `/actions` |
| POST | `/installations/authorize` | Установка App с state текущей сессии |
| GET | `/installations/callback` | Возврат после установки; без state ничего не привязывает |
| GET | `/installations?page=1` | Установки этого App, доступные подключённому пользователю |
| GET | `/installations/{id}/repositories?page=1` | Репозитории установки, доступные пользователю |
| DELETE | `/connection` | Удаление credentials и state, отключение созданных пользователем CI-привязок |

OAuth state действует 10 минут, хранится как SHA-256 и связан с пользователем,
назначением и токеном инициирующей сессии. Обмен не держит блокировку БД, затем
повторно проверяет сессию. Refresh сериализован отдельным lease. Обе страницы
GitHub списков используют по 100 элементов. Disconnect не удаляет установку App
из GitHub; новые назначения прекращаются, контроллер очищает активные runners.

## Привязка

`POST /v1/github/routes` требует `manage` и `execute`, личного пользователя,
GitHub repository admin и доступа App именно к указанному repository ID. Для
организации платформы дополнительно нужны `resources.manage` и `execution.run`.
Эти права и связь GitHub проверяются снова перед назначением.

```json
{
  "installation_id": 172111136,
  "repository_id": 1422262495,
  "workflow_path": ".github/workflows/ci.yml",
  "job_key": "test",
  "ref": "refs/heads/main",
  "company_id": null,
  "computer_ids": [],
  "cpu_millis": 2000,
  "memory_bytes": 4294967296,
  "disk_bytes": 10737418240,
  "preparation_seconds": 90,
  "timeout_seconds": 3600,
  "max_parallel": 1,
  "fallback": "ubuntu-24.04-arm"
}
```

Пустой `computer_ids` означает весь выбранный пул; непустой список ограничивает
назначение перечисленными компьютерами. Проверяются их принадлежность пулу и
runtime Power Cell. Ресурсы одного job выделяются на одном Mac. Для каждого
запуска используется пустое начальное состояние, образ `actions`, режим `vm` и
запрет автоматического повторного исполнения после потери изолята.

`GET /routes` — список доступных привязок, `limit` до 200 и `offset`.
`GET /routes/{id}/workflow` выдаёт самостоятельный пример YAML с Python bootstrap
из поставки API. Его routing job должен быть добавлен в workflow, а основной
job получает `needs` и динамический `runs-on`. Существующие steps, permissions,
concurrency и зависимости сохраняет автор workflow. `job_key` — имя job для
генерируемого примера; OIDC авторизует workflow целиком, а не отдельный job:
GitHub OIDC не содержит надёжного job ID для этой проверки. Авторы workflow с
правом менять выбранную ветку входят в доверенную границу этой привязки.

`DELETE /routes/{id}` отключает привязку, оставляя историю.
`GET /routes/{id}/runs?limit=50` возвращает статусы, причины выбора, isolate ID и
ссылки на GitHub. Credentials и внутренняя controller lease в ответ не входят.
Изменение параметров выполняется созданием новой привязки и отключением старой.

## Выбор runner и очистка

Bootstrap работает на GitHub-hosted ARM64 и получает короткий OIDC token с
`aud = <public API origin>/v1/github/ci`. Запрос:

```text
POST /v1/github/ci/routes/{id}/reserve
Authorization: Bearer <GitHub OIDC token>
{"instance":"default"}
```

Проверяются RS256, фиксированные issuer/JWKS, audience, время, repository/owner
IDs, workflow path/ref, событие и GitHub-hosted источник. Сейчас Power Cells
разрешены для `push` и `workflow_dispatch` указанной ветки. Pull requests и
остальные события получают fallback в bootstrap; `pull_request_target` не
получает доступ к пулу. Reusable workflows требуют отдельного расширения политики.

Ключ идемпотентности: route + run ID + run attempt + instance. Для матрицы нужен
отдельный bootstrap на каждый instance с устойчивым уникальным
`POWERCELL_INSTANCE`; обычный пример генерируется для одного job. Повтор запроса
не создаёт новую VM; новый attempt получает отдельную резервацию.

Сначала возвращается `status: preparing`. Планировщик резервирует CPU/RAM/disk
через общий ресурсный учёт изолятов. После подготовки изолята контроллер повторно
проверяет GitHub доступ и workflow run, регистрирует одноразовый JIT runner,
запускает официальный `run.sh` и ждёт `online` плюс подтверждения запуска агентом.
Тогда результат содержит `runs_on: ["self-hosted", "powercell-<uuid>"]`.

Перед публикацией этого output bootstrap подтверждает выбор через
`POST /ci/routes/{id}/commit` с тем же instance/OIDC. Неподтверждённый `ready`
живёт только до исходного срока подготовки. После подтверждения runner имеет
не более пяти минут на получение job; начало job продлевает срок до его
настроенного timeout. Эти сроки также отражаются в lifetime изолята, поэтому
общий scheduler может выполнить очистку независимо от CI controller.

Если bootstrap выбирает fallback после потери связи или истечения срока, он
вызывает `POST /ci/routes/{id}/cancel`. Отмена создаёт идемпотентную запись даже
если исходный reserve ещё проверяет GitHub: поздний запрос увидит отмену и не
создаст VM. Начавшийся job отменять этим endpoint нельзя. Недоступный cancel
страхуется сроком подготовки и lease выбранного runner. Долгоживущего
персонального токена платформы в workflow нет.

При истечении подготовки, квотах или ошибке GitHub решение фиксируется как
fallback: `runs_on: "ubuntu-24.04-arm"` и машинная причина. Значение `none`
отключает fallback и завершает routing ошибкой. Стандартный fallback принимается
только при ресурсах до 2 CPU, 8 GiB RAM и 14 GiB disk; большие требования требуют
`none`. Его доступность и оплачиваемые минуты определяет GitHub.

JIT-конфигурация зашифрована в резервации; обычные Command.env и AgentTask.payload
её не содержат. Расшифровка происходит при выдаче задачи только назначенному
аутентифицированному агенту. После завершения/отмены секрет удаляется, изолят
уничтожается, runner удаляется из GitHub. Занятые ресурсы освобождаются только
после подтверждения агента либо подтверждённого чистого старта. Неизвестная
регистрация после сбоя находится по уникальному имени; финальная проверка
очистки учитывает срок controller lease. Offline Mac сохраняет резервацию до
подтверждения очистки, чтобы не назначить эти ресурсы повторно.

Webhook: `POST /v1/github/webhook`, HMAC SHA-256 сырых байтов, обязательный
`X-GitHub-Delivery`, лимит 2 MiB. ID доставки дедуплицируется, изменение payload
под тем же ID отклоняется. `workflow_job` сверяет run attempt, runner ID/name;
запоздалый in_progress не возвращает завершённую резервацию в работу.
`workflow_run` завершает оставшиеся ресурсы; удаление/приостановка установки и
удаление репозитория из App отключают привязки. Неполученные события дополнительно
страхуются опросом GitHub и сроком жизни резервации.

После выдачи готового self-hosted runner нет переноса уже выбранного job на
GitHub при потере Mac. Нельзя исключить исчезновение runner между ответом
bootstrap и назначением job GitHub. Официальный image не содержит весь набор
GitHub-hosted ПО; Linux ARM64, отсутствие Docker/container actions и macOS/Xcode
остаются ограничениями текущего исполнения.
