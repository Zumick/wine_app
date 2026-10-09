# WineApp / ScoreTaste — production image for Coolify (Hostinger VPS).
# Flask + gunicorn; SQLite and uploaded event media live on a persistent volume (/data).

FROM node:22-bookworm-slim AS guide
WORKDIR /build/scoretaste
COPY scoretaste/package.json scoretaste/package-lock.json ./
RUN npm ci
COPY scoretaste/ ./
# Build-time options of the guide (set as Build Variables in Coolify if needed).
ARG VITE_PUBLIC_GUIDE_ORIGIN
ARG VITE_PILOT_MONITOR
ENV VITE_PUBLIC_GUIDE_ORIGIN=$VITE_PUBLIC_GUIDE_ORIGIN \
    VITE_PILOT_MONITOR=$VITE_PILOT_MONITOR
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_DIR=/data \
    GUIDE_MEDIA_DIR=/data/guide-media \
    PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -rf scoretaste/node_modules scoretaste/dist
COPY --from=guide /build/scoretaste/dist scoretaste/dist
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/guide-media \
    && chown -R app:app /app /data
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:%s/' % os.environ.get('PORT','8000'), timeout=4); sys.exit(0)"
# SQLite: one worker process, concurrency via threads.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:${PORT} --forwarded-allow-ips='*' app:app"]
