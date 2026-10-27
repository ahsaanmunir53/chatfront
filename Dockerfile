# One image for Render's free web service: the API, the worker and the built web app.

FROM node:22-slim AS web
WORKDIR /app
COPY i18n ./i18n
# The web app bundles the demo shops straight from the business templates.
COPY api/src/chatfront/templates ./api/src/chatfront/templates
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci
COPY web ./web
RUN cd web && npm run build

FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app/api
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY api ./
RUN uv sync --frozen --no-dev
COPY i18n /app/i18n
COPY --from=web /app/web/dist /app/web/dist
RUN useradd --create-home chatfront && chown -R chatfront /app
USER chatfront
ENV PATH="/app/api/.venv/bin:$PATH"
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m chatfront.seed && uvicorn chatfront.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
