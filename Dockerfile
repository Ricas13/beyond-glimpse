FROM python:3.13-slim

# Keep the image lean: install only required runtime packages and do not retain
# apt/pip package caches.
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN mkdir -p /app/web /app/data /app/state /app/scripts /var/cache/nginx/posters \
    && chown -R www-data:www-data /var/cache/nginx

COPY VERSION /app/VERSION
COPY scripts/plex_data_fetcher.py /app/scripts/
COPY scripts/jellyfin_data_fetcher.py /app/scripts/
COPY scripts/ultralight_jellyfin.py /app/scripts/
COPY scripts/catalogue_core.py /app/scripts/
COPY scripts/catalogue_sync.py /app/scripts/
COPY scripts/catalogue_service.py /app/scripts/
COPY scripts/catalogue_scheduler.py /app/scripts/
COPY scripts/configure_poster_proxy.py /app/scripts/
COPY scripts/prepare_web.py /app/scripts/
COPY scripts/prepare_entrypoint.py /app/scripts/
COPY scripts/sync_runner.py /app/scripts/
COPY scripts/status.py /app/scripts/
COPY scripts/initial_sync.py /app/scripts/
COPY scripts/smoke_test.py /app/scripts/
RUN chmod +x /app/scripts/*.py

COPY web/ /app/web/
RUN python /app/scripts/prepare_web.py /app/web/index.html

RUN rm -f /etc/nginx/sites-enabled/default
COPY config/nginx.conf /etc/nginx/conf.d/default.conf
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Build-time fallback keeps nginx -t valid without secrets. The production
# include points at the localhost catalogue service and contains no Jellyfin
# credentials; runtime still disables it when Jellyfin is unconfigured.
RUN printf '%s\n' 'location /poster/ { return 404; }' > /etc/nginx/poster-proxy.inc \
    && nginx -t \
    && JELLYFIN_URL='http://jellyfin:8096' JELLYFIN_TOKEN='abcdef123456' \
       POSTER_PROXY_MAX_WIDTH='320' POSTER_PROXY_QUALITY='72' \
       python /app/scripts/configure_poster_proxy.py \
    && nginx -t \
    && printf '%s\n' 'location /poster/ { return 404; }' > /etc/nginx/poster-proxy.inc

COPY config/entrypoint.sh /app/
RUN python /app/scripts/prepare_entrypoint.py /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
RUN nginx -t

RUN touch /etc/cron.d/media-cron && chmod 0644 /etc/cron.d/media-cron

RUN mkdir -p \
    /app/data/plex /app/data/jellyfin /app/data/emby \
    /app/state/plex /app/state/jellyfin /app/state/emby

WORKDIR /app

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/healthz', timeout=3).read()" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
