# Python 3.11 slim base image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Security: Create system user and group
RUN groupadd --system appuser && \
    useradd --system --gid appuser --create-home --home-dir /home/appuser --shell /usr/sbin/nologin appuser

# Pre-create data directory with correct ownership for SQLite/local persistence
RUN mkdir -p /app/data && chown appuser:appuser /app/data

# Layer caching: Install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code with unprivileged ownership in a single layer
COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
