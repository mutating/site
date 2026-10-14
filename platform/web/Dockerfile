FROM node:24-alpine AS ui
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable
COPY server ./server
COPY --from=ui /build/dist ./dist
RUN useradd --create-home --uid 10002 dashboard
USER dashboard
ENV PATH=/app/.venv/bin:$PATH \
    DASHBOARD_PUBLIC_ORIGIN=https://platform.mutating.tech \
    DASHBOARD_SECURE_COOKIE=true
EXPOSE 8001
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8001"]
