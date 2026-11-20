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
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "psycopg[binary,pool]>=3.2" "httpx>=0.27" "pydantic>=2.7" "pyyaml>=6" "rapidfuzz>=3.9" "pyjwt[crypto]>=2.8"
COPY backend/ backend/
COPY reference/ reference/
COPY --from=web /web/dist frontend/dist
USER ri
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["sh", "-c", "python -m ri.cli migrate && python -m ri.cli seed && python -m ri.cli serve"]
