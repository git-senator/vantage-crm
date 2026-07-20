# Multi-stage build.
#
# Portability note: this image takes ALL configuration from environment
# variables and writes nothing outside /tmp. That is what lets the same image
# run under Compose today and ECS or Kubernetes later without modification.

# ------------------------------------------------------------------ builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

# Install into an isolated prefix that the runtime stage copies wholesale, so
# compilers never reach the final image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel \
    && pip install "psycopg[binary]>=3.2" \
    && pip install .

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq for asyncpg/psycopg, curl for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root. A container escape should not land on uid 0.
RUN groupadd --system --gid 1001 vantage \
    && useradd --system --uid 1001 --gid vantage --create-home vantage

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv/app
COPY --chown=vantage:vantage app ./app
COPY --chown=vantage:vantage alembic ./alembic
COPY --chown=vantage:vantage alembic.ini ./

USER vantage

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# --no-server-header: do not advertise the stack to scanners. uvicorn appends
# this header after middleware runs, so it can only be suppressed here.
# --proxy-headers: trust X-Forwarded-* from the Next.js BFF in front of us.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--proxy-headers", "--forwarded-allow-ips", "*"]
