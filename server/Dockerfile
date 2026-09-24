# syntax=docker/dockerfile:1
#
# spec 095: snapshot.timolehtonen.tech's gallery server, on Azure Container
# Apps. Mirrors ai-timolehtonen-tech/deploy/gcp/Dockerfile's shape
# (proven portable across Cloud Run and Container Apps unchanged) -
# python:3.13-slim, non-root user, ENV PORT=8080. That last line is
# load-bearing here in a way it wasn't on Cloud Run: Container Apps
# does NOT inject a PORT env var the way Cloud Run does, so this
# default is what --target-port 8080 actually depends on.
#
# Build context is the REPO ROOT (not server/), so this can serve
# web/{index.html,app.js} directly - see .dockerignore for what's
# excluded from that context (core/'s cv2/mediapipe stack, recordings,
# venv, etc. - none of which this container needs or may import, see
# server/blob_videos.py's own docstring on why).

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN useradd --create-home --uid 1001 appuser

WORKDIR /app

# Dependencies as their own layer: a code change doesn't invalidate the pip cache.
COPY server/requirements.txt ./server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ ./server/
COPY web/ ./web/

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Shell form so ${PORT} expands; exec makes uvicorn PID 1 so Container
# Apps' SIGTERM goes straight to it. --proxy-headers/--forwarded-allow-ips
# so request.url.scheme reads https behind Container Apps' Envoy
# ingress, not http.
CMD exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"
