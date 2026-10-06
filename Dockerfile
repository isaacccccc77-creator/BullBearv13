# Tickveil — portable container.
#
# Builds the same image whether it runs on Fly.io, Railway, Render or
# Cloud Run, so the host is a decision you can change later without
# touching the app. Nothing here is host-specific except $PORT, which
# every one of them supplies.

FROM python:3.11-slim AS base

# Fail fast and log straight through, rather than buffering output that
# then vanishes when a container is killed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, in their own layer: requirements.txt changes far
# less often than the app does, so a code edit reuses the cached install.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. If the app is ever tricked into writing where
# it should not, the blast radius is this user's permissions rather than
# the container's root.
RUN useradd --create-home --uid 10001 tickveil \
    && chown -R tickveil:tickveil /app
USER tickveil

ENV PORT=8080
EXPOSE 8080

# Streamlit serves its own liveness endpoint; the platform health check
# should point here rather than at /, which costs a full page render.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
        sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8080)}/_stcore/health', timeout=4).status==200 else 1)"

# Shell form so $PORT expands. enableCORS stays off and XSRF protection
# stays ON: turning XSRF off is the usual cargo-culted fix for a proxy
# problem and it removes a real protection.
CMD streamlit run app_v30.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=true \
    --server.fileWatcherType=none \
    --browser.gatherUsageStats=false
