# Dockerfile for Square Schedule Manager
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first for better layer caching. No build
# toolchain is needed — every dependency ships wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application files
COPY app.py wsgi.py database.py square_api.py ollama_client.py security.py timezones.py scheduler.py ./
COPY templates/ templates/
COPY static/ static/

# Data + uploads live on volumes so they survive container recreation.
RUN mkdir -p /app/data /app/uploads /app/data/backups
VOLUME /app/data
VOLUME /app/uploads

EXPOSE 5000

ENV FLASK_ENV=production \
    DB_PATH=/app/data/schedules.db \
    BACKUP_DIR=/app/data/backups \
    GUNICORN_WORKERS=2

# Run as a non-root user. Own the app + data dirs so SQLite can write.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Healthcheck hits the unauthenticated liveness endpoint using stdlib only
# (no curl in the slim image) and treats a non-200 as unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/healthz', timeout=5).status==200 else 1)" || exit 1

# Production WSGI server (not the Flask dev server). wsgi:application bootstraps
# an initial admin on first start.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:5000 --workers ${GUNICORN_WORKERS:-2} --timeout 120 --access-logfile - wsgi:application"]
