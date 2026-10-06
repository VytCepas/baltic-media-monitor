# One image for every pipeline step (producer, monitor, Spark, AI client, report): `baltic <command>`.
# Python 3.12 + a Java runtime for Spark + exactly the locked dependencies; runs as an unprivileged user.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless \
 && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv==0.11.28 \
 && useradd --uid 1000 --create-home app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
# dependencies first, so a code change rebuilds only the last layers
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
USER app
ENTRYPOINT ["baltic"]
