FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user for security
RUN groupadd -r botuser && useradd -r -g botuser -d /app -s /sbin/nologin botuser \
    && mkdir -p /data/db /data/logs \
    && chown -R botuser:botuser /app /data

COPY --chown=botuser:botuser . .

USER botuser

CMD ["python", "runner.py"]
