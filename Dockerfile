FROM python:3.13-slim

# Install dependencies
RUN apt-get update && apt-get install -y \
    cron \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install requests

# Set up directories
RUN mkdir -p /app/web /app/data /app/state /app/scripts

# Copy Python scripts
COPY scripts/plex_data_fetcher.py /app/scripts/
COPY scripts/jellyfin_data_fetcher.py /app/scripts/
COPY scripts/prepare_web.py /app/scripts/
COPY scripts/prepare_entrypoint.py /app/scripts/
COPY scripts/sync_runner.py /app/scripts/
COPY scripts/status.py /app/scripts/
RUN chmod +x /app/scripts/plex_data_fetcher.py
RUN chmod +x /app/scripts/jellyfin_data_fetcher.py
RUN chmod +x /app/scripts/prepare_web.py
RUN chmod +x /app/scripts/prepare_entrypoint.py
RUN chmod +x /app/scripts/sync_runner.py
RUN chmod +x /app/scripts/status.py

# Copy web files and inject the bounded large-library renderer before shipping.
# Keeping this as a build step lets us retain the upstream UI while replacing
# only its expensive all-at-once rendering path.
COPY web/ /app/web/
RUN python /app/scripts/prepare_web.py /app/web/index.html

# Remove default Nginx configuration and add our custom one
RUN rm -f /etc/nginx/sites-enabled/default
COPY config/nginx.conf /etc/nginx/conf.d/default.conf
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY config/entrypoint.sh /app/
RUN python /app/scripts/prepare_entrypoint.py /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
RUN nginx -t

# Create empty crontab file
RUN touch /etc/cron.d/media-cron
RUN chmod 0644 /etc/cron.d/media-cron

# Create data/state directory structure for all three servers
RUN mkdir -p \
    /app/data/plex /app/data/jellyfin /app/data/emby \
    /app/state/plex /app/state/jellyfin /app/state/emby

WORKDIR /app

# Expose port for the internal Nginx server. Reverse proxies such as Traefik
# should route to this port directly; TLS terminates at the reverse proxy.
EXPOSE 80

# Health checks deliberately test only the internal web server. During the first
# large-library sync the container can remain unready until Nginx starts, after
# which Docker/Traefik will automatically recover it to healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/healthz', timeout=3).read()" || exit 1

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
