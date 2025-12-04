# ---- Stage 1: Build ----
FROM python:3.12-slim AS builder

# System‑Dependencies (ohne `apt-get update`‑Cache)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq-dev gcc libssl-dev ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Arbeitsverzeichnis
WORKDIR /app

# Dependencies first – cacheable layer
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONHASHSEED=0

WORKDIR /app

RUN mkdir /app/logs/

# Copy *only* the compiled site-packages from the builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy project source (ignores .dockerignore)
COPY . .

# Optional: Permissions – keep everything readable
RUN chmod -R u+rwX /app

# Collect static files and run migrations on container start
# We use an entrypoint script so that every `docker run` triggers it
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["entrypoint.sh"]

# Expose the port Django will listen on
EXPOSE 8000

# Default command (overridden by entrypoint)
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]