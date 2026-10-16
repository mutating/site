# Keep the renderer and fonts identical for baseline updates and CI comparisons.
FROM mcr.microsoft.com/playwright:v1.64.0-noble@sha256:06a9939e57531807f8d5fd76ce44b53165ffb7d7501d87ab10e285c20b1e971f
COPY --from=ghcr.io/astral-sh/uv:0.10.12@sha256:72ab0aeb448090480ccabb99fb5f52b0dc3c71923bffb5e2e26517a1c27b7fec /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy
WORKDIR /workspace
WORKDIR /workspace/platform/api
COPY platform/api/pyproject.toml platform/api/uv.lock platform/api/README.md ./
COPY platform/api/throng_api ./throng_api
RUN uv sync --frozen --python 3.12
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY platform/web/package.json ./platform/web/package.json
COPY packages/ui/package.json ./packages/ui/package.json
RUN npm ci
WORKDIR /workspace/platform/web
COPY platform/web/pyproject.toml platform/web/uv.lock platform/web/README.md ./
COPY platform/web/server ./server
RUN uv sync --frozen --python 3.12
COPY platform/web ./
COPY tests/platform/web /workspace/tests/platform/web
RUN npm run build
CMD ["npm", "run", "test:visual"]
