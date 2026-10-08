FROM python:3.12-slim-trixie AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.11.3

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache


FROM python:3.12-slim-trixie AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

RUN groupadd --system --gid 10001 mfdataindia \
    && useradd --system --uid 10001 --gid mfdataindia --home-dir /app mfdataindia

WORKDIR /app

COPY --from=build --chown=mfdataindia:mfdataindia /app/.venv /app/.venv
COPY --chown=mfdataindia:mfdataindia scripts ./scripts
COPY --chown=mfdataindia:mfdataindia sql ./sql

USER 10001:10001

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn mfdataindia.api.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8080}\" --no-access-log"]
