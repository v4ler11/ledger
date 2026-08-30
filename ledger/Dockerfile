FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

RUN --mount=type=ssh apt-get update && apt-get install -y \
    git \
    ca-certificates \
    openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY pyproject.toml uv.lock README.md ./
COPY src/ledger ./src/ledger
COPY src/tg_bot ./src/tg_bot
COPY src/scripts ./src/scripts

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"

RUN --mount=type=ssh uv sync --frozen --no-dev
