FROM node:24-alpine AS ui
WORKDIR /build
COPY package.json package-lock.json ./
COPY platform/web/package.json ./platform/web/package.json
COPY packages/ui/package.json ./packages/ui/package.json
COPY tools/http-lint/package.json tools/http-lint/package-lock.json ./tools/http-lint/
RUN npm ci
COPY packages/ui ./packages/ui
COPY platform/web ./platform/web
RUN npm run build:web

FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
COPY platform/web/pyproject.toml platform/web/uv.lock ./
RUN uv sync --frozen --no-dev --no-editable
COPY platform/web/server ./server
COPY --from=ui /build/platform/web/dist ./dist
RUN useradd --create-home --uid 10002 dashboard
USER dashboard
ENV PATH=/app/.venv/bin:$PATH \
    DASHBOARD_API_URL=https://api.mutating.tech/v1
EXPOSE 8001
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8001"]
