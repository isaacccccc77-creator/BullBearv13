# The website: React front end built by Node, served by the Python API.
# The Streamlit app keeps its own Dockerfile; this one is separate so
# either can be deployed without touching the other.
#
#   docker build -f web.Dockerfile -t tickveil-web .
#   docker run -p 8080:8080 -e ANTHROPIC_API_KEY=... tickveil-web

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY scoring.py verdict.py quant.py deals.py forecast.py ./
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist

RUN useradd --uid 10001 --create-home tickveil
USER tickveil

ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"

# One worker on purpose: the debate cache and the spending limits live in
# this process's memory, and a second worker would double every limit.
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1
