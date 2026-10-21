# The web app (frontend/, React + Mantine), built once on the build platform: its output is plain files.
FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:6a1c190e17215d844926b0677f4bf87273ff7d5e54db06d3b5b5863ab0b09a3d AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build -- --outDir /web/dist

FROM python:3.14-slim@sha256:fbecd461fc72d21b2143c6eaba25143738079154a6d0ea3da3aec724f50eed4a

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# pdftotext/pdftoppm for flyer PDFs, tesseract for agents that publish rent tables as images.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.27@sha256:143b40f4ab56a780f43377604702107b5a35f83a4453daf1e4be691358718a6a /uv /usr/local/bin/uv
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
