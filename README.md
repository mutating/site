# The Mutating Company

Один репозиторий для сайта Hugo, Throng API и веб-интерфейса платформы.

```text
site/                   Hugo: контент, темы, конфигурация и инструменты сайта
packages/ui/            @mutating/ui: общие стили, поведение, React и Hugo адаптеры
platform/api/           Python API, контроллер, миграции и документация протокола
platform/web/           React/Vite и Python-сервис кабинета
tests/site/             Все тесты сайта и их фикстуры
tests/ui/               Проверки UI-библиотеки и сравнение хэдера с оригиналом
tests/platform/api/     Все тесты API, включая миграции и общий запуск
tests/platform/web/     Серверные, unit, браузерные, HTTPS и визуальные тесты
infra/                  Общая настройка сервера, nginx, TLS и systemd
deploy/                 Общий запуск и активация одного релиза
```

Исходный репозиторий `throng_api` сохраняется отдельно. Здесь находятся копии API
и кабинета; дальнейшие изменения этого монорепозитория не обновляют его автоматически.

## Разработка

Статический сайт использует хэдер из общей UI-библиотеки. Платформа пока
использует собственную реализацию:

```sh
npm ci
npm run dev:ui      # HTML-демо: http://127.0.0.1:18120; React: /react.html
npm run build:ui
npm run test:ui     # Нужны Hugo, Chromium и Firefox для Playwright
```

Состав, API и примеры подключения описаны в
[packages/ui/README.md](packages/ui/README.md).

Из корня:

```sh
npm ci
npm run build:ui
hugo server --source site
```

После изменений в `packages/ui` повторите `npm run build:ui`.
Production-сборка сайта: `npm run build:site` — библиотека собирается первой.

Скриншотные регрессии хэдера: `npm run test:site:header`. Эталон старого
хэдера, матрица состояний и аудит e2e находятся в
[tests/site/header](tests/site/header/README.md).

API и кабинет после установки зависимостей и сборки запускаются одной командой:

```sh
uv sync --project platform/api --frozen
npm ci
npm run build:web
# При первом запуске:
cp platform/api/.env.example platform/api/.env
uv run --project platform/api python deploy/run.py
```

API: <http://127.0.0.1:8000/docs>; кабинет:
<http://127.0.0.1:8001/>. Ctrl+C останавливает оба процесса.
В общем `.env` задайте `DASHBOARD_API_URL=http://127.0.0.1:8000/v1`,
`THRONG_BROWSER_ORIGINS=["http://127.0.0.1:8001","http://127.0.0.1:5173"]`
и `THRONG_BROWSER_SECURE_COOKIE=false`.

Для разработки UI с Vite используйте настройки из
[platform/web/README.md](platform/web/README.md).
Документация API — [platform/api/README.md](platform/api/README.md),
сайта — [site/README.md](site/README.md).

## Проверки

Python-окружения сайта, API и кабинета самостоятельны. Это сохраняет прежние
закреплённые зависимости, фикстуры и изоляцию наборов тестов. Зависимости Node.js
устанавливаются из общего `package-lock.json` через npm workspaces.

```sh
uv venv tests/site/.venv --python 3.11
uv pip install --python tests/site/.venv/bin/python -r requirements_dev.txt
uv sync --project platform/api --frozen
uv sync --project platform/web --frozen
npm ci
npx playwright install chromium firefox webkit
tests/site/.venv/bin/python -m playwright install chromium
npm run build:web

tests/site/.venv/bin/python -m pytest tests/site -n 2 --dist loadgroup
uv run --project platform/api pytest -c platform/api/pyproject.toml tests/platform/api \
  --cov=throng_api --cov-config=platform/api/pyproject.toml
uv run --project platform/web pytest -c platform/web/pyproject.toml tests/platform/web/server
npm run test:web:unit
npm run test:web:e2e
npm run test:web:deploy
```

Для HTTPS-проверок нужен nginx и OpenSSL. Сетевые тесты используют временные базы
и отдельные порты; production-данные не затрагиваются.
Визуальные эталоны сравниваются в закреплённом Linux ARM64-образе:

```sh
docker build --platform linux/arm64 -f tests/platform/web/visual.Dockerfile -t platform-visual .
docker run --rm --ipc=host --platform linux/arm64 platform-visual
```

Для воспроизведения всех Python-проверок в чистом Linux-окружении:

```sh
docker build --platform linux/arm64 -f tests/verification.Dockerfile -t verification .
docker run --rm --ipc=host verification tests/site/.venv/bin/python -m pytest tests/site -n 2 --dist loadgroup
docker run --rm --ipc=host verification platform/api/.venv/bin/python -m pytest -c platform/api/pyproject.toml tests/platform/api
```

В CI каждый push и pull request запускает все прежние группы: сайт, WebGL, SEO,
API на SQLite в Python 3.11/3.12/3.13, API на PostgreSQL, сервер кабинета, unit,
пять браузерных конфигураций, HTTPS, общий сервис и визуальные регрессии.
Фильтрации проверок по изменённым каталогам пока нет. На каждой версии Python
запускается весь набор тестов API. Покрытие строк и ветвей измеряется на Python
3.12 с порогом 100% для SQLite и PostgreSQL: на Python 3.11 измеритель теряет
выполненные переходы в сочетании корутин и greenlet, используемых SQLAlchemy.
В `main` тесты запускаются отдельным workflow параллельно с деплоем.

Сохранность тестов и результаты повторных прогонов после переноса записаны в
[отчёте проверки](docs/migration-verification.md).

## Production

Единый workflow собирает Hugo и UI, загружает весь релиз по SSH, сохраняет SQLite,
применяет миграции, перезапускает общий сервис и публикует статическую сборку.
Деплой запускается на каждый push в `main` без ожидания результатов тестов.

- `mutating.tech` — сайт Hugo;
- `api.mutating.tech` — API;
- `platform.mutating.tech` — интерфейс кабинета в корне `/`, прямые запросы браузера к API на `https://api.mutating.tech/v1`.

На сервере используется systemd; для запуска обоих приложений:

```sh
sudo systemctl start throng
```

Настройка сервера, DNS, SSH-секретов и сохранения данных описана в
[docs/deployment.md](docs/deployment.md). На существующем nginx, который также
обслуживает `pomponchik.org` и может обслуживать другие сайты, платформа добавляется
через `infra/playbooks/setup-platform.yml`. Полный `infra/playbooks/setup-server.yml`
объединяет настройку статического сайта и платформы на новом сервере.
Изменения nginx и системных файлов требуют явного применения playbook;
обычный CI обновляет приложения и миграции.
