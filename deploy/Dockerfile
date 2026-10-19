FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

# pdftotext/pdftoppm for flyer PDFs, tesseract for agents that publish rent tables as images.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
ENV UV_PYTHON_PREFERENCE=only-system \
    UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-cache

COPY src ./src

ARG GIT_SHA=unknown
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    HW_REVISION=${GIT_SHA} \
    HW_DB=/data/housing-watch.db

VOLUME ["/data"]
CMD ["python", "-m", "housing_watch", "loop"]
