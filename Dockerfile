# Dockerfile for Square Schedule Manager
# Multi-stage build for efficient deployment

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY database.py .
COPY square_api.py .
COPY templates/ templates/
COPY static/ static/

# Create volumes for data persistence
VOLUME /app/data
VOLUME /app/uploads

# Expose port
EXPOSE 5000

# Set environment variables
ENV FLASK_ENV=production
ENV DB_PATH=/app/data/schedules.db

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/login')" || exit 1

# Run application
CMD ["python", "app.py"]
