FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /bin/

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY api/alembic.ini ./
COPY api/alembic ./alembic
COPY api/app ./app
COPY deep-sky-advisor /opt/starun-skills/deep-sky-advisor
COPY deep-sky-processor /opt/starun-skills/deep-sky-processor
RUN groupadd --gid 10001 starun \
    && useradd --uid 10001 --gid starun --create-home starun \
    && mkdir -p /data \
    && chown -R starun:starun /app /data \
    && chmod -R a-w /opt/starun-skills

USER starun

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
