# Image for the always-on Dagster webserver + daemon that orchestrate the
# Da Nang real estate pipeline. Includes uv (to run the project + dbt) and the
# docker CLI (the job bounces the Metabase container via the mounted docker.sock).
FROM python:3.12-slim

# docker CLI (client only — talks to the host daemon via the mounted socket).
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv for dependency management / running the project + dbt.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (cached) using just the metadata, then the source is
# bind-mounted at runtime by docker-compose so code edits don't need a rebuild.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --extra dagster --frozen

ENV DAGSTER_HOME=/opt/dagster_home
RUN mkdir -p /opt/dagster_home

EXPOSE 3000
