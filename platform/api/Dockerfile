FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY throng_api ./throng_api
RUN uv sync --frozen --no-dev --no-editable
RUN useradd --create-home --uid 10001 throng && mkdir /data && chown throng:throng /data
USER throng
ENV THRONG_DATABASE_URL=sqlite+aiosqlite:////data/throng.db \
    THRONG_STORAGE_PATH=/data/artifacts \
    PATH=/app/.venv/bin:$PATH
EXPOSE 8000
CMD ["throng-api", "serve", "--host", "0.0.0.0", "--port", "8000"]
