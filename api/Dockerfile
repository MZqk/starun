FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /bin/

WORKDIR /app

ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY deep-sky-advisor /opt/starun-skills/deep-sky-advisor
COPY deep-sky-processor /opt/starun-skills/deep-sky-processor
RUN python /opt/starun-skills/deep-sky-processor/scripts/download_starnet.py
RUN groupadd --gid 10001 starun \
    && useradd --uid 10001 --gid starun --create-home starun \
    && mkdir -p /data \
    && chown -R starun:starun /app /data \
    && chmod -R a-w /opt/starun-skills

USER starun

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
