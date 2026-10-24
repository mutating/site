# Аудит безопасности — The Mutating Company

Дата: 18.10.2026 · Область: сайт (`site/`, Hugo), API (`platform/api/throng_api`, FastAPI), кабинет (`platform/web`, React + Python-сервер), UI-пакет (`packages/ui`), инфраструктура и деплой (`deploy/`, `infra/`, `.github/workflows`).

**Методика:** статический анализ кода и конфигураций, сканирование зависимостей (`npm audit`, pip-audit по `uv.lock`), поиск секретов в истории git, пассивные live-проверки production (`curl -I` на mutating.tech / api.mutating.tech / platform.mutating.tech). Активная эксплуатация не проводилась; учётные записи не создавались.

## Итог

| Критичность | Кол-во |
|---|---|
| Critical | 0 |
| High | 2 |
| Medium | 9 |
| Low | 12 |
| Info | 7 |

Общая оценка: **выше среднего**. Критических дыр не найдено. Логика аутентификации, владения ресурсами, CSRF, SSRF, инъекций и path traversal — защищена грамотно (см. «Что сделано хорошо»). Основные риски — supply chain деплоя, устаревшая `cryptography`, отсутствие rate limiting и заголовков на API.

---

## 🟠 High

### H1. Деплой в production не зависит от результатов тестов + неизолированная действия
**Где:** `.github/workflows/deploy.yml:3-8` · README.md:131 («без ожидания результатов тестов»)
**Суть:** на каждый push в `main` релиз уходит на сервер без `needs: [test]`, без GitHub Environment с обязательными ревьюерами и без required checks на ветке. Компрометация CI или красный merge = сразу production. Дополнительно: все действия (`checkout@v4`, `setup-node@v4`, `peaceiris/actions-hugo@v3.2.0` и др.) — по тегам, не по SHA (см. M6).
**Рекомендация:** `needs:` на статус тестов (или required check) + `environment: production` с required reviewer; пинировать действия по SHA.

### H2. Уязвимая `cryptography 47.0.0` в API (7 advisory, CVSS до 7.5)
**Где:** `platform/api` (`uv.lock`, pip-audit) · Исправление: ≥ 48.0.1, остальные PYSEC — в 49.0.0/50.0.0
**Суть:** в wheels включён уязвимый статический OpenSSL (GHSA-537c-gmf6-5ccf / CVE-2026-34180, CVSS 7.5 — network DoS через разбор untrusted TLS), плюс PYSEC-2026-3552/3553/3554. Библиотека используется API для релея/mTLS, Fernet-токенов GitHub, сертификатов.
**Рекомендация:** `uv lock --upgrade-package cryptography` в `platform/api`, обновить lock и задеплоить.

---

## 🟡 Medium

### M1. Swagger/OpenAPI открыты в production (подтверждено live)
**Где:** `throng_api/app.py:76-108` · live: `api.mutating.tech/docs` → 200, `openapi.json` → 200 (153 КБ)
**Суть:** полное описание всех маршрутов (включая `/agent/v1/*`, `/acme/powercell/*`, `/v1/github/*`) доступно неавторизованно — карта поверхности атаки для разведки.
**Рекомендация:** `docs_url=None, redoc_url=None, openapi_url=None` в production-конфиге.

### M2. Нет rate limiting на логин/регистрацию — онлайн-брутфорс возможен
**Где:** `throng_api/accounts.py:80-116`, `throng_api/browser.py:72-84`, `config.py:15` (`THRONG_ALLOW_REGISTRATION=true`)
**Суть:** `POST /v1/auth/login`, `/v1/auth/session` и `/v1/users` не ограничены по частоте. Перечисление пользователей блокировано ( dummy-Argon2 + единый текст ошибки — сделано хорошо), но число попыток неограничено; регистрация неограниченно спамится.
**Рекомендация:** `limit_req` на nginx для этих путей (или slowapi/lockout).

### M3. Смена email без верификации → перехват email-приглашений
**Где:** `throng_api/accounts.py:159-183`, `throng_api/invitations.py:42-46, 212-261`
**Суть:** приглашения без `invitee_user_id` матчатся по email пользователя; собственный email меняется мгновенно, без письма-подтверждения. Кто-то, знающий адрес открытого приглашения, может присвоить его (сменить свой email до accepting).
**Рекомендация:** подтверждение email при смене; для приглашений предпочесть `invitee_user_id` или требовать верифицированный email при accept.

### M4. Security-заголовки на API полностью отсутствуют (кроме HSTS)
**Где:** `deploy/nginx.conf.j2` · live: `api.mutating.tech` отдаёт только `strict-transport-security`
**Суть:** нет `X-Content-Type-Options: nosniff` (в т.ч. на `/v1/artifacts/{id}/content` — downloader на `octet-stream`, но барьер стоит добавить), нет `X-Frame-Options`/CSP на `/docs`.
**Рекомендация:** один `add_header`-набор в nginx-локации API: nosniff, XFO, Referrer-Policy.

### M5. На основном сайте mutating.tech нет CSP и frame-контроля
**Где:** `site/hugo.toml`, `themes/.../head.html`, nginx-локация сайта
**Суть:** лапка HSTS/nosniff/Referrer-Policy на сайте есть (подтверждено live), но CSP нет вовсе — при появлении любой XSS-точки защиты не будет. Yandex Metrika (`mc.yandex.ru`) — внешний скрипт без SRI, не вписан в гипотетический CSP.
**Рекомендация:** CSP вида `default-src 'self'; script-src 'self' https://mc.yandex.ru; ...` + `frame-ancestors 'self'` уже через nginx.

### M6. GitHub Actions не запинены по SHA
**Где:** `.github/workflows/tests.yml:78-97`, `.github/workflows/deploy.yml:76` (+ разнобой setup-python v5/v6 внутри tests.yml)
**Суть:** тег action перемещаем; для workflow с доступом к SSH-секретам продакшена это вектор supply chain. `peaceiris/actions-hugo` — сторонний.
**Рекомендация:** pin по full commit SHA (Dependabot/renovate для обновления).

### M7. SSH-деплой по паролю через sshpass
**Где:** `deploy/upload.sh:11-34` (`PreferredAuthentications=password`, `SSHPASS` из CI-секрета)
**Суть:** known_hosts запинены и StrictHostKeyChecking=yes — хорошо, но парольная аутентификация слабее ключа, не ротируется без даунтайма и попадает в окружение CI-джобы. Плюс `activate.sh` запускает python именно из загруженного релиза под `throng`, который читает `/etc/throng/throng.env` (секреты GitHub App/PowerCell) — компрометация CI даёт blast radius вплоть до этих секретов.
**Рекомендация:** deploy-only SSH-ключ с ограничением `command=`; рассмотреть отдельный axe миграций без полного чтения env.

### M8. Буферизация тел запросов агентов: до 100 МБ RAM на соединение
**Где:** `throng_api/device_security.py:203-230` + `powercell.py:274-282`
**Суть:** тело агента целиком читается в память (`bounded_body` → `request._body`, затем вторая копия при записи артефакта) — N параллельных туннелей = N×100 МБ RAM хоста. БД (SQLite) и контроллер живут на той же машине.
**Рекомендация:** лимит одновременных соединений на релей/nginx (`limit_conn`) + стриминговая верификация или отказ от двойного чтения.

### M9. Нет `limit_req`/`limit_conn` ни в одной nginx-конфигурации
**Где:** `deploy/nginx.conf.j2` и все vhost'ы (`grep` — 0 вхождений)
**Суть:** при `client_max_body_size 100m` и отсутствии троттлинга — удобный DoS-вектор на inference/аплоады.
**Рекомендация:** `limit_req_zone`/`limit_conn_zone` на этапе edge (минимум: `limit_conn` на `/v1/artifacts` и агентские пути).

---

## 🔵 Low

### L1. systemd: не применён «глубокий» харденинг
**Где:** `deploy/throng.service:1-26`
Базовый набор есть (User=throng, UMask=0077, NoNewPrivileges, PrivateTmp, ProtectSystem=strict, ProtectHome, ReadWritePaths), но нет `RestrictAddressFamilies`, `CapabilityBoundingSet`, `ProtectKernel*`, `SystemCallFilter`, `LockPersonality`. Добавить по чек-листу systemd hardening.

### L2. Docker-образы по тегам, не по digest
**Где:** `platform/api/Dockerfile`, `platform/web/Dockerfile` (`python:3.12-slim`, `node:24-alpine`, `ubuntu:…`)
Тег-подмена при пуле. Пинировать по sha256-digest.

### L3. Дизайн-стадии кабинета уходят в production, личный email захардкожен
**Где:** `platform/web/public/profile-designs/gallery.js:5,26`, `organization-designs*/…` → доступны на `platform.mutating.tech/…`
В публично обслуживаемых файлах стоит личный email (`zhenl-b@yandex.ru`) и имя — спам/фишинг-вектор. Дизайны — статические заготовки (innerHTML с захардкоженными данными, пользовательский ввод не участвует). Рекомендация: вычистить email, исключить `public/*-designs` из prod-сборки.

### L4. `run.url` из API вставляется в `<a href>` без валидации схемы
**Где:** `platform/web/src/components/CIRoutes.tsx:126`
Если API скомпрометирован — `javascript:`-ссылка по клику. Валидировать как в `src/lib/github.ts:25-30`.

### L5. Пагинация без лимита страниц — клиентский DoS
**Где:** `platform/web/src/lib/api.ts:59-74` (`allPages`)
Бесконечный цикл запросов, если API отдаёт по 200 записей всегда. Добавить счётчик страниц.

### L6. `compare_digest` на Unicode → 500 на ACME-секретах из пути
**Где:** `throng_api/acme.py:239-253`
`secrets.compare_digest(str, str)` бросает TypeError на не-ASCII → 500 вместо 401. Привести оба аргумента к bytes/UTF-8 или проверить схему заранее. Аналогично: глубоко вложенный JSON может дать `RecursionError` вместо 400 (`strict_json` в `device_security.py`).

### L7. Hugo: `href` из front matter без проверки схемы; гольдмарк `unsafe = true`
**Где:** `themes/mutating/layouts/authors/single.html:45`; `site/hugo.toml:83`
`javascript:`-URL из front matter авторов отрисуется как есть; `safeHTML` на `.Text` заголовков — доверенные данные, но лягут в DOM as-is. Фильтровать `http(s)://` и помнить про trusted-content при подключении внешних авторов.

### L8. Загрузка Hugo/uv без проверки чексуммы
**Где:** `infra/playbooks/setup-site.yml:77-85, 43-47`
`.deb` и tar с GitHub Releases без `checksum:`. Добавить SHA-256 в playbook.

### L9. IFrame тентаклей без `sandbox`
**Где:** `themes/.../contact.html:6`
Сейчас same-origin по дизайну — безопасно; при переносе фрейма на другой origin добавить `sandbox="allow-scripts"`.

### L10. Отладочные скрипты и `gzip`-глоб от корня
**Где:** `site/scripts/analyze_crawler_logs.py:132` (`Path("/").glob(...)`); WebGL-параметры `?mt-…` доступны любому посетителю
Инструменты локальные/доверенные, но лучше ограничить префикс; параметры фрейма не влияют на безопасность.

### L11. Утилитарные мелочи деплоя
`deploy/activate.sh:15-19` — canary `grep -Fq '--no-access-log'` обходится комментарием (это обнаружение, не контроль); HSTS без `preload`; `Permissions-Policy` не отдаётся платформой (`platform/web/server/app.py:28-41`) и API; для HEAD-запросов платформа отвечает 405 вместо GET.

### L12. Sourcemaps UI-пакета публичны
**Где:** `packages/ui/dist/*.map` (публикуются и через сайт)
Секретов внутри нет; если исходники TS нежелательно раскрывать — отключить sourcemap в prod-сборке.

---

## ⚪ Info (наблюдения без практической эксполяции)

- **Секреты в git-истории: не обнаружены** (scan по `rev-list --all`; `platform/api/.env` с dev-секретами предупреждён gitignore, `git ls-files` подтверждает — вне VCS). Рекомендация: rotate `THRONG_POWERCELL_SECRET` из локального `.env` при первом же переносе в любой менеджер.
- CSP платформы содержит `'unsafe-inline'` для стилей — осознанный компромисс под React (`server/app.py:29-41`).
- `session_id` компьютера возвращается владельцу (`public(Computer)`) — по дизайну; без `agt_`-токена бесполезен.
- `content-type` артефактов полностью user-controlled, но `Content-Disposition: attachment` + `octet-stream` предотвращают рендер.
- `compose.yaml:9` — `THRONG_ALLOW_REGISTRATION: "true"` по умолчанию; если пользовательское планирование выдвигает контейнер наружу — регистрация открыта.
- Релей PowerCell слушает `0.0.0.0:8444` без IP-allowlist (осознанная структура mTLS-протокола; можно добавить rate limit/allowlist на уровне фаервола — см. tasks/powercell-firewall.yml).
- 100% покрытие строк и ветвей API (SQLite+PostgreSQL) и богатый набор тестов безопасности (traversal, cookie, HTTPS, CSP) — большая страховка от регрессий.

## Что сделано хорошо (обзор)

Полная проверка владения на каждом маршруте с единым 404 (без раскрытия существования); Argon2-пароли + timing-safe dummy-хэш; токены — только SHA-256-хэши в БД, сырые значения выдаются один раз; HMAC-подпись GitHub webhook на `hmac.compare_digest`; строгий JSON-парсер (дублиCAT ключей, NaN, лимиты размеров); CBOR-аттестация устройств с закреплённым SSL-корнем и replay-nonce; SSRF-поверхность отсутствует (все внешние хосты — константы, `follow_redirects=False`, `trust_env=False`); CSRF для cookie-сессий закрыт трижды (SameSite=Lax + Origin + X-Requested-With, подтверждено тестами); кабинет — минималистичный статический сервер со строгим CSP и traversal-защитой, покрытой юнит-тестом; скрипты деплоя — списковые `subprocess`, валидация входов regex, `flock`, атомарные swaps, откат при неудачной миграции; systemd на непривилегированном пользователе с UMask=0077 и ProtectSystem=strict; секреты — только в GitHub Secrets/Ansible `no_log`; npm audit — 0 уязвимостей, Python-зависимости зафиксированы.

## План исправления (по приоритету)

1. **H2** — обновить `cryptography` в `platform/api` (lock → deploy).
2. **H1 + M6** — отделить деплой от статуса тестов, GH Environment с ревьюером, pin actions по SHA.
3. **M1** — отключить `/docs`/`/openapi.json` в production.
4. **M2/M9** — rate limiting на auth-пути и апплоады (nginx `limit_req`/`limit_conn`).
5. **M5/M4** — CSP на сайте, security-заголовки API (nosniff/XFO/Referrer-Policy).
6. **M3** — email-верификация при смене адреса / accept приглашений.
7. **M7/M8** — SSH-ключ вместо пароля; лимит одновременных агентских туннелей.
8. **Medium/others** — systemd-глубокий харденинг (L1), digest-пин образов (L2), вычистить email и дизайн-стадии из prod (L3).
