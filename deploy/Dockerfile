# The web app (frontend/, React + Mantine), built once on the build platform: its output is plain files.
FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:8996bb5821d62a8fd9bb27c1cb07462f3fdce6e47d640c6a881ead0b064ce216 AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build -- --outDir /web/dist

FROM python:3.15-slim@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# pdftotext/pdftoppm for flyer PDFs, tesseract for agents that publish rent tables as images.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
ENV UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-cache

# Headless Chromium for sources whose data is only behind clicks (Reside's application form); hetzner only uses it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN /app/.venv/bin/playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY alembic ./alembic
COPY src ./src
COPY --from=web /web/dist ./src/housing_watch/web/dist

ARG GIT_SHA=unknown
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    HW_REVISION=${GIT_SHA}

# State is in Postgres (HW_DATABASE_URL). `serve` on hetzner (worker + web), `worker` everywhere else.
EXPOSE 9091
ENTRYPOINT ["python", "-m", "housing_watch"]
CMD ["serve"]
