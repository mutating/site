# The Mutating Company

Один репозиторий для сайта Hugo, Throng API и веб-интерфейса платформы.

```text
site/                   Hugo: контент, темы, конфигурация и инструменты сайта
platform/api/           Python API, контроллер, миграции и документация протокола
platform/web/           React/Vite и Python-сервис кабинета
tests/site/             Все тесты сайта и их фикстуры
tests/platform/api/     Все тесты API, включая миграции и общий запуск
tests/platform/web/     Серверные, unit, браузерные, HTTPS и визуальные тесты
infra/                  Общая настройка сервера, nginx, TLS и systemd
deploy/                 Общий запуск и активация одного релиза
```

Исходный репозиторий `throng_api` сохраняется отдельно. Здесь находятся копии API
и кабинета; дальнейшие изменения этого монорепозитория не обновляют его автоматически.

## Разработка

Из корня:

```sh
hugo server --source site
```

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
<http://127.0.0.1:8001/account/>. Ctrl+C останавливает оба процесса.
В общем `.env` кабинету нужен origin `http://127.0.0.1:8001`.

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
Фильтрации проверок по изменённым каталогам пока нет. Порог покрытия API — 100%.
В `main` полный набор вызывается из deploy workflow, чтобы не запускать его
повторно отдельным workflow на тот же push.

Сохранность тестов и результаты повторных прогонов после переноса записаны в
[отчёте проверки](docs/migration-verification.md).

## Production

Единый workflow собирает Hugo и UI, загружает весь релиз по SSH, сохраняет SQLite,
применяет миграции, перезапускает общий сервис и публикует статическую сборку.
Деплой на каждый push в `main` ждёт успешного завершения всех тестовых групп.

- `mutating.tech` — сайт Hugo;
- `api.mutating.tech` — API;
- `platform.mutating.tech` — кабинет с маршрутом `/account/`.

На сервере используется systemd; для запуска обоих приложений:

```sh
sudo systemctl start throng
```

Настройка сервера, DNS, SSH-секретов и сохранения данных описана в
[docs/deployment.md](docs/deployment.md). Основной playbook —
`infra/playbooks/setup-server.yml`. Он объединяет настройку статического сайта и
платформы. Изменения nginx и системных файлов требуют явного применения playbook;
обычный CI обновляет приложения и миграции.
