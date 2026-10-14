# Extend the exact visual renderer with the remaining native/website test tools.
# Build tests/platform/web/visual.Dockerfile as platform-visual first.
ARG VISUAL_IMAGE=platform-visual
FROM ${VISUAL_IMAGE}
USER root
WORKDIR /workspace
ARG HUGO_VERSION=0.164.0
RUN apt-get update && apt-get install --yes --no-install-recommends nginx openssl curl ca-certificates \
    && architecture="$(dpkg --print-architecture)" \
    && curl --fail --location --silent --show-error \
       "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_extended_${HUGO_VERSION}_linux-${architecture}.tar.gz" \
       --output /tmp/hugo.tar.gz \
    && tar -xzf /tmp/hugo.tar.gz -C /usr/local/bin hugo
COPY requirements_dev.txt pytest.ini ./
COPY platform/api/alembic.ini ./platform/api/alembic.ini
RUN uv venv tests/site/.venv --python 3.11 \
    && uv pip install --python tests/site/.venv/bin/python -r requirements_dev.txt \
    && PLAYWRIGHT_SKIP_BROWSER_GC=1 tests/site/.venv/bin/python -m playwright install chromium
COPY site ./site
COPY tests ./tests
COPY deploy ./deploy
COPY infra ./infra
COPY .github ./.github
CMD ["tests/site/.venv/bin/python", "-m", "pytest", "tests/site", "-q"]
