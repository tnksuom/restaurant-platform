#!/bin/sh
# Start the web server. In demo mode, first reset the database to the example restaurant.
set -e
if [ "${DEMO_MODE:-false}" = "true" ]; then
  python manage.py migrate --noinput
  python manage.py demo_reset
fi
exec gunicorn config.wsgi \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 60 --max-requests 1000 --max-requests-jitter 100 \
  --access-logfile - --forwarded-allow-ips='*'
