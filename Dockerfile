# Production image for restaurant-ops (Django + gunicorn + WhiteNoise).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    PRIVATE_UPLOAD_ROOT=/var/data/private_uploads \
    PUBLIC_UPLOAD_ROOT=/var/data/public_uploads

WORKDIR /app
RUN useradd --create-home --uid 10001 app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Static files are collected at build time; settings need placeholder values to import.
RUN DJANGO_SECRET_KEY=build-only DATABASE_URL=postgres://build@localhost/build \
    python manage.py collectstatic --noinput \
 && python -m compileall -q . \
 && chmod +x bin/docker-entrypoint.sh bin/start.sh

EXPOSE 8000
ENTRYPOINT ["bin/docker-entrypoint.sh"]
CMD ["bin/start.sh"]
