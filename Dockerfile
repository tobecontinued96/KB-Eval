ARG PYTHON_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ARG APT_MIRROR=http://mirrors.aliyun.com/debian
ARG APT_SECURITY_MIRROR=http://mirrors.aliyun.com/debian-security
ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_DEFAULT_INDEX=${PYPI_INDEX_URL} \
    PIP_INDEX_URL=${PYPI_INDEX_URL} \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
        -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR}|g" \
        -e "s|http://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" \
        /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY alembic.ini ./
COPY backend ./backend
COPY kb_eval ./kb_eval
COPY config ./config
COPY datasets ./datasets
COPY generated_sources ./generated_sources
COPY reports/.gitkeep ./reports/.gitkeep
COPY docker ./docker

RUN uv sync --frozen --no-dev \
    && mkdir -p /app/reports /app/logs /app/datasets/generated /app/generated_sources \
    && chmod +x /app/docker/backend-entrypoint.sh

EXPOSE 8200

ENTRYPOINT ["/app/docker/backend-entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8200"]
