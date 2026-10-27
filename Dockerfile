FROM python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /dist

FROM python:3.11-slim@sha256:ae952e0a57138a4317b9b61caaa19a8217c2f356803f8d7e63aad08769950937
LABEL org.opencontainers.image.source="https://github.com/agi1clj/raidlock" \
      org.opencontainers.image.description="raidlock: lease-and-review board for coding agents" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update \
 && apt-get install -y --no-install-recommends git bubblewrap \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 raid \
 && useradd --uid 10001 --gid 10001 --home-dir /home/raid --create-home --shell /usr/sbin/nologin raid \
 && install -d -o 10001 -g 10001 -m 0700 /workspace /home/raid/.raidlock
RUN --mount=type=bind,from=builder,source=/dist,target=/dist \
    pip install --no-cache-dir --no-deps /dist/*.whl \
 && pip uninstall -y pip setuptools wheel
ENV RAIDLOCK_HOME=/home/raid/.raidlock \
    RAIDLOCK_SANDBOX=container \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["raid"]
