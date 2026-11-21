# Single image: API + SPA (built in stage 1) + worker (same image, different command). No Kubernetes required.
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/backend/src
WORKDIR /app
RUN useradd -r -u 10001 ri
COPY backend/pyproject.toml backend/
# one source of truth for dependencies: pyproject.toml (a hand-copied list here caused a missing-package crash once)
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('backend/pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/ backend/
COPY reference/ reference/
COPY --from=web /web/dist frontend/dist
USER ri
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["sh", "-c", "python -m ri.cli migrate && python -m ri.cli seed && python -m ri.cli serve"]
