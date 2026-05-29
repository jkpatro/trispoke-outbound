# trispoke-outbound — production image (multi-stage, non-root, Linux).
# One image; compose services override `command` to pick app / sender /
# imap / migrate entry points.

# =========================================================================
# Stage 1 — builder. Has compilers + libpq-dev for psycopg / cffi wheels.
# =========================================================================
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer cache: install deps before copying source.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# =========================================================================
# Stage 2 — runtime. libpq5 only, plus tini + curl for healthcheck.
# =========================================================================
FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --uid 1000 --shell /bin/bash trispoke

WORKDIR /app

# Copy the prebuilt venv + source from the builder, owned by the
# non-root runtime user.
COPY --from=builder --chown=trispoke:trispoke /app/.venv /app/.venv
COPY --from=builder --chown=trispoke:trispoke /app/src   /app/src
COPY --chown=trispoke:trispoke pyproject.toml uv.lock README.md alembic.ini ./
COPY --chown=trispoke:trispoke alembic/                  ./alembic/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

USER trispoke

EXPOSE 8501

# Streamlit's built-in liveness endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# tini = PID 1 → reaps zombies and forwards SIGTERM cleanly to streamlit /
# python daemons (important for `docker compose down`).
ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["streamlit", "run", "src/trispoke/ui/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
